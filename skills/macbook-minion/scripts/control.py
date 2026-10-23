"""Durable FIFO coordinator. Remote lock and launch claim remain authoritative."""
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

import audit

TERMINAL = {"succeeded", "failed", "cancelled"}


def text_field(value, name, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError(name + " must be nonempty text of at most " + str(limit) + " characters")
    return value.strip()


def submit(config, snapshot, script, reserve, owner, *, title=None, description=None, kind="shell", transport=None, start=True):
    import minion
    transport = transport or minion
    transport.identifier(snapshot)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", owner):
        raise ValueError("Owner must be a project/session identifier")
    if type(reserve) is not int or reserve < 1:
        raise ValueError("Output reserve must be at least 1 GiB")
    script = script.expanduser().resolve()
    if not script.is_file():
        raise ValueError("Job script does not exist")
    transport.execute(["/bin/bash", "-n", str(script)])
    title = text_field(title or script.name, "Title", 160)
    description = text_field(description or ("Run " + script.name + " against the verified input snapshot."), "Description", 2000)
    store = audit.store_for(config)
    if not store:
        raise ValueError("Queue requires a saved controller configuration")
    job = "j-" + uuid.uuid4().hex
    spool = store.root / "spool" / job
    spool.mkdir(parents=True, mode=0o700)
    shutil.copyfile(script, spool / "command.sh")
    shutil.copyfile(transport.SCRIPTS / "runner.sh", spool / "runner.sh")
    for file in spool.iterdir():
        file.chmod(0o600)
    metadata = {"id": job, "title": title, "description": description, "owner": owner,
                "kind": kind, "snapshot": snapshot, "reserve_output_gib": reserve,
                "created_at": audit.now(), "script_sha256": transport.digest(spool / "command.sh"),
                "runner_sha256": transport.digest(spool / "runner.sh")}
    (spool / "metadata.json").write_text(audit.canonical(metadata))
    (spool / "metadata.json").chmod(0o600)
    store.enqueue(metadata)
    result = {"job": job, "state": "queued", "title": title, "description": description, "owner": owner}
    if start:
        try:
            result["dashboard_url"] = ensure_service(config)["url"]
        except (OSError, ValueError, TimeoutError) as error:
            result["controller_notice"] = "Job saved; start minion dashboard to dispatch. " + str(error)
    return result


def remote_state(info):
    if info.get("lock_retained") == "yes" or info.get("state") == "interrupted":
        return "attention"
    if info.get("state") == "finished":
        return "succeeded" if info.get("exit_code") == "0" else "failed"
    if info.get("state") == "running":
        return "running"
    if info.get("launched") == "yes":
        return "attention"  # Launch recorded but no live process or exit status confirmed.
    return "dispatching"


class Coordinator:
    def __init__(self, config, transport=None):
        import minion
        self.config = config
        self.transport = transport or minion
        self.store = audit.store_for(config)

    def sync(self):
        raw = self.transport.remote(self.config, "overview", timeout=20)
        blocks = [self.transport.key_values(p) for p in raw.strip().split("\n\n") if p.strip()]
        head = blocks[0] if blocks else {}
        if "active_job" not in head:
            raise ValueError("Worker overview did not contain ownership state")
        jobs = {}
        for info in blocks[1:]:
            job = self.transport.identifier(info["job"])
            jobs[job] = info
            saved = self.store.job(job)
            if not saved:
                metadata = {"id": job, "title": "Earlier worker job " + job[:10],
                            "description": "Predates this controller ledger. Original request messages were not recorded.",
                            "owner": info.get("owner", "unknown"), "kind": "legacy", "snapshot": info.get("snapshot", ""),
                            "created_at": info.get("created_at") or info.get("started_at") or audit.now()}
                if info.get("metadata_b64"):
                    candidate = json.loads(base64.b64decode(info["metadata_b64"], validate=True))
                    if candidate.get("id") != job:
                        raise ValueError("Worker job metadata ID mismatch")
                    metadata.update(candidate)
                self.store.import_job(metadata, info, remote_state(info))
                saved = self.store.job(job)
            observed = remote_state(info)
            if saved["state"] == "attention" and observed == "dispatching" and (self.store.state("attempts:" + job) or 0) >= 3:
                observed = "attention"
            error = saved.get("error") if observed == "attention" else None
            if observed == "attention" and info.get("launched") == "yes" and info.get("state") == "staged_or_interrupted":
                error = "Launch was recorded, but the worker process is unconfirmed. Inspect this job; it will not be launched again."
            self.store.transition(job, observed, remote=info, error=error)
            if info.get("worker_events_b64"):
                lines = base64.b64decode(info["worker_events_b64"], validate=True).decode("utf-8").splitlines()
                for index, line in enumerate(lines):
                    parts = line.split("\t", 2)
                    if len(parts) != 3:
                        continue  # An append may be in progress; next poll reads it again.
                    at, kind, value = parts
                    summary = {"accepted": "Worker reserved this job", "launched": "Worker accepted the launch",
                               "started": "Job started on the MacBook", "finished": "Job finished with exit code " + value,
                               "lock_retained": "Worker retained ownership for cleanup review"}.get(kind)
                    if kind == "note":
                        summary = base64.b64decode(value, validate=True).decode("utf-8")[:8000]
                    if summary:
                        self.store.event("worker", "controller", "worker." + kind, summary, job=job,
                            owner=saved["metadata"].get("owner"), observed_at=at,
                            event_key=job + ":worker:" + str(index), details={"line": index + 1})
        previous = self.store.state("worker") or {}
        state = {"online": True, "checked_at": audit.now(), "last_success_at": audit.now(),
                 "worker_at": head.get("worker_at"), "active_job": head["active_job"], "host": self.config["host"]}
        self.store.state("worker", state)
        if previous.get("online") is not True:
            self.store.event("worker", "controller", "worker.online", "MacBook connection verified")
        return head, jobs

    def dispatch(self, row, info=None):
        job, metadata = row["id"], row["metadata"]
        spool = self.store.root / "spool" / job
        t = self.transport
        with audit.context(job=job, owner=metadata["owner"]):
            for name, key in (("command.sh", "script_sha256"), ("runner.sh", "runner_sha256")):
                if not (spool / name).is_file() or (spool / name).is_symlink() or t.digest(spool / name) != metadata[key]:
                    self.store.transition(job, "attention", error="Queued payload changed or is missing; no dispatch attempted")
                    return
            if info is None:
                t.remote(self.config, "verify_snapshot", metadata["snapshot"], timeout=30)
                required = (self.config["min_free_gib"] + metadata["reserve_output_gib"]) * 1024**2
                t.remote(self.config, "prepare_job", job, metadata["snapshot"], required, metadata["owner"])
            elif info.get("launched") == "yes":
                # An acknowledged or lost-reply launch is NEVER sent a second time.
                return
            (spool / "metadata.json").write_text(audit.canonical(metadata))
            names = ("command.sh", "runner.sh", "metadata.json")
            for name in names:
                t.upload_file(self.config, spool / name, f"jobs/{job}/{name}")
            manifest = spool / "payload.sha256"
            manifest.write_text(t.manifest_text([(name, t.digest(spool / name)) for name in names]))
            t.upload_file(self.config, manifest, f"jobs/{job}/payload.sha256")
            t.remote(self.config, "verify_job", job)
            t.remote(self.config, "launch", job)
            self.store.transition(job, "dispatching")

    def tick(self):
        try:
            head, remote_jobs = self.sync()
        except Exception as error:
            old = self.store.state("worker") or {}
            self.store.state("worker", {**old, "online": False, "checked_at": audit.now(),
                                       "error": type(error).__name__, "host": self.config["host"]})
            if old.get("online") is not False:
                self.store.event("controller", "sender", "worker.unreachable", "Cannot confirm worker state; dispatch is paused",
                                 details={"error_type": type(error).__name__})
            return False
        active = head["active_job"]
        row = None
        if active:
            candidate = self.store.job(active)
            if (candidate and candidate["managed"] and candidate["state"] == "dispatching"
                    and remote_jobs.get(active, {}).get("launched") != "yes"):
                row = candidate
            else:
                return True
        else:
            for candidate in self.store.jobs():
                if candidate["managed"] and candidate["state"] in ("dispatching", "uncertain") and candidate["id"] not in remote_jobs:
                    row = candidate
                    break
            row = row or self.store.claim()
        if row:
            try:
                self.dispatch(row, remote_jobs.get(row["id"]))
            except Exception as error:
                key = "attempts:" + row["id"]
                attempts = (self.store.state(key) or 0) + 1
                self.store.state(key, attempts)
                self.store.transition(row["id"], "attention" if attempts >= 3 else "uncertain",
                    error="Dispatch reply not confirmed. Reconcile the same job ID; do not submit a duplicate. " + type(error).__name__)
        return True


def service_info(config):
    store = audit.store_for(config)
    file = store.root / "service.json"
    try:
        info = json.loads(file.read_text())
        url = info["url"]
        if not re.fullmatch(r"http://127\.0\.0\.1:\d+/[A-Za-z0-9_-]+/", url):
            return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "api/health", timeout=1) as response:
            health = json.load(response)
        return info if health.get("instance") == info["instance"] else None
    except (OSError, ValueError, KeyError):
        return None


def ensure_service(config):
    existing = service_info(config)
    if existing:
        return existing
    store = audit.store_for(config)
    log = store.root / "controller.log"
    with log.open("ab") as output:
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("minion.py")),
                          "--config", config["_config_path"], "serve"],
                         stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                         start_new_session=True, close_fds=True)
    log.chmod(0o600)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        info = service_info(config)
        if info:
            return info
        time.sleep(0.1)
    raise TimeoutError("Controller did not become ready; inspect its private controller.log")


def stop_service(config):
    info = service_info(config)
    if not info:
        return {"state": "not_running", "remote_jobs": "unchanged"}
    audit.store_for(config).event("sender", "controller", "controller.stop_requested", "Sender requested dispatch to stop; remote jobs continue")
    os.kill(info["pid"], signal.SIGTERM)
    return {"state": "stop_requested", "instance": info["instance"], "remote_jobs": "continue", "queue": "saved"}


@audit.context()
def serve(config, port=8765):
    from dashboard import make_server
    store = audit.store_for(config)
    with (store.root / "service.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        token = store.state("dashboard_token")
        if not token:
            token = secrets.token_urlsafe(32)
            store.state("dashboard_token", token)
        instance = uuid.uuid4().hex
        coordinator = Coordinator(config)
        server = make_server(config, token, instance, port)
        import threading
        stopping = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
        signal.signal(signal.SIGINT, lambda *_: stopping.set())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        info = {"pid": os.getpid(), "instance": instance, "started_at": audit.now(),
                "url": "http://127.0.0.1:" + str(server.server_port) + "/" + token + "/"}
        runtime = store.root / "service.json"
        temp = store.root / ("service-" + instance + ".json")
        temp.write_text(audit.canonical(info))
        temp.chmod(0o600)
        temp.replace(runtime)
        store.event("controller", "sender", "controller.started", "Queue controller and local dashboard started")
        try:
            while not stopping.is_set():
                online = coordinator.tick()
                stopping.wait(3 if online else 10)
        finally:
            server.shutdown()
            server.server_close()
            store.event("controller", "sender", "controller.stopped", "Dispatch stopped; saved jobs will resume when the controller starts")
