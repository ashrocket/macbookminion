# Using HaiKrew with OpenAI Codex (WIP)

HaiKrew can coordinate OpenAI Codex helpers through a standalone Codex skill and
Python routing adapter. The main Codex conversation owns the task and design,
delegates bounded work, inspects the returned diff and verifies the result.

This guide is about using OpenAI with HaiKrew. MacBook Minion is optional: the
Codex crew works without an SSH worker. The upstream Claude Code plugin is a
separate runtime and is not loaded by these instructions.

## Install the Codex adaptation

The [Codex contribution](https://github.com/ashrocket/HaiKrew/tree/codex/openai-haikrew-wip-20261117/codex)
is WIP, awaiting contributor review before an upstream review request. Until it
is merged, inspect the contribution branch rather than assuming upstream main
contains it:

```sh
git clone --branch codex/openai-haikrew-wip-20261117 --single-branch \
  https://github.com/ashrocket/HaiKrew.git haikrew-codex
```

Review `haikrew-codex/codex/README.md` and the files under
`haikrew-codex/codex/skills/haikrew/`. Requires Python 3.9+ and a Codex client with
helper-agent tools. Copy that skill directory into the skill location supported
by your client, such as `~/.agents/skills/haikrew/`.

If HaiKrew is already installed, compare it with the contribution and preserve
local policy before updating. Do not overwrite an installed skill blindly. Open
a Codex session that can discover the skill and invoke `$haikrew`.

## Use an OpenAI crew

For example, give Codex a concrete task:

```text
Use $haikrew to implement the parser fix described in this issue. Inspect the
repository instructions and source first. Keep the current conversation as
overseer, give helpers bounded file ownership, and run the required checks.
Report the actual diff, checks and remaining concerns. Do not commit unless asked.
```

Before launching helpers, Codex checks the actual tool's available models,
reasoning efforts, fork semantics and concurrency. The supplied policy uses
`gpt-6-luna`/medium for bounded research and docs, `gpt-6.1-sol`/medium for routine
implementation/tests/review, and `gpt-6-astra`/high for sensitive work. These are
aliases from the originating runtime; they are not universal model availability.
Adapt both `config/routing.json` and the router's allowlists if your tool differs.

Use portable dispatch first: the overseer passes its exact proposed spawn
envelope to `scripts/router.py` on stdin, checks the decision, and passes the
complete returned `updatedInput` to the real collaboration spawn tool. A routing
decision does not launch a model or authorize a tool call. The skill contains the
full envelope and handling rules. Omit `--log` in portable mode.

Normal implementation benefits from an independent review of the actual changes;
small low-risk changes can be reviewed directly. Respect any explicit owner hold
on review requests. Run the repository's required checks, and distinguish model
selection, worker completion and verification in the final report.

## Optional local hooks

The adaptation documents a local `PreToolUse` hook for compatible Codex runtimes.
Installation of the skill does not install or enable that hook. Inspect and trust
any new or changed definition in `/hooks`; preserve unrelated definitions. Cloud
orchestration does not run local command hooks just because tools execute locally.

Keep using portable dispatch until a real no-model spawn, hook receipt and actual
worker model demonstrate interception. Fixture tests alone are insufficient. See
the contribution's README and the [official hook documentation](https://learn.chatgpt.com/docs/hooks).

## Optional MacBook Minion placement

When a crew has a suitable portable tool job, the separately installed
`macbook-minion` skill can place it on a compatible worker. Follow the existing
[integration guide](../integrations/haikrew.md) and
[queue guide](../skills/macbook-minion/references/queue.md).
Keep model reasoning in Codex and use the existing canonical queue/configuration.
Moving tool execution does not add OpenAI quota or prove token savings.
