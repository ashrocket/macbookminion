"""Read-only host sampling and deterministic placement; never executes a task.

All observations expire after five minutes. Unknown host metrics forbid local
execution; unknown session budgets require compact results but do not invent a
quota. Eligible workers must retain 2 GiB memory and their configured disk floor
plus 5 GiB output reserve (default total: 30 GiB).
Memory capacity is total RAM, not a promise of available RAM; pressure is also
required. Offloading moves tools and scratch work, never model inference/quota.
"""
import math
import os
import platform
import shutil
import subprocess
import time

MAX_AGE_SECONDS = 300
FUTURE_TOLERANCE_SECONDS = 5
MEMORY_RESERVE_GIB = 2
PRIMARY_DISK_FLOOR_GIB = 10
WORKER_DISK_FLOOR_GIB = 25
OUTPUT_RESERVE_GIB = 5
PRIMARY_LOAD_PRESSURE = 0.75
WORKER_LOAD_LIMIT = 0.9


def _number(value, *, positive=False, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        finite = math.isfinite(value)
    except (OverflowError, ValueError):
        return False
    return finite and value >= 0 and (not positive or value > 0) and (
        not integer or isinstance(value, int))


def _fresh(value, now):
    return _number(value) and -FUTURE_TOLERANCE_SECONDS <= now - value <= MAX_AGE_SECONDS


def _sysctl(name):
    try:
        result = subprocess.run(
            ["/usr/sbin/sysctl", "-n", name], capture_output=True, text=True,
            timeout=2, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def sample_primary(session_state=None, *, now=None, disk_path="/"):
    """Sample this machine; unavailable readings are None with explicit errors.

    session_state is caller-supplied JSON, never discovered in credentials or
    transcripts. Its own observed_at is preserved, not refreshed by sampling.
    """
    observed = time.time() if now is None else now
    if not _number(observed):
        raise ValueError("now must be a finite nonnegative timestamp")
    result = {
        "observed_at": observed, "logical_cpus": None, "load1": None,
        "memory_gib": None, "memory_pressure": None, "free_gib": None,
        "arch": platform.machine(), "session_state": session_state,
        "sampling_errors": [],
    }
    try:
        result["logical_cpus"] = os.cpu_count()
    except (OSError, NotImplementedError):
        pass
    try:
        result["load1"] = os.getloadavg()[0]
    except (OSError, AttributeError):
        pass
    try:
        result["free_gib"] = shutil.disk_usage(disk_path).free / (1024 ** 3)
    except OSError:
        pass
    if platform.system() == "Darwin":
        raw_memory = _sysctl("hw.memsize")
        try:
            result["memory_gib"] = int(raw_memory) / (1024 ** 3)
        except (ValueError, TypeError, OverflowError):
            pass
        raw_pressure = _sysctl("kern.memorystatus_vm_pressure_level")
        result["memory_pressure"] = {"1": "normal", "2": "warn", "4": "critical"}.get(raw_pressure)
    for key in ("logical_cpus", "load1", "memory_gib", "memory_pressure", "free_gib"):
        value = result[key]
        valid = value in ("normal", "warn", "warning", "critical") if key == "memory_pressure" else _number(
            value, positive=key in ("logical_cpus", "memory_gib"), integer=key == "logical_cpus")
        if not valid:
            result[key] = None
            result["sampling_errors"].append(key + " is unknown")
    return result


def _host(raw, now):
    fresh = isinstance(raw, dict) and _fresh(raw.get("observed_at"), now)
    raw = raw if isinstance(raw, dict) else {}
    data = {"fresh": fresh, "unknown": []}
    for key in ("logical_cpus", "load1", "memory_gib", "memory_pressure", "free_gib"):
        value = raw.get(key)
        if key == "memory_pressure" and value == "warning":
            value = "warn"
        valid = value in ("normal", "warn", "warning", "critical") if key == "memory_pressure" else _number(
            value, positive=key in ("logical_cpus", "memory_gib"), integer=key == "logical_cpus")
        data[key] = value if fresh and valid else None
        if data[key] is None:
            data["unknown"].append(key)
    data["load_ratio"] = (data["load1"] / data["logical_cpus"]
                          if data["load1"] is not None and data["logical_cpus"] else None)
    data["arch"] = raw.get("arch") if isinstance(raw.get("arch"), str) else None
    return data


def _session(raw, now):
    result = {"status": "unknown", "context_utilization": None,
              "subscription_remaining_fraction": None, "pressure": None,
              "unknown": [], "reasons": []}
    if not isinstance(raw, dict) or not _fresh(raw.get("observed_at"), now):
        result["reasons"].append("session budget is unknown or stale; require compact results")
        return result
    used, limit = raw.get("context_used"), raw.get("context_limit")
    if _number(used) and _number(limit, positive=True) and used <= limit:
        result["context_utilization"] = used / limit
    else:
        result["unknown"].append("context_utilization")
    remaining = raw.get("subscription_remaining_fraction")
    if remaining is not None and _number(remaining) and remaining <= 1:
        result["subscription_remaining_fraction"] = remaining
    else:
        result["unknown"].append("subscription_remaining_fraction")
    context_pressure = result["context_utilization"] is not None and result["context_utilization"] >= 0.75
    quota_pressure = result["subscription_remaining_fraction"] is not None and remaining <= 0.2
    if context_pressure:
        result["reasons"].append("context utilization is at least 75%; reduce and compact tool results")
    if quota_pressure:
        result["reasons"].append("reported subscription remaining is at most 20%; reduce and compact tool results")
    if result["unknown"]:
        result["reasons"].append("unmeasured session budget signals require compact results: " + ", ".join(result["unknown"]))
    result["pressure"] = True if context_pressure or quota_pressure else (None if result["unknown"] else False)
    result["status"] = "pressure" if result["pressure"] else ("unknown" if result["unknown"] else "normal")
    return result


def _task_error(task):
    if not isinstance(task, dict):
        return "task must be a JSON object"
    if task.get("kind") not in ("compute", "test", "research", "crawl"):
        return "task.kind must be compute, test, research, or crawl"
    for key in ("portable", "primary_only"):
        if type(task.get(key)) is not bool:
            return "task." + key + " must be a boolean"
    for key in ("min_memory_gib", "min_free_gib", "estimated_seconds"):
        if not _number(task.get(key)):
            return "task." + key + " must be finite and nonnegative"
    if task.get("required_arch") is not None and (not isinstance(task["required_arch"], str) or not task["required_arch"].strip()):
        return "task.required_arch must be a nonempty string or null"
    return None


def route(primary, workers, task, now=None):
    """Return local/offload/defer plus evidence; malformed input never means idle.

    Primary has the host fields used by sample_primary, including session_state.
    Task min_free_gib means minimum free capacity, not additional scratch size.
    Worker min_free_gib is an optional disk floor (default 25 GiB).
    Worker fields are id, online, observed_at, logical_cpus, load1, memory_gib,
    memory_pressure (normal/warn/critical; warning is accepted as an alias), free_gib, active_job (empty when
    idle), queued_jobs, and arch. Reserved/queued workers are not idle capacity.
    All outputs are JSON-safe even if input contains NaN/Infinity.
    """
    now = time.time() if now is None else now
    result = {"decision": "defer", "worker_id": None, "reasons": [],
              "pressure": {}, "return_hints": {
                  "compact": True, "deterministic_reduction": True,
                  "max_output_chars": 4000, "model_execution": "primary",
                  "subscription_capacity_extended": False,
              }}
    error = _task_error(task)
    if error or not _number(now) or not isinstance(primary, dict) or not isinstance(workers, list):
        result["reasons"] = [error or "invalid now, primary object, or workers list"]
        return result
    host = _host(primary, now)
    session = _session(primary.get("session_state"), now)
    result["pressure"] = {"primary": host, "session": session, "workers": []}
    reasons = result["reasons"]
    reasons.extend(session["reasons"])
    if session["pressure"] is False:
        result["return_hints"].update(compact=False, deterministic_reduction=False, max_output_chars=12000)
    pressured = False
    if host["unknown"]:
        pressured = True
        reasons.append("primary host metrics unknown or stale: " + ", ".join(host["unknown"]))
    if host["load_ratio"] is not None and host["load_ratio"] >= PRIMARY_LOAD_PRESSURE:
        pressured = True
        reasons.append("primary load is at least 75% of logical CPU capacity")
    if host["memory_pressure"] in ("warn", "critical"):
        pressured = True
        reasons.append("primary memory pressure is " + host["memory_pressure"])
    if host["free_gib"] is not None and host["free_gib"] < 20:
        pressured = True
        reasons.append("primary free disk is below 20 GiB")
    pressured = pressured or session["pressure"] is True
    required_memory = task["min_memory_gib"] + MEMORY_RESERVE_GIB
    required_disk = max(task["min_free_gib"], PRIMARY_DISK_FLOOR_GIB)
    def capacity_ok(h, disk_requirement=required_disk):
        return (not h["unknown"] and h["memory_pressure"] == "normal"
                and h["memory_gib"] >= required_memory and h["free_gib"] >= disk_requirement
                and (task.get("required_arch") is None or h["arch"] == task["required_arch"]))
    local_safe = capacity_ok(host) and host["load_ratio"] < 1
    candidates = []
    ids = [w.get("id") for w in workers if isinstance(w, dict) and isinstance(w.get("id"), str)]
    for worker in workers:
        w = worker if isinstance(worker, dict) else {}
        ident = w.get("id")
        ident = ident if isinstance(ident, str) and ident.strip() else None
        h = _host(w, now)
        rejected = []
        if not ident or ids.count(ident) != 1:
            rejected.append("missing or duplicate worker id")
        if w.get("online") is not True:
            rejected.append("worker is offline or online state is unknown")
        floor = w.get("min_free_gib", WORKER_DISK_FLOOR_GIB)
        disk_requirement = max(task["min_free_gib"], floor + OUTPUT_RESERVE_GIB) if _number(floor) else None
        if disk_requirement is None or not capacity_ok(h, disk_requirement):
            rejected.append("unknown, stale, incompatible, pressured, or insufficient host capacity")
        if h["load_ratio"] is not None and h["load_ratio"] >= WORKER_LOAD_LIMIT:
            rejected.append("worker load is saturated")
        if not isinstance(w.get("active_job"), str) or w["active_job"]:
            rejected.append("worker has an active job or unknown job state")
        if not _number(w.get("queued_jobs"), integer=True) or w["queued_jobs"] != 0:
            rejected.append("worker has queued jobs or unknown queue state")
        if not isinstance(w.get("arch"), str) or not w["arch"].strip():
            rejected.append("worker architecture is unknown")
        result["pressure"]["workers"].append({"id": ident, "eligible": not rejected, "reasons": rejected,
                                                "required_free_gib": disk_requirement})
        if not rejected:
            # Prefer more unused CPU capacity, then RAM/disk, then stable ID.
            rank = (-(h["logical_cpus"] - h["load1"]), -h["memory_gib"], -h["free_gib"], ident)
            candidates.append((rank, ident))
    can_offload = task["portable"] and not task["primary_only"]
    heavy = task["estimated_seconds"] > 60
    if can_offload and candidates and (pressured or heavy or not local_safe):
        result["decision"] = "offload"
        result["worker_id"] = min(candidates)[1]
        reasons.append("selected eligible idle worker capacity" + (" for a portable job over 60 seconds" if heavy else ""))
    elif local_safe:
        result["decision"] = "local"
        reasons.append("primary has sufficient measured capacity" + ("; task must remain local" if not can_offload else "; no offload needed or no eligible idle worker available"))
    else:
        reasons.append("primary execution is unsafe or unverified and no permitted eligible offload is available")
    return result
