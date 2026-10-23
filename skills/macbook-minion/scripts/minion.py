#!/usr/bin/env python3
"""Dependency-free controller; the worker only needs macOS command-line tools."""
import argparse
import fnmatch
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile
from browser_plan import validate_plan
import audit

SCRIPTS = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path.home() / ".config/macbookminion/config.json"
REMOTE_ROOT = ".macbookminion"
EXCLUDES = (
    ".git", ".hg", ".svn", ".env", ".env.*", ".ssh", ".aws", ".azure",
    ".config", ".codex", ".claude", ".npmrc", ".pypirc", ".netrc",
    "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
    "credentials*", "secrets*", "node_modules", ".venv", "venv",
    "__pycache__", ".DS_Store", ".minion", ".macbookminion",
)


class MinionError(Exception):
    pass


def emit(value):
    print(json.dumps(value, indent=2))


def execute(argv, *, input=None, capture=True, timeout=None):
    result = subprocess.run(argv, input=input, text=True, capture_output=capture,
                            timeout=timeout, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip() if capture else ""
        raise MinionError(f"{Path(argv[0]).name} exited {result.returncode}: {detail}")
    return result.stdout if capture else ""


def host_value(value):
    # Host alias or user@IPv4/DNS name; no SSH options, shell syntax, or IPv6 in v1.
    if not re.fullmatch(r"(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*", value):
        raise MinionError("Use an SSH alias or USER@HOST (DNS/IPv4), with no spaces or options")
    return value


def identifier(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", value):
        raise MinionError("Invalid snapshot/job ID")
    return value


def validate_config(config):
    host_value(config["host"])
    if type(config["min_free_gib"]) is not int or config["min_free_gib"] < 1:
        raise MinionError("min_free_gib must be a positive integer")
    if config.get("identity"):
        key = Path(config["identity"]).expanduser().resolve()
        if not key.is_file():
            raise MinionError(f"SSH identity does not exist: {key}")
        config["identity"] = str(key)
    return config


def load_config(path):
    if not path.is_file():
        raise MinionError("Not paired. See references/pairing.md; then run configure.")
    config = validate_config(json.loads(path.read_text()))
    config["_config_path"] = str(path.expanduser().resolve())
    host_hash = hashlib.sha256(config["host"].encode()).hexdigest()[:12]
    config["_state_dir"] = str(path.expanduser().resolve().parent / ("state-" + host_hash))
    return config


def ssh_base(config):
    args = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
            "-o", "ServerAliveCountMax=3"]
    if config.get("identity"):
        args += ["-o", "IdentitiesOnly=yes", "-i", config["identity"]]
    return args


def remote(config, action, *args, timeout=45):
    payload = SCRIPTS.joinpath("remote.sh").read_text()
    payload += '\nremote_main "$HOME/.macbookminion" ' + shlex.join([action, *map(str, args)]) + "\n"
    request = "rpc-" + uuid.uuid4().hex
    job = next((str(a) for a in args if re.fullmatch(r"j-[a-z0-9-]+", str(a))), audit.CONTEXT.get().get("job"))
    with audit.context(job=job):
        audit.event(config, "controller", "worker", "rpc.send", "Request: " + action,
                    request_id=request, action=action, transport="SSH",
                    identifiers=[str(a) for a in args if re.fullmatch(r"[sj]-[a-z0-9-]+", str(a))])
        started = time.monotonic()
        try:
            result = execute(ssh_base(config) + [host_value(config["host"]), "/bin/bash -s"],
                             input=payload, timeout=timeout)
        except (MinionError, OSError, subprocess.TimeoutExpired) as error:
            audit.event(config, "controller", "sender", "rpc.error", "Request did not return success: " + action,
                        request_id=request, action=action, error_type=type(error).__name__,
                        elapsed_ms=round((time.monotonic()-started)*1000), outcome="Reconcile worker state before retrying")
            raise
        audit.event(config, "worker", "controller", "rpc.reply", "Acknowledged: " + action,
                    request_id=request, action=action, elapsed_ms=round((time.monotonic()-started)*1000),
                    response_bytes=len(result.encode()), response_sha256=hashlib.sha256(result.encode()).hexdigest())
        return result


def key_values(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def inventory(source):
    """Select regular files once; transfer exactly this list, not a broad rsync tree."""
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise MinionError("Source must be a focused directory")
    if source == Path.home() or source == Path(source.anchor):
        raise MinionError("Refusing to transfer a home directory or filesystem root")
    if any(part in (".ssh", ".aws", ".azure", ".config", ".codex", ".claude") for part in source.parts):
        raise MinionError("Refusing to transfer an authentication or application-config directory")
    files, skipped, total = [], [], 0
    for directory, dirs, names in os.walk(source, followlinks=False):
        for name in sorted(dirs + names):
            path = Path(directory) / name
            rel = path.relative_to(source).as_posix()
            if path.is_symlink() or any(fnmatch.fnmatch(name.lower(), p.lower()) for p in EXCLUDES):
                skipped.append(rel)
                if name in dirs:
                    dirs.remove(name)
                continue
            if any(c in rel for c in "\n\r\\"):
                raise MinionError(f"Unsupported filename (newline or backslash): {rel!r}")
            if path.is_dir():
                continue
            if not path.is_file():
                raise MinionError(f"Not a regular file: {rel}")
            total += path.stat().st_size
            files.append((rel, digest(path)))
    if not files:
        raise MinionError("No transferable files after exclusions")
    return source, files, skipped, total


def manifest_text(files):
    return "".join(f"{sha}  ./{name}\n" for name, sha in files)


def rsync(config, source, destination, *, file_list=None):
    args = ["rsync", "-rpt", "--chmod=u+rwX,go-rwx", "--no-links",
            "-e", shlex.join(ssh_base(config))]
    if file_list:
        args += ["--files-from=" + str(file_list)]
    # All local paths are absolute and remote paths use only validated IDs.
    request = "transfer-" + uuid.uuid4().hex
    inbound = source.startswith(config["host"] + ":")
    origin, target = ("worker", "controller") if inbound else ("controller", "worker")
    audit.event(config, origin, target, "transfer.send", "Downloading artifacts" if inbound else "Uploading job files",
                request_id=request, transport="rsync over SSH")
    try:
        execute(args + [source, destination], timeout=None)
    except (MinionError, OSError) as error:
        audit.event(config, "controller", "sender", "transfer.error", "Transfer failed; verification pending",
                    request_id=request, error_type=type(error).__name__)
        raise
    audit.event(config, target, origin, "transfer.reply", "Transfer complete; awaiting checksum verification", request_id=request)


def remote_path(config, suffix):
    return config["host"] + ":" + REMOTE_ROOT + "/" + suffix


def remote_home_path(config, suffix):
    # Outside REMOTE_ROOT on purpose: the desktop launcher must be somewhere
    # the worker's own user notices it, not hidden inside the workspace.
    return config["host"] + ":" + suffix


def upload_file(config, local, suffix):
    rsync(config, str(local.resolve()), remote_path(config, suffix))


def install_kill_switch(config):
    """Give the worker a local, network-free way to stop anything MacBook
    Minion left running on it, independent of the controller or pairing."""
    with tempfile.TemporaryDirectory(prefix="minion-killswitch-") as temp:
        temp = Path(temp)
        script = temp / "kill-switch.sh"
        script.write_text((SCRIPTS / "kill-switch.sh").read_text())
        script.chmod(0o700)
        upload_file(config, script, "kill-switch.sh")
        launcher = temp / "launcher.command"
        launcher.write_text(
            "#!/bin/bash\n"
            "printf 'MacBook Minion: stopping anything running on this Mac.\\n\\n'\n"
            '/bin/bash "$HOME/' + REMOTE_ROOT + '/kill-switch.sh"\n'
            "printf '\\nPress Return to close this window.\\n'\n"
            "read -r _\n"
        )
        launcher.chmod(0o700)
        rsync(config, str(launcher), remote_home_path(config, "Desktop/Stop-MacBook-Minion.command"))


def put(config, source):
    source, files, skipped, total = inventory(source)
    snap = "s-" + uuid.uuid4().hex
    # Allow 10% transfer overhead plus the OS reserve; no source deletions.
    required = config["min_free_gib"] * 1024**2 + math.ceil(total * 1.1 / 1024)
    remote(config, "prepare_snapshot", snap, required)
    with tempfile.TemporaryDirectory(prefix="minion-transfer-") as temp:
        temp = Path(temp)
        listing = temp / "files.txt"
        listing.write_text("".join(name + "\n" for name, _ in files))
        manifest = temp / "manifest.sha256"
        manifest.write_text(manifest_text(files))
        try:
            rsync(config, str(source) + "/", remote_path(config, f"snapshots/{snap}/"), file_list=listing)
            upload_file(config, manifest, f"snapshots/{snap}.sha256")
            remote(config, "verify_snapshot", snap, timeout=None)
        except (MinionError, subprocess.TimeoutExpired) as error:
            raise MinionError(f"Snapshot {snap} was not verified; retained for inspection. {error}") from error
    return {"snapshot": snap, "files": len(files), "bytes": total,
            "skipped": skipped, "sha256_verified": True}


def run_job(config, snapshot, script, reserve_output_gib, owner="manual"):
    identifier(snapshot)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", owner):
        raise MinionError("Owner must be a short project/session ID without spaces")
    script = script.expanduser().resolve()
    if not script.is_file():
        raise MinionError("Job script does not exist")
    if reserve_output_gib < 1:
        raise MinionError("Output reserve must be at least 1 GiB")
    execute(["/bin/bash", "-n", str(script)])
    job = "j-" + uuid.uuid4().hex
    required = (config["min_free_gib"] + reserve_output_gib) * 1024**2
    remote(config, "prepare_job", job, snapshot, required, owner)
    try:
        upload_file(config, script, f"jobs/{job}/command.sh")
        upload_file(config, SCRIPTS / "runner.sh", f"jobs/{job}/runner.sh")
        return key_values(remote(config, "launch", job))
    except (MinionError, subprocess.TimeoutExpired) as error:
        raise MinionError(f"Job {job} may be staged or running. Inspect status/list before retrying. {error}") from error


def verify_download(destination, manifest):
    expected = set()
    for line in manifest.splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  \./(.+)", line)
        if not match:
            raise MinionError("Unsupported or malformed remote manifest")
        sha, name = match.groups()
        rel = Path(name)
        if rel.is_absolute() or ".." in rel.parts or "\\" in name or "\r" in name:
            raise MinionError("Unsafe artifact path in manifest")
        target = destination / rel
        if not target.is_file() or target.is_symlink() or digest(target) != sha:
            raise MinionError(f"Missing or changed artifact: {name}")
        expected.add(name)
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
    if actual != expected:
        raise MinionError("Downloaded file list differs from the manifest")
    return len(expected)


def fetch(config, kind, item_id, destination):
    identifier(item_id)
    destination = destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise MinionError("Destination must be new; existing files are never overwritten")
    manifest = remote(config, "manifest", kind, item_id, timeout=None)
    destination.mkdir(parents=True, mode=0o700)
    try:
        rsync(config, remote_path(config, f"{kind}/{item_id}/"), str(destination) + "/")
        count = verify_download(destination, manifest)
    except (MinionError, subprocess.TimeoutExpired) as error:
        raise MinionError(f"Unverified download retained at {destination}: {error}") from error
    with audit.context(job=item_id if kind == "jobs" else None):
        audit.event(config, "controller", "sender", "artifacts.verified", "Retrieved artifacts passed SHA-256 verification", files=count)
    return {"id": item_id, "destination": str(destination), "files": count, "sha256_verified": True}


def browser_job(config, owner, *, plan=None, setup=False, selftest=False, title=None, description=None):
    """Stage an adapter job through exactly the same lock/transfer/runner as shell jobs."""
    info = key_values(remote(config, "browser_probe"))
    if not info.get("python3") or not info.get("chrome_version"):
        raise MinionError("Browser jobs require Python 3 and Google Chrome on the worker")
    if info.get("active_job") and not config.get("_state_dir"):
        raise MinionError("Worker busy: " + info["active_job"] + "; inspect list for its owner")
    if not setup and info.get("driver_installed") != "yes":
        raise MinionError("Run browser-setup once before browser-run")
    with tempfile.TemporaryDirectory(prefix="minion-browser-") as temp:
        temp = Path(temp)
        bundle = temp / "bundle"
        bundle.mkdir()
        for name in ("browser_worker.py", "browser_plan.py"):
            shutil.copyfile(SCRIPTS / name, bundle / name)
        if setup:
            if info.get("arch") != "x86_64":
                raise MinionError("This browser installer currently supports Intel macOS only")
            build = ".".join(info["chrome_version"].split(".")[:3])
            catalog_url = "https://googlechromelabs.github.io/chrome-for-testing/latest-patch-versions-per-build-with-downloads.json"
            with urllib.request.urlopen(catalog_url, timeout=30) as response:
                catalog = json.load(response)
            entry = catalog["builds"].get(build)
            if not entry:
                raise MinionError("No official ChromeDriver matches build " + build)
            version = entry["version"]
            if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
                raise MinionError("Invalid driver catalog version")
            url = next((d["url"] for d in entry["downloads"]["chromedriver"] if d["platform"] == "mac-x64"), None)
            expected = f"https://storage.googleapis.com/chrome-for-testing-public/{version}/mac-x64/chromedriver-mac-x64.zip"
            if url != expected:
                raise MinionError("Unexpected ChromeDriver download origin/path")
            archive = temp / "driver.zip"
            with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as target:
                if response.url != expected:
                    raise MinionError("Unexpected ChromeDriver download redirect")
                shutil.copyfileobj(response, target)
            with zipfile.ZipFile(archive) as zipped:
                # Extract only this exact file; never trust arbitrary ZIP paths.
                bundle.joinpath("chromedriver").write_bytes(zipped.read("chromedriver-mac-x64/chromedriver"))
            bundle.joinpath("driver.json").write_text(json.dumps({
                "version": version, "source_url": url, "archive_sha256": digest(archive),
                "binary_sha256": digest(bundle / "chromedriver")}, indent=2))
            mode = "install"
        elif selftest:
            mode = "selftest"
        else:
            bundle.joinpath("plan.json").write_text(json.dumps(validate_plan(plan), indent=2))
            mode = "run"
        script = temp / "browser-job.sh"
        script.write_text("#!/bin/bash\nset -euo pipefail\nexec python3 browser_worker.py " + mode + "\n")
        snapshot = put(config, bundle)["snapshot"]
        if config.get("_state_dir"):
            from control import submit
            kind = "browser-setup" if setup else "browser-selftest" if selftest else "browser"
            default_title = "Prepare Chrome automation" if setup else "Verify browser readiness" if selftest else "Browser task"
            default_description = ("Install the official matching ChromeDriver in the worker workspace." if setup else
                "Open a generated test page, type, click, verify the result and capture screenshots." if selftest else
                "Run " + str(len(plan["steps"])) + " reviewed browser steps in a fresh profile.")
            result = submit(config, snapshot, script, 1, owner, title=title or default_title,
                            description=description or default_description, kind=kind)
        else:
            result = run_job(config, snapshot, script, 1, owner)
        result.update({"snapshot": snapshot, "kind": "browser-setup" if setup else "browser-selftest" if selftest else "browser",
                       "verified_complete": False})
        return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("doctor", help="Check local prerequisites and pairing metadata")
    for command in ("probe", "configure"):
        q = sub.add_parser(command, help="Read-only worker inventory" if command == "probe" else "Verify SSH and save local pairing")
        q.add_argument("--host", required=command == "configure")
        q.add_argument("--identity", type=Path)
        q.add_argument("--min-free-gib", type=int, default=25)
    sub.add_parser("init", help="Create the dedicated worker workspace")
    for command in ("plan", "put"):
        q = sub.add_parser(command)
        q.add_argument("source", type=Path)
    q = sub.add_parser("run")
    q.add_argument("snapshot")
    q.add_argument("--script", required=True, type=Path)
    q.add_argument("--reserve-output-gib", type=int, default=5)
    q.add_argument("--owner", default=os.environ.get("CODEX_THREAD_ID", "manual"),
                   help="Owning project/session ID, shown in status and list")
    q.add_argument("--title", help="Short readable job name")
    q.add_argument("--description", help="Purpose, expected output and context; no credentials")
    for command in ("status", "logs"):
        q = sub.add_parser(command)
        q.add_argument("job")
    sub.add_parser("list")
    sub.add_parser("browser-probe", help="Read browser/driver readiness without opening a browser")
    q = sub.add_parser("browser-setup", help="Install the matching official ChromeDriver in the worker workspace")
    q.add_argument("--owner", default=os.environ.get("CODEX_THREAD_ID", "manual"))
    q.add_argument("--title")
    q.add_argument("--description")
    q = sub.add_parser("browser-selftest", help="Verify browser interactions against a disposable loopback test page")
    q.add_argument("--owner", default=os.environ.get("CODEX_THREAD_ID", "manual"))
    q.add_argument("--title")
    q.add_argument("--description")
    q = sub.add_parser("browser-run", help="Submit a bounded headless browser plan")
    q.add_argument("plan", type=Path)
    q.add_argument("--owner", default=os.environ.get("CODEX_THREAD_ID", "manual"))
    q.add_argument("--title")
    q.add_argument("--description")
    sub.add_parser("queue", help="Show persistent jobs, descriptions and current states as JSON")
    sub.add_parser("dashboard", help="Start/reuse the local queue service and print its private dashboard URL")
    sub.add_parser("sync", help="Reconcile observed worker history without dispatching jobs")
    q = sub.add_parser("serve", help="Run the queue coordinator and read-only dashboard in the foreground")
    q.add_argument("--port", type=int, default=8765)
    sub.add_parser("controller-stop", help="Stop local dispatch; remote jobs continue and the queue is saved")
    for command in ("cancel", "retry-dispatch"):
        q = sub.add_parser(command)
        q.add_argument("job")
    q = sub.add_parser("audit", help="Inspect, verify, or export the append-only event ledger")
    q.add_argument("--job")
    q.add_argument("--before", type=int)
    group = q.add_mutually_exclusive_group()
    group.add_argument("--verify", action="store_true")
    group.add_argument("--export", type=Path)
    for command, label in (("fetch", "job"), ("get", "snapshot")):
        q = sub.add_parser(command)
        q.add_argument(label)
        q.add_argument("destination", type=Path)
    return p


def main(argv=None):
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        if args.action == "doctor":
            emit({"python": sys.version.split()[0], "tools": {t: shutil.which(t) for t in ("ssh", "rsync", "ssh-keygen", "ssh-copy-id")},
                  "config": str(args.config), "configured": args.config.is_file(), "live_connection_tested": False})
            return 0
        if args.action == "plan":
            source, files, skipped, total = inventory(args.source)
            emit({"source": str(source), "files": [n for n, _ in files], "bytes": total, "skipped": skipped})
            return 0
        if args.action in ("probe", "configure") and args.host:
            config = validate_config({"host": args.host, "identity": str(args.identity) if args.identity else None,
                                      "min_free_gib": args.min_free_gib})
        else:
            config = load_config(args.config)
        if args.action in ("probe", "configure"):
            info = key_values(remote(config, "probe"))
            if args.action == "configure":
                if info.get("os") != "Darwin":
                    raise MinionError("Expected a macOS worker; configuration not saved")
                args.config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # Exclusive creation avoids silently replacing an established pairing.
                with args.config.open("x") as output:
                    os.chmod(args.config, 0o600)
                    json.dump(config, output, indent=2)
                info["config_saved"] = str(args.config)
            emit(info)
        elif args.action == "init":
            result = key_values(remote(config, "init"))
            install_kill_switch(config)
            result["kill_switch"] = "installed at ~/.macbookminion/kill-switch.sh and ~/Desktop/Stop-MacBook-Minion.command"
            emit(result)
        elif args.action == "put":
            emit(put(config, args.source))
        elif args.action == "run":
            from control import submit
            emit(submit(config, args.snapshot, args.script, args.reserve_output_gib, args.owner,
                        title=args.title, description=args.description))
        elif args.action in ("status", "logs"):
            row = audit.store_for(config).job(identifier(args.job))
            if row and row["state"] in ("queued", "cancelled"):
                emit(row) if args.action == "status" else print("Job has not started on the worker.")
                return 0
            text = remote(config, args.action, identifier(args.job))
            emit(key_values(text)) if args.action == "status" else print(text, end="")
        elif args.action == "list":
            print(remote(config, "list"), end="")
        elif args.action == "browser-probe":
            emit(key_values(remote(config, "browser_probe")))
        elif args.action == "browser-setup":
            emit(browser_job(config, args.owner, setup=True, title=args.title, description=args.description))
        elif args.action == "browser-selftest":
            emit(browser_job(config, args.owner, selftest=True, title=args.title, description=args.description))
        elif args.action == "browser-run":
            plan = validate_plan(json.loads(args.plan.read_text()))
            emit(browser_job(config, args.owner, plan=plan, title=args.title, description=args.description))
        elif args.action in ("queue", "dashboard", "serve", "sync", "cancel", "retry-dispatch", "controller-stop", "audit"):
            from control import Coordinator, ensure_service, serve, stop_service
            store = audit.store_for(config)
            if args.action == "queue":
                emit({"jobs": store.jobs(), "worker": store.state("worker")})
            elif args.action == "dashboard":
                emit(ensure_service(config))
            elif args.action == "serve":
                serve(config, args.port)
            elif args.action == "controller-stop":
                emit(stop_service(config))
            elif args.action == "sync":
                Coordinator(config).sync()
                emit({"worker": store.state("worker"), "jobs": len(store.jobs())})
            elif args.action == "cancel":
                store.cancel(identifier(args.job))
                emit({"job": args.job, "state": "cancelled"})
            elif args.action == "retry-dispatch":
                _, jobs = Coordinator(config).sync()
                job = identifier(args.job)
                row = store.job(job)
                if not row or not row["managed"] or row["state"] not in ("attention", "uncertain") or jobs.get(job, {}).get("launched") == "yes":
                    raise MinionError("Only an unlaunched managed job awaiting review can resume dispatch")
                store.state("attempts:" + job, 0)
                store.transition(job, "uncertain")
                store.event("sender", "controller", "dispatch.resume", "Sender requested reconciliation of the same unlaunched job", job=job)
                emit({"job": job, "state": "uncertain", "dashboard_url": ensure_service(config)["url"]})
            elif args.verify:
                emit(store.verify())
            elif args.export:
                with args.export.expanduser().open("x", encoding="utf-8") as output:
                    result = store.verify(export=output)
                emit({**result, "exported": str(args.export)})
            else:
                emit(store.events(identifier(args.job) if args.job else None, before=args.before))
        elif args.action == "fetch":
            emit(fetch(config, "jobs", args.job, args.destination))
        elif args.action == "get":
            emit(fetch(config, "snapshots", args.snapshot, args.destination))
        return 0
    except (MinionError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f"minion: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
