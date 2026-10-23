#!/usr/bin/env python3
"""Isolated headless Chrome jobs using the standard WebDriver HTTP protocol."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

from browser_plan import validate_plan

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
ELEMENT = "element-6066-11e4-a52e-4f735466cecf"


def chrome_version():
    with CHROME.parent.parent.joinpath("Info.plist").open("rb") as f:
        return plistlib.load(f)["CFBundleShortVersionString"]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def owned_workspace():
    root = Path.home() / ".macbookminion"
    job = os.environ.get("MINION_JOB_ID", "")
    if not re.fullmatch(r"j-[a-z0-9-]+", job):
        raise RuntimeError("Browser adapter must run through the shared controller")
    if root.joinpath("active/job").read_text().strip() != job:
        raise RuntimeError("Browser job does not own the worker lock")
    for part in (root, root / "browser"):
        if part.is_symlink():
            raise RuntimeError("Unsafe browser workspace")
        part.mkdir(exist_ok=True, mode=0o700)
    return root


def install(root, output):
    receipt = json.loads(Path("driver.json").read_text())
    version = receipt["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
        raise RuntimeError("Invalid driver version")
    if version.split(".")[:3] != chrome_version().split(".")[:3]:
        raise RuntimeError("Chrome changed; run browser-setup again")
    source = Path("chromedriver")
    if sha256(source) != receipt["binary_sha256"]:
        raise RuntimeError("Driver checksum mismatch")
    source.chmod(0o700)
    signature = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(source)],
                               text=True, capture_output=True)
    # Google's mac-x64 package can be unsigned. Record that fact; never alter
    # quarantine/Gatekeeper settings, and reject an invalid existing signature.
    if signature.returncode and "code object is not signed at all" not in signature.stderr:
        raise RuntimeError("Driver signature validation failed: " + signature.stderr)
    receipt["code_signature"] = "unsigned" if signature.returncode else "verified"
    observed = subprocess.check_output([str(source.resolve()), "--version"], text=True, timeout=15).strip()
    if not observed.startswith("ChromeDriver " + version + " "):
        raise RuntimeError("Driver reported an unexpected version")
    drivers = root / "browser/drivers"
    if drivers.is_symlink():
        raise RuntimeError("Unsafe driver directory")
    drivers.mkdir(exist_ok=True, mode=0o700)
    target = drivers / version
    if target.is_symlink():
        raise RuntimeError("Unsafe driver path")
    if target.exists():
        if sha256(target) != receipt["binary_sha256"]:
            raise RuntimeError("Existing driver differs; retained for inspection")
    else:
        with target.open("xb") as f:
            f.write(source.read_bytes())
        target.chmod(0o700)
    receipt["driver_version_output"] = observed
    staging = root / ("browser/driver-" + os.environ["MINION_JOB_ID"] + ".json")
    staging.write_text(json.dumps(receipt, indent=2))
    staging.replace(root / "browser/driver.json")
    output.joinpath("installation.json").write_text(json.dumps(receipt, indent=2))


class WebDriverError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(code + ": " + message)
        self.code = code


class Driver:
    def __init__(self, port):
        self.base = "http://127.0.0.1:" + str(port)
        self.session = ""
        # Do not send loopback browser commands to an inherited HTTP proxy.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, path, data=None, timeout=35):
        request = urllib.request.Request(self.base + path, method=method,
                    data=json.dumps(data).encode() if data is not None else None,
                    headers={"Content-Type": "application/json"})
        try:
            with self.http.open(request, timeout=timeout) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            result = json.load(error)
        value = result.get("value")
        if isinstance(value, dict) and value.get("error"):
            raise WebDriverError(value["error"], value.get("message", ""))
        return value

    def command(self, method, suffix, data=None, timeout=35):
        return self.call(method, "/session/" + self.session + suffix, data, timeout)

    def element(self, selector):
        # Require a unique target instead of silently clicking the first match.
        values = self.command("POST", "/elements", {"using": "css selector", "value": selector})
        if len(values) != 1:
            raise WebDriverError("target count", "Expected one matching element; found " + str(len(values)))
        return "/element/" + values[0][ELEMENT]

    def capture(self, output, name):
        output.joinpath(name + ".png").write_bytes(base64.b64decode(self.command("GET", "/screenshot"), validate=True))
        body = self.element("body")
        output.joinpath(name + ".txt").write_text(self.command("GET", body + "/text"), encoding="utf-8")
        output.joinpath(name + ".html").write_text(self.command("GET", "/source"), encoding="utf-8")
        output.joinpath(name + ".json").write_text(json.dumps({
            "title": self.command("GET", "/title"), "url": self.command("GET", "/url")}, indent=2))


def perform(driver, step, output):
    action = step["action"]
    if action == "open":
        driver.command("POST", "/url", {"url": step["url"]})
    elif action == "capture":
        driver.capture(output, step["name"])
    elif action in ("wait", "assert_text"):
        deadline = time.monotonic() + 15
        while True:
            try:
                element = driver.element(step["selector"])
                text = driver.command("GET", element + "/text")
                if action == "wait" or step["text"] in text:
                    return
            except WebDriverError as error:
                if error.code not in ("target count", "stale element reference"):
                    raise
            if time.monotonic() >= deadline:
                raise RuntimeError("Element/text check timed out")
            time.sleep(0.2)
    else:
        element = driver.element(step["selector"])
        if action == "fill":
            kind = driver.command("GET", element + "/attribute/type")
            if kind in ("password", "file", "hidden"):
                raise RuntimeError("Password, upload, and hidden fields are not supported")
            driver.command("POST", element + "/clear", {})
            driver.command("POST", element + "/value", {"text": step["text"]})
        elif action == "click":
            driver.command("POST", element + "/click", {})


def timeout_handler(signum, frame):
    raise TimeoutError("Browser job time limit reached")


def run(root, output, plan):
    report = {"job": os.environ["MINION_JOB_ID"], "host": platform.node(),
              "os": platform.system(), "arch": platform.machine(), "steps_completed": 0,
              "success": False, "profile_removed": False, "driver_stopped": False}
    start = time.monotonic()
    driver = None
    process = None
    profile = None
    log = None
    lock_marker = root / "jobs" / os.environ["MINION_JOB_ID"] / "retain_lock"
    old_alarm = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(plan["timeout_seconds"])
    try:
        receipt = json.loads(root.joinpath("browser/driver.json").read_text())
        version = receipt["version"]
        if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
            raise RuntimeError("Invalid driver receipt")
        report["chrome_version"] = chrome_version()
        if version.split(".")[:3] != report["chrome_version"].split(".")[:3]:
            raise RuntimeError("Browser/driver build mismatch; run browser-setup")
        binary = root / "browser/drivers" / version
        if binary.is_symlink() or sha256(binary) != receipt["binary_sha256"]:
            raise RuntimeError("Installed driver checksum mismatch")
        # If this process is killed, the runner must leave ownership in place.
        lock_marker.write_text("Browser cleanup must be verified before releasing this job\n")
        profile = Path(tempfile.mkdtemp(prefix="profile-", dir=root / "browser"))
        report["profile_path"] = str(profile)
        # A port race fails this job; never connect to an unrelated service/session.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        report["driver_port"] = port
        log = output.joinpath("driver.log").open("w")
        process = subprocess.Popen([str(binary), "--port=" + str(port)],
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        report["driver_pid"] = process.pid
        driver = Driver(port)
        deadline = time.monotonic() + 15
        while True:
            if process.poll() is not None:
                raise RuntimeError("ChromeDriver exited during startup")
            # Its own startup line confirms this process claimed the selected port.
            if "ChromeDriver was started successfully" in output.joinpath("driver.log").read_text():
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("ChromeDriver startup timed out")
            time.sleep(0.1)
        listeners = subprocess.check_output([
            "/usr/sbin/lsof", "-nP", "-a", "-p", str(process.pid),
            "-iTCP", "-sTCP:LISTEN", "-Fn"], text=True, timeout=10)
        addresses = [line[1:] for line in listeners.splitlines() if line.startswith("n")]
        report["driver_listeners"] = addresses
        if not addresses or any(address not in ("127.0.0.1:" + str(port), "[::1]:" + str(port)) for address in addresses):
            raise RuntimeError("Browser driver is not restricted to loopback")
        result = driver.call("POST", "/session", {"capabilities": {"alwaysMatch": {
            "browserName": "chrome", "acceptInsecureCerts": False,
            "unhandledPromptBehavior": "dismiss and notify",
            "goog:chromeOptions": {"binary": str(CHROME), "args": [
                "--headless=new", "--user-data-dir=" + str(profile), "--remote-debugging-pipe",
                "--window-size=1280,900", "--no-first-run", "--no-default-browser-check"],
                "prefs": {"credentials_enable_service": False, "profile.password_manager_enabled": False,
                          "download_restrictions": 3}}}}})
        driver.session = result["sessionId"]
        report["browser_version"] = result["capabilities"].get("browserVersion")
        driver.command("POST", "/timeouts", {"pageLoad": 30000, "script": 10000, "implicit": 0})
        for step in plan["steps"]:
            perform(driver, step, output)
            report["steps_completed"] += 1
        report["success"] = True
    except Exception as error:
        report["error"] = str(error)[:3000]
        signal.alarm(10)
        if driver and driver.session:
            try:
                driver.capture(output, "failure")
            except Exception:
                pass
    finally:
        signal.alarm(0)
        if driver and driver.session:
            try:
                driver.command("DELETE", "", timeout=10)
            except Exception as error:
                report["close_error"] = str(error)[:500]
        if process:
            # Only the process group created by THIS job; never pkill by browser name.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            report["driver_stopped"] = process.poll() is not None
            deadline = time.monotonic() + 5
            while True:
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    report["process_group_stopped"] = True
                    break
                if time.monotonic() >= deadline:
                    report["process_group_stopped"] = False
                    break
                time.sleep(0.1)
        else:
            report["process_group_stopped"] = True
        if log:
            log.close()
        if profile and report["process_group_stopped"]:
            try:
                shutil.rmtree(profile)
                report["profile_removed"] = not profile.exists()
            except OSError as error:
                report["cleanup_error"] = str(error)
        elif profile is None:
            report["profile_removed"] = True
        if report["process_group_stopped"] and report["profile_removed"]:
            lock_marker.unlink(missing_ok=True)
        report["lock_retained"] = lock_marker.exists()
        signal.signal(signal.SIGALRM, old_alarm)
        report["elapsed_seconds"] = round(time.monotonic() - start, 3)
        if not report["driver_stopped"] or report["lock_retained"]:
            report["success"] = False
        output.joinpath("report.json").write_text(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


def main():
    os.umask(0o077)
    root = owned_workspace()
    output = Path(os.environ["MINION_OUTPUT_DIR"])
    if sys.argv[1] == "install":
        install(root, output)
        return 0
    if sys.argv[1] == "selftest":
        return selftest(root, output)
    plan = validate_plan(json.loads(Path("plan.json").read_text()))
    return run(root, output, plan)


def selftest(root, output):
    """A dedicated generated test page; no files or user data are served."""
    page = b'''<!doctype html><html lang="en"><meta charset="utf-8">
<title>MacBook Minion browser test</title>
<style>body{font:20px system-ui;margin:60px;max-width:700px;color:#15304a}
input,button{font:inherit;padding:12px;margin:10px 8px 10px 0}#result{padding:20px;background:#e8f5ed}</style>
<h1>MacBook Minion browser test</h1><p>This page is generated on the Intel worker for verification.</p>
<form onsubmit="event.preventDefault();document.getElementById('result').textContent='Verified: '+document.getElementById('name').value">
<label for="name">Test input</label><br><input id="name"><button type="submit">Run check</button></form>
<p id="result">Waiting for browser interaction</p></html>'''
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    plan = validate_plan({"timeout_seconds": 60, "steps": [
        {"action": "open", "url": "http://127.0.0.1:" + str(server.server_port)},
        {"action": "assert_text", "selector": "h1", "text": "MacBook Minion browser test"},
        {"action": "fill", "selector": "#name", "text": "Intel MacBook"},
        {"action": "capture", "name": "before-submit"},
        {"action": "click", "selector": "button[type='submit']"},
        {"action": "assert_text", "selector": "#result", "text": "Verified: Intel MacBook"},
        {"action": "capture", "name": "after-submit"}]}, allow_private=True)
    try:
        return run(root, output, plan)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    sys.exit(main())
