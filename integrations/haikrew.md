# HaiKrew integration

Install `skills/macbook-minion/` in the agent's skill directory. Review changes
before updating an existing installation. Keep its existing private config and
state; installation does not register a new worker or create another queue.

The HaiKrew overseer uses the MacBook Minion skill after defining a bounded tool
job. `route` reports a placement; `offload` observes again and queues it if eligible.
The overseer preserves worker/job IDs, verifies returned artifacts and integrates
the result. Native agent model routing continues through HaiKrew's normal path.

Add this short instruction to an existing HaiKrew skill or project AGENTS.md:

> For substantial portable tool work, use the installed macbook-minion skill to
> sample primary compute, memory, disk and explicit session token pressure. Use
> its canonical config and pressure-aware offload command. Keep reasoning on the
> subscription agent, keep unknown quotas unknown, and return compact findings
> backed by checksum-verified artifacts. Minion placement does not grant task
> authority, add subscription quota or replace independent review.

This is an explicit agent workflow. It does not silently intercept every tool
call, install hooks, transfer login credentials or start another model subscription.
