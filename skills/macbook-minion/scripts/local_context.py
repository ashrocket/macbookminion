"""Diagnose the calling process, without contacting or blaming a worker."""
import os
import pwd
import subprocess
import sys

CODE = "local_context_unavailable"
RECOVERY = (
    "This process cannot use the local macOS user/session services. "
    "Open a fresh Terminal and compare id -un and launchctl managername. "
    "If those work there, restart the affected parent application/daemon from "
    "that healthy session. Changing SSH usernames or keys will not repair it. "
    "Saved jobs and remote locks must be preserved."
)


class LocalContextError(OSError):
    code = CODE

    def __init__(self):
        super().__init__(CODE + ": " + RECOVERY)


def identity_available():
    try:
        return bool(pwd.getpwuid(os.getuid()).pw_name)
    except (KeyError, OSError):
        return False


def inspect():
    identity = identity_available()
    manager = None
    manager_ok = sys.platform != "darwin"
    if sys.platform == "darwin" and identity:
        try:
            result = subprocess.run(["/bin/launchctl", "managername"], capture_output=True,
                                    text=True, timeout=3, check=False)
            name = result.stdout.strip()
            if result.returncode == 0 and name in ("Aqua", "Background", "System", "LoginWindow"):
                manager, manager_ok = name, True
        except (OSError, subprocess.TimeoutExpired):
            pass
    ready = identity and manager_ok
    return {"ready": ready, "uid": os.getuid(), "user_lookup": "ok" if identity else "unavailable",
            "launch_manager": manager, "error_code": None if ready else CODE,
            "recovery": None if ready else RECOVERY}


def require_healthy():
    result = inspect()
    if not result["ready"]:
        raise LocalContextError()
    return result
