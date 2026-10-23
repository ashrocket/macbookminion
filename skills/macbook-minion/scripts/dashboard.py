"""Read-only, loopback-only dashboard for the shared controller ledger."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import parse_qs, urlsplit

import audit


def make_server(config, token, instance, port=8765):
    store = audit.store_for(config)
    prefix = "/" + token + "/"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not write the private dashboard URL into request logs.

        def respond(self, status, value, html=False, nonce=None):
            body = value.encode() if html else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'nonce-" + (nonce or "none") + "'; style-src 'nonce-" + (nonce or "none") + "'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.respond(405, {"error": "This dashboard is read-only. Use the controller CLI for changes."})

        do_PUT = do_DELETE = do_OPTIONS = do_POST

        def do_GET(self):
            allowed_host = "127.0.0.1:" + str(self.server.server_port)
            if (self.headers.get("Host") != allowed_host or
                    self.headers.get("Origin") not in (None, "http://" + allowed_host)):
                self.respond(403, {"error": "Local origin required"})
                return
            parsed = urlsplit(self.path)
            if not parsed.path.startswith(prefix):
                self.respond(404, {"error": "Unknown dashboard address"})
                return
            route = parsed.path[len(prefix):]
            query = parse_qs(parsed.query)
            try:
                if route in ("", "index.html"):
                    nonce = secrets.token_urlsafe(20)
                    page = Path(__file__).parent.parent.joinpath("assets/dashboard.html").read_text()
                    self.respond(200, page.replace("__NONCE__", nonce), html=True, nonce=nonce)
                elif route == "api/health":
                    self.respond(200, {"instance": instance, "ok": True})
                elif route == "api/state":
                    jobs = store.jobs()
                    for job in jobs:
                        job["remote"] = {k: v for k, v in job["remote"].items() if not k.endswith("_b64")}
                    self.respond(200, {"jobs": jobs, "worker": store.state("worker"), "at": audit.now(),
                                       "controller": {"instance": instance, "host": "This Mac", "read_only": True}})
                elif route == "api/events":
                    import minion
                    job = query.get("job", [None])[0]
                    if job:
                        minion.identifier(job)
                    before = int(query["before"][0]) if "before" in query else None
                    self.respond(200, {"events": store.events(job, before=before)})
                elif route == "api/logs":
                    import minion
                    job = minion.identifier(query.get("job", [""])[0])
                    row = store.job(job)
                    if not row:
                        self.respond(404, {"error": "Unknown job"})
                    elif row["state"] in ("queued", "cancelled"):
                        self.respond(200, {"text": "This job has not started on the MacBook."})
                    else:
                        with audit.context(job=job):
                            self.respond(200, {"text": minion.remote(config, "logs", job, timeout=15)})
                elif route == "api/audit":
                    self.respond(200, store.verify())
                else:
                    self.respond(404, {"error": "Unknown endpoint"})
            except (ValueError, KeyError):
                self.respond(400, {"error": "Invalid request"})
            except Exception as error:
                self.respond(503, {"error": "Data unavailable; check connection and retry", "type": type(error).__name__})

    try:
        return ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        if port == 0:
            raise
        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)
