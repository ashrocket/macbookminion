"""Canonical worker registry and conservative, read-only capacity observations."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

import audit
import local_context


def worker_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value):
        raise ValueError("Worker ID must contain lowercase letters, digits and hyphens")
    return value


def registry(document):
    import minion
    if not isinstance(document, dict):
        raise ValueError("Configuration must be an object")
    entries = document.get("workers", {})
    if not isinstance(entries, dict):
        raise ValueError("workers must be an object")
    entries = dict(entries)
    if "host" in document:
        if "default" in entries:
            raise ValueError("Legacy host already defines worker default")
        entries["default"] = {k: v for k, v in document.items() if k not in ("workers", "default_worker")}
    if not entries:
        raise ValueError("No workers configured")
    result, hosts, identities = {}, set(), set()
    for name, entry in entries.items():
        worker_id(name)
        if not isinstance(entry, dict):
            raise ValueError("Worker configuration must be an object")
        entry = minion.validate_config(dict(entry))
        host = entry["host"].split("@")[-1].lower().rstrip(".")
        if host in hosts:
            raise ValueError("Duplicate worker host; use the existing worker ID")
        hosts.add(host)
        uid = entry.get("worker_uid")
        if "worker_uid" in entry:
            if not isinstance(uid, str) or not re.fullmatch(r"[a-f0-9]{64}", uid) or uid in identities:
                raise ValueError("Invalid or duplicate physical worker identity")
            identities.add(uid)
        result[name] = entry
    default = document.get("default_worker", "default")
    if default not in result:
        raise ValueError("default_worker does not identify a configured worker")
    return result, default


def load_configs(path):
    path = Path(path).expanduser().resolve()
    entries, _ = registry(json.loads(path.read_text()))
    result = {}
    for name, entry in entries.items():
        host_hash = hashlib.sha256(entry["host"].encode()).hexdigest()[:12]
        result[name] = {**entry, "_worker_id": name, "_config_path": str(path),
                        "_state_dir": str(path.parent / ("state-" + host_hash))}
    return result


def select(path, worker=None):
    path = Path(path).expanduser().resolve()
    document = json.loads(path.read_text())
    entries, default = registry(document)
    name = worker if worker is not None else default
    if name not in entries:
        raise ValueError("Unknown worker: " + str(name))
    entry = entries[name]
    host_hash = hashlib.sha256(entry["host"].encode()).hexdigest()[:12]
    return {**entry, "_worker_id": name, "_config_path": str(path),
            "_state_dir": str(path.parent / ("state-" + host_hash))}


@contextmanager
def config_lock(path):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lockpath = path.with_name(path.name + ".lock")
    fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield path


def write_document(path, document):
    """Caller holds config_lock, including its read/modify/write transaction."""
    fd, temporary = tempfile.mkstemp(prefix=".minion-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(document, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_initial(path, config):
    with config_lock(path) as path:
        if path.exists():
            raise ValueError("Configuration already exists; use worker-add")
        write_document(path, config)


def resolved_host(config, transport):
    # ssh -G expands local aliases without connecting or changing authentication.
    value = transport.execute(transport.ssh_base(config) + ["-G", config["host"]], timeout=15)
    fields = dict(line.split(None, 1) for line in value.splitlines() if " " in line)
    host = fields.get("hostname")
    if not host:
        raise ValueError("Cannot resolve SSH alias")
    return host.lower().rstrip(".")


def add_worker(path, name, host, identity, min_free_gib=25, transport=None):
    import minion
    transport = transport or minion
    worker_id(name)
    if identity is None:
        raise ValueError("worker-add requires an explicit SSH identity")
    candidate = minion.validate_config({"host": host, "identity": str(identity), "min_free_gib": min_free_gib})
    info = transport.key_values(transport.remote(candidate, "probe"))
    if info.get("os") != "Darwin":
        raise ValueError("Expected a macOS worker; configuration not saved")
    endpoint = resolved_host(candidate, transport)
    uid = info.get("worker_uid", "")
    if not re.fullmatch(r"[a-f0-9]{64}", uid):
        raise ValueError("Cannot establish physical worker identity; configuration not saved")
    candidate["worker_uid"] = uid
    with config_lock(path) as path:
        document = json.loads(path.read_text())
        entries, _ = registry(document)
        if name in entries:
            raise ValueError("Worker ID already exists")
        for current in entries.values():
            if (host.split("@")[-1].lower().rstrip(".") == current["host"].split("@")[-1].lower().rstrip(".")
                    or resolved_host(current, transport) == endpoint):
                raise ValueError("Host or SSH alias is already registered")
            existing_uid = current.get("worker_uid")
            if not existing_uid:
                existing_uid = transport.key_values(transport.remote(current, "probe")).get("worker_uid")
            if not existing_uid:
                raise ValueError("Cannot verify existing worker identity; retry when it is reachable")
            if existing_uid == uid:
                raise ValueError("Physical worker is already registered under another host")
        document.setdefault("workers", {})[name] = candidate
        registry(document)
        write_document(path, document)
    return {"worker": name, "configured": True, "probe_verified": True}


def number(value, divisor=1, integer=False):
    try:
        value = float(value) / divisor
        if not math.isfinite(value) or value < 0:
            return None
        if integer:
            return int(value) if value.is_integer() else None
        return round(value, 3)
    except (TypeError, ValueError):
        return None


def observe(config, transport=None):
    import minion
    transport = transport or minion
    store = audit.store_for(config)
    pending = sum(row["state"] == "queued" for row in store.jobs()) if store else 0
    state = {"worker": config.get("_worker_id", "default"), "online": False,
             "logical_cpus": None, "load1": None, "memory_gib": None,
             "memory_pressure": "unknown", "free_gib": None, "arch": None,
             "active_job": None, "pending_queue_count": pending, "observed_at": time.time(),
             "id": config.get("_worker_id", "default"), "queued_jobs": pending,
             "min_free_gib": config["min_free_gib"], "python3": False, "python3_version": None}
    try:
        info = transport.key_values(transport.remote(config, "probe", timeout=25))
        if info.get("os") != "Darwin":
            raise ValueError("Unverified worker response")
        if "worker_uid" in config and info.get("worker_uid") != config["worker_uid"]:
            raise ValueError("Physical worker identity differs or is unavailable")
        state.update(online=True, logical_cpus=number(info.get("logical_cpus"), integer=True),
                     load1=number(info.get("load1")), memory_gib=number(info.get("memory_bytes"), 1024**3),
                     free_gib=number(info.get("free_kib"), 1024**2))
        state["python3"] = bool(info.get("tool_python3"))
        version = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", info.get("version_python3", ""))
        state["python3_version"] = version.group(1) if version else None
        pressure = info.get("memory_pressure")
        state["memory_pressure"] = pressure if pressure in ("normal", "warn", "critical") else "unknown"
        state["arch"] = info.get("arch") if info.get("arch") in ("x86_64", "arm64", "i386") else None
        active = info.get("active_job")
        state["active_job"] = active if isinstance(active, str) and (not active or re.fullmatch(r"j-[a-z0-9-]{1,77}", active)) else None
    except Exception as error:
        # Never expose SSH stderr (which can contain private connection metadata),
        # or substitute stale capacity when an observation failed.
        state["error"] = type(error).__name__
        if isinstance(error, local_context.LocalContextError):
            state.update(online=None, error_code=error.code)
    state["observed_at"] = time.time()
    return state


def overview(path, transport=None):
    return {"workers": [observe(config, transport) for config in load_configs(path).values()]}


def assert_item_worker(config, item):
    """Known IDs stay on their originating host; unknown legacy IDs stay selected."""
    path = config.get("_config_path")
    if not path:
        return
    for other in load_configs(path).values():
        if other["_state_dir"] == config["_state_dir"]:
            continue
        # Avoid creating ledgers on workers that have never been used.
        if not (Path(other["_state_dir"]) / "controller.sqlite3").is_file():
            continue
        store = audit.store_for(other)
        if store.job(item) or store.state("snapshot:" + item):
            raise ValueError("ID belongs to worker " + other["_worker_id"] + "; select it explicitly")
