"""Small, reviewable browser job format; shared by controller and worker."""
from urllib.parse import urlsplit
import ipaddress
import re


def _is_disallowed_destination(hostname):
    """Reject a literal loopback/private/link-local/reserved IP address.

    Static and DNS-free by design (this module stays a small, reviewable,
    offline-testable format shared by both sides): a hostname that only
    resolves to a private address (DNS rebinding) is not caught here.
    """
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return (address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified)


def validate_plan(plan, *, allow_private=False):
    if not isinstance(plan, dict) or set(plan) - {"steps", "timeout_seconds"}:
        raise ValueError("Plan accepts only steps and timeout_seconds")
    timeout = plan.get("timeout_seconds", 120)
    if type(timeout) is not int or not 10 <= timeout <= 900:
        raise ValueError("timeout_seconds must be an integer from 10 to 900")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError("Plan needs 1 to 100 steps")
    fields = {"open": {"url"}, "click": {"selector"},
              "fill": {"selector", "text"}, "wait": {"selector"},
              "assert_text": {"selector", "text"}, "capture": {"name"}}
    names = set()
    for step in steps:
        if not isinstance(step, dict) or step.get("action") not in fields:
            raise ValueError("Unknown browser action")
        action = step["action"]
        if set(step) != fields[action] | {"action"}:
            raise ValueError("Unexpected or missing fields for " + action)
        for field in fields[action]:
            if not isinstance(step[field], str) or len(step[field]) > 8000 or "\x00" in step[field]:
                raise ValueError("Browser fields must be strings of at most 8000 characters")
        if "selector" in step and not step["selector"].strip():
            raise ValueError("Empty selector")
        if action == "open":
            url = urlsplit(step["url"])
            if (url.scheme not in ("http", "https") or not url.hostname
                    or url.username is not None or url.password is not None
                    or any(c.isspace() for c in step["url"])):
                raise ValueError("Open requires HTTP(S), with no embedded credentials or whitespace")
            _ = url.port  # Reject malformed ports before submission.
            if not allow_private and _is_disallowed_destination(url.hostname):
                raise ValueError("Open targets only the public web; loopback/private/link-local addresses are rejected")
        if action == "capture":
            name = step["name"]
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", name) or name in names or name == "failure":
                raise ValueError("Capture names must be unique short lowercase names; failure is reserved")
            names.add(name)
    if steps[0]["action"] != "open":
        raise ValueError("First step must open an HTTP(S) page")
    return {"timeout_seconds": timeout, "steps": steps}
