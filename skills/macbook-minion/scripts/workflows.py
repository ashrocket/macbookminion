"""HaiKrew placement bridge: observe, select, then use the existing durable queue."""
import json
import re
from pathlib import Path
import shutil
import ssl
import tempfile

import audit
import control
import fleet
import minion
import pressure
import research_worker


def add_arguments(sub):
    command = sub.add_parser("pressure", help="Sample primary CPU, RAM, disk and explicit session budgets")
    command.add_argument("--session-state", type=Path)
    command = sub.add_parser("route", help="Recommend placement from fresh pressure and fleet observations")
    command.add_argument("task", type=Path)
    command.add_argument("--session-state", type=Path)
    command = sub.add_parser("offload", help="Route a reviewed portable script and enqueue only an eligible offload")
    command.add_argument("source", type=Path)
    command.add_argument("--task", required=True, type=Path)
    command.add_argument("--script", required=True, type=Path)
    command.add_argument("--reserve-output-gib", type=int, default=5)
    research = sub.add_parser("research", help="Queue bounded public-web collection on an eligible worker")
    research.add_argument("plan", type=Path)
    research.add_argument("--ca-bundle", type=Path, help="Reviewed PEM certificate bundle; never disables TLS verification")
    for item in (command, research):
        item.add_argument("--session-state", type=Path)
        item.add_argument("--owner", required=True)
        item.add_argument("--title", required=True)
        item.add_argument("--description", required=True)


def read(path):
    return json.loads(path.read_text()) if path else None


def certificate_bundle(path):
    """Only public PEM certificates/comments may enter the source snapshot."""
    text = Path(path).read_text()
    blocks = re.findall(r"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----", text)
    rest = re.sub(r"-----BEGIN CERTIFICATE-----[\s\S]*?-----END CERTIFICATE-----", "", text)
    if not blocks or any(line.strip() and not line.lstrip().startswith("#") for line in rest.splitlines()):
        raise ValueError("CA bundle must contain only public PEM certificates and comments")
    clean = "\n".join(blocks) + "\n"
    ssl.create_default_context(cadata=clean)  # Reject malformed certificates before transfer.
    return clean


def placement(args, task, *, needs_python=False, reserve=5):
    primary = pressure.sample_primary(read(args.session_state))
    workers = fleet.overview(args.config)["workers"]
    if reserve > 5:
        # The policy adds its base 5GiB allowance to each worker's own floor.
        workers = [{**w, "min_free_gib": w["min_free_gib"] + reserve - 5} for w in workers]
    if args.worker:
        # Validate selection rather than silently treating a typo as an empty fleet.
        minion.load_config(args.config, args.worker)
        workers = [w for w in workers if w["id"] == args.worker]
    if needs_python:
        for worker in workers:
            version = worker.get("python3_version") or ""
            parts = tuple(int(p) for p in version.split(".") if p.isdigit())
            if not worker.get("python3") or parts < (3, 8):
                worker["online"] = False
    return pressure.route(primary, workers, task)


def submit(args, source, script, task, reserve=5, needs_python=False):
    if type(reserve) is not int or reserve < 1:
        raise ValueError("Output reserve must be at least 1 GiB")
    # Validate metadata and syntax before any transfer; submit validates them again.
    control.text_field(args.title, "Title", 160)
    control.text_field(args.description, "Description", 2000)
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", args.owner):
        raise ValueError("Owner must be a project/session identifier")
    minion.execute(["/bin/bash", "-n", str(script.resolve())])
    minion.inventory(source)
    decision = placement(args, task, needs_python=needs_python, reserve=reserve)
    result = {"submitted": False, "routing": decision}
    if decision["decision"] != "offload":
        return result
    config = minion.load_config(args.config, decision["worker_id"])
    with audit.context(owner=args.owner):
        snapshot = minion.put(config, source)
        receipt = control.submit(config, snapshot["snapshot"], script, reserve, args.owner,
                                 title=args.title, description=args.description)
        audit.event(config, "haikrew", "controller", "placement.selected",
                    "Pressure-aware offload selected", worker=decision["worker_id"],
                    job_id=receipt["job"], reasons=decision["reasons"])
    return {**result, **receipt, "submitted": True, "worker": decision["worker_id"],
            "snapshot": snapshot["snapshot"], "verified_complete": False}


def handle(args):
    if args.action == "pressure":
        return pressure.sample_primary(read(args.session_state))
    if args.action == "route":
        return placement(args, read(args.task))
    if args.action == "offload":
        return submit(args, args.source, args.script, read(args.task), args.reserve_output_gib)
    if args.action == "research":
        plan = research_worker.validate_plan(read(args.plan))
        # Collection is useful independently, and should leave the interactive Mac free.
        task = {"kind": "research", "portable": True, "primary_only": False,
                "min_memory_gib": .25, "min_free_gib": 1, "estimated_seconds": 120}
        with tempfile.TemporaryDirectory(prefix="minion-research-") as temp:
            source = Path(temp)
            shutil.copyfile(Path(__file__).with_name("research_worker.py"), source / "research_worker.py")
            (source / "plan.json").write_text(json.dumps(plan))
            script = source / "research.sh"
            configured = read(args.config).get("research_ca_bundle")
            bundle = args.ca_bundle or configured
            extra = ""
            if bundle:
                (source / "ca-bundle.crt").write_text(certificate_bundle(Path(bundle).expanduser()))
                extra = " --ca-file ca-bundle.crt"
            script.write_text('#!/bin/bash\nset -euo pipefail\n'
                              'minion_note "Collecting bounded public evidence"\n'
                              'python3 research_worker.py plan.json "$MINION_OUTPUT_DIR/research.json"' + extra + '\n')
            return submit(args, source, script, task, needs_python=True)
    raise ValueError("Unknown workflow action")
