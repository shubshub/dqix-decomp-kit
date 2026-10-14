# Native agent coordination

This is a coordinator-operated adaptation of the Claude dynamic workflows for Codex and
other hosts with native subagents. Read [AGENTS.md](../AGENTS.md),
[WORKFLOW.md](WORKFLOW.md), [FLEET.md](FLEET.md), and
[IMPROVEMENT_LOOP.md](IMPROVEMENT_LOOP.md) first. Start with `dqix-coordinate`.

## Codex on Windows

- Run every command that talks to GitHub (`gh`, `git fetch`/`push`, `kit_update.py`) with
  escalated permissions from the first attempt: the sandbox cannot see the GitHub login.
- Run `frida/colorforce.py` and `frida/schedforce.py` escalated with a 10-minute timeout. Inside
  the sandbox frida cannot attach to the compiler and the process hangs holding a core.
- Bash cannot start inside the sandbox, so `.sh` scripts and the integrators run escalated too.
- Write files with `apply_patch`, never PowerShell redirection or `Set-Content`: PowerShell 5
  writes ANSI or UTF-16 and silently corrupts the source.

## Session and ownership

Record the authorized scope, model/effort choices, concurrency, budget limits and stop state
in `$SP/OPEN_WORK.md`.

Follow [AGENTS.md's update instructions](../AGENTS.md#update-the-kit-on-every-stop)
for updates and rereads. One coordinator serializes checks for agents sharing a checkout.
Follow [rule 21](../AGENTS.md#hard-rules) before publication on either repository.

Before each new function, reconcile current upstream ownership intervals and source, open
decomp PRs, open kit issues, and local claims. `claim.py` only arbitrates its own state;
independent state directories are not a global reservation service. One coordinator owns
the team's public batch issue and the mapping from addresses to workers. Teams can propose
their own next targets; dispatch needs a completed reservation check, not the coordinator
to solve or choose every function personally. Follow [rule 20](../AGENTS.md#hard-rules)
for reservations and release.

## Roles and evidence flow

The coordinator owns reservations, updates, stop/usage decisions and the final publication.
Delegate bounded queue preparation, blocker triage, and evidence indexing when authorized;
these do not all need to wait on the coordinator's implementation work.

Implementation agents own different functions. Same-function agents are a deliberate crack
experiment with distinct hypotheses under one claim, not independent claims. Each agent gets
the same starting source and its own writable candidate directory. Shared header
changes are proposals until the designated owner applies and validates them.

Follow [rules 4 and 5](../AGENTS.md#hard-rules) and [WORKFLOW.md](WORKFLOW.md)
for acceptance and integration. Workers write per-worker reports; a designated owner merges
shared ledgers and boards so concurrent agents cannot overwrite each other's results.

Refill an available slot with authorized, reserved work while results queue. Honor
freshness, promotion and blocker holds from the kit.

## Research records for difficult functions

Use `blocker.py` and `blockercheck.py --verbose` to measure and rank blocker families.
Select a representative by expected reusable benefit and cost. Preserve the best source,
other useful residue shapes, and failed experiments before changing the owner or model.

Keep a concise board under `$SP/handwork/`, linked from `OPEN_WORK.md`. Use the kit's
`<addr>_board.md` convention when adapting a single-function evolve run. Record the research
question, related family members, baseline and measured residue, experiment hypotheses and
candidate paths, results and logs, ruled-out hypotheses, and the next bounded experiment.

Honor `gatelog.py` STOP for that run. A follow-up is a distinct, authorized experiment with
new evidence or a new hypothesis and an explicit cap. Keep inactive research locally.

## Bounded crack and evolve

Read [.claude/workflows/dqix-crack.js](../.claude/workflows/dqix-crack.js) or
[dqix-evolve.js](../.claude/workflows/dqix-evolve.js) for the selected mode. Map their
agent/parallel/phase operations to the native tools actually exposed by the host; do not
invoke their JavaScript through Node and expect Claude's globals to exist.

For a **crack**, measure the baseline with the real gate, then assign a small authorized
round of different source hypotheses to isolated copies. Pass the relevant recipe excerpts,
dead ends, ABI/layout evidence, baseline and concise board digest. Between experiments,
check for a verified winner or stop. A reported MATCH stops new experiments; validate it
through [WORKFLOW.md](WORKFLOW.md) before treating it as accepted. If rejected,
record why before continuing within the remaining budget.

For **evolve**, score candidates through `pad/evo_score.py`. Keep a bounded population with
distinct residue signatures; do not retain only the lowest byte count. Plan directed edits
and optional crossover from named parents, and rescore children yourself instead of
trusting reported fitness. Consult [dqix-evolve.js](../.claude/workflows/dqix-evolve.js)
for its defaults; the user's authorization sets the native experiment's limits.
Fitness zero still needs the normal gate and integration checks in [WORKFLOW.md](WORKFLOW.md).

Before dispatch, set finite experiment/compile and wall-time limits, plus the user's model
and usage limits. Record actual model, effort and timing for comparisons. `evocap.py` reads
Claude session accounting; it does not cap native Codex work. Use the host's real usage
telemetry for its limits, and report unavailable cost as unknown, not zero. If a required
usage threshold cannot be checked, stop new dispatch until resolved. Never infer a cheap
run from missing Claude logs.

A stronger model can provide a bounded diagnosis of one family, using the same compact
evidence and a specific question. Use it only within the user's model/budget authorization.
The implementer must test the suggested source change; a persuasive explanation is not
a gate result. This permits a small amount of expensive reasoning to help multiple workers.

## Feed the shared kit

Follow [IMPROVEMENT_LOOP.md](IMPROVEMENT_LOOP.md) for promotion and delivery.
Mirror required outcome records from isolated worker states into the designated shared
state under a single writer, preserving provenance and avoiding duplicate rows. Otherwise
the coordinator's `levercheck.py` and `blockercheck.py` cannot see those workers' findings.
Do not add incompatible fields to the kit's TSV formats; put extra provenance in sidecars.

Send the kit only what [AGENTS.md](../AGENTS.md#changing-the-kit) lists
([CONTRIBUTING.md](CONTRIBUTING.md)).

At stop, preserve unfinished research and evidence, release or transfer reservations,
complete the serialized update/reread checks, and report attempted, gate-matched,
landed and upstream-merged totals separately. User pauses and budget stops apply to native
agents as well as shell workers; stopping a shell fleet alone does not stop native agents.
