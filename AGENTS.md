# DQIX decomp kit

This directory is `$KIT`, the kit checkout: scripts, docs and skills only. Everything the pipeline
produces (attempts, logs, claims, staging, worker docs, knobs, `OPEN_WORK.md`, `STATE.md`) lives in
`$SP`, the state directory outside the checkout: `$DQIX_STATE`, else the path in `state.path`, else
`../dqix-kit-state`. The decomp checkout is `$DQIX_REPO` (default `../dqix-decomp`, branch
`decomp-matching`). Every script resolves all three through `kitpaths.py`. In Git Bash, from this
directory:

    export KIT="$(pwd -W 2>/dev/null || pwd)"
    export SP="$(python kitpaths.py state)"

A session that runs from another directory sets `DQIX_KIT` to the checkout; skills and workflows
read it.

On Linux everything here works except the fleet, `frida/` and `pad/renum/`; `buildcfg.py` runs the
Win32 toolchain through the runner the decomp's own `configure.py` names, and the `.sh` scripts
resolve their interpreter and `ninja` through `kitpaths.py`. [docs/LINUX.md](docs/LINUX.md).

## Update the kit on every stop

Update at the start of every session and every time you stop:

    python kit_update.py

Claude Code sessions opened in this directory run it automatically (`.claude/settings.json`: a
SessionStart hook, and a Stop hook that keeps you working if an instruction file changed). Every
other agent (Codex, MiniMax, any model) runs it itself; nothing reminds you but this: every kit
script prints `KIT IS N COMMIT(S) BEHIND` while the checkout is stale, and `claim.py` hands out no
new work (exit 3) until you update. Your own unpublished kit commits are kept on top of each update.

| exit | meaning | do |
|---|---|---|
| 0 | up to date, or updated and `kit_init.py` re-run | re-read every file it prints as `RE-READ`, then work |
| 1 | the fetch failed (offline), or `kit_init.py` failed after an update | tell the user the line it printed; fix a `kit_init.py` FAIL before working |
| 2 | the fleet or an integration is running, so nothing was pulled | nothing to do: the fleet drains and updates itself (docs/FLEET.md); never pull under a running script |
| 3 | uncommitted changes, or your unpublished commits conflict with the update | tell the user; never stash, reset or discard them yourself |

## Changing the kit

The kit has been tested and refined over months of matching. Use it as it is. Do not change it for
your own purposes: no local tweaks, private copies of a script, notes or rewordings. Change it only
when you are certain a change is needed, and then only as one of these:

- a recipe: a `worker_src/core.md` rule that closed a function now landed on `decomp-matching`,
  citing its address, in a few lines
- a script: a `colorsweep.py` rule, a repair, or a fix to a kit script or skill, with the
  `regress.py` case that fails without it (rule 14)
- a tool fault you cannot fix with a `regress.py` case: an issue in the kit repository, not a pull
  request, giving the address, the exact command and the output line that shows the fault

Each of these can be checked: a landed address proves a recipe, a failing test proves a fix, and a
maintainer reproduces a fault before acting on it. Nothing that cannot be checked is accepted: dead ends, residue notes, trial logs, docs and wording, refactors, comments,
hardening against a fault that never happened. A dead end is a claim that nothing worked, and every
later worker on that address is handed it as settled. Record a miss in `$SP` with `blocker.py` and
the function's handoff. Opening no kit pull request is the normal outcome of a session. The kit's
maintainers push to `main` directly.
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) says what an accepted pull request needs.

**Pull both before every pull request** (rule 21). A pull request built on a stale kit or a stale
decomp silently reverts what landed since you started, re-adds files for addresses someone else
already owns, records dead ends for functions already matched and cites matches that never landed.
It is sent back, and the review it cost is wasted.

## Where things are

| need | read |
|---|---|
| state of a fresh session's work | `OPEN_WORK.md` first, then `STATE.md` (`python progress.py --print`) |
| one function, start to commit | `docs/WORKFLOW.md` |
| the fleet, knobs, stopping | `docs/FLEET.md` |
| compiler facts and pipeline rules | `docs/LESSONS.md` |
| every codegen lever | `worker_src/core.md` (1400 lines: grep a heading, never read whole) |
| forms already ruled out per address | `worker_src/deadends.md` |
| what each script does | `INVENTORY.md` |
| named residues nobody cracked | `OPEN_RESIDUES.md` (grep an address) |
| closest saved attempt per address | `priors/INDEX.tsv`, `python resumable.py <addr>` |
| naming already-matched functions | `naming/HANDOFF.md` |
| finished experiments cited as evidence | `archive/README.md` (nothing runs them) |

## Skills

`.claude/skills/` is the source of every skill. `python kit_init.py` copies the agent-neutral ones
into `.agents/skills/`, where Codex and other Agent Skills readers load them; edit the source, never
the copy.

| skill | use | agents |
|---|---|---|
| `dqix-hand-match <addr>` | close one function in this session | all |
| `dqix-status` | where things are: fleet, coverage, what moved, what needs a decision | all |
| `dqix-stop` | stop everything now and verify nothing survived | all |
| `dqix-coordinate` | coordinate native agents, bounded crack/evolve experiments, and shared findings | native-agent hosts |
| `dqix-plan` | run the standing plan: one function at a time, gate it, land it or record why | Claude Code |
| `dqix-continue` | a fresh session after a limit, a crash or a long session | Claude Code |

The fleet's workers are Claude Code sessions (`claude -p`), so running the fleet needs the Claude Code
CLI whichever agent operates the kit. Do not start the fleet, a workflow or any paid worker unless
the user asks.

For a coordinator-operated adaptation using Codex or another host's native agents, see
[docs/NATIVE_AGENTS.md](docs/NATIVE_AGENTS.md). It does not run the Claude workflow JavaScript or
start a background fleet. Keep the same reservation, gate, integration and promotion rules.

## Hard rules

1. Source being matched lives in `$SP/wip/<main|ovNNN>/`; scratch in `$SP/handwork/`. Never write
   into `$DQIX_REPO/src/`. Never put scratch in `staging/`, `hold_*/`, `gated/` or `attempts/`:
   those are pipeline inputs.
2. Create and change files with your file-editing tool. Never with a shell heredoc or `sed -i`.
3. Pass absolute source paths to `wgate.py` and `wdiff.py`; they change into the decomp directory.
4. A match lands only through `staging/<main|ovNNN>/` and `finish_wave.sh <main|NNN>` (or
   `integrate_fast.sh`). Only a commit that passes `ninja check` proves a match.
5. One integration at a time: `finish_wave.sh` and `integrate_fast.sh` take `$SP/wave.lock`. Never
   run `integrate.py` (even `--dry`) by hand while it exists. Gating, claiming and sweeping go on
   during an integration.
6. Launch integrations as a background task with a long timeout and the command passed plain: no
   `nohup`, no trailing `&`.
7. Integrations run in their own worktree (`python integ_tree.py path`) from the tip of
   `decomp-matching`, then fast-forward the decomp checkout. Uncommitted edits in the checkout are
   not part of them: commit what a match needs first.
8. No hand assembly except addresses in `asm_allow.txt`. No codegen `#pragma`. Nothing in
   `tools/cc_overrides.txt` or `tools/cc_flag_overrides.txt`. A match that needs one of these is a
   diagnosis: find what the source has that the ROM's did not.
9. Route on the `RESIDUE <CLASS>` line `wgate.py` prints. Run `colorsweep.py` before calling a
   register or scheduling residue stuck. Never write a function off as unmatchable.
10. Key on the address, never the `func_` name. A curated name in `symbols.txt` is binding; define
    exactly that symbol.
11. Preserve before deleting. An untracked worker file is the only copy of that attempt.
12. Never edit a running bash script; edit a copy and `mv` it over. Quiesce the fleet before editing
    any pipeline script (`touch STOP_PULL FLEET_STOPPED`, then `fullstop.sh`).
13. Never kill a DQIX process by hand. `bash fullstop.sh` (`--dry` to look, `--hard` for CPU jobs
    too), `bash killfleet.sh` to escalate. Count jobs with `bash psq.sh`, dispatchers only by
    `pull_all.pid`.
14. Verify the effect, not the patch. After any script edit: `python selfcheck.py` and
    `python regress.py`; `regress.py --slow` after touching `colorsweep.py`, `wdiff.py` or
    `wgate.py`; `python pipetest.py` after touching the gate.
15. Never fork a code path; parameterise it. A new script gets a line in `INVENTORY.md`.
16. After editing `worker_src/core.md`, run `python build_worker_docs.py` and grep the built doc for
    the new text.
17. Record work in progress in `OPEN_WORK.md` while working, not at the end. A fresh session starts
    from it.
18. State stays in `$SP`, outside the checkout. Never write state into `$KIT` and never point
    `state.path` or `DQIX_STATE` inside it. Update the checkout through `kit_update.py` only.
19. A crack is not finished until it is promoted: append the `$SP/wlog/levers.tsv` row, then turn the
    lever into a `colorsweep.py` rule, a repair, or a `core.md` rule citing the address, with its
    `regress.py` proof, or decline it in `levers_declined.txt`. On a miss, record the blocker with
    `blocker.py` and what was ruled out in the function's handoff; only maintainers add `deadends.md`
    rows. The dispatcher stops claiming until this is done. What may go to the kit is listed in
    "Changing the kit" above.
    [docs/IMPROVEMENT_LOOP.md](docs/IMPROVEMENT_LOOP.md) has the whole loop.
20. Reserve work with ONE open issue in the kit repository listing every address you are actively
    working on, never one issue per function. Edit it as the list changes. The moment you open the
    pull request for those addresses, close the issue with a comment naming it
    (`PR: ZevyaDev/dqix-decomp#27`); do not wait for the merge. Then open a new issue for the next
    batch. An address is reserved only while it is listed in an open kit issue or changed by an open
    pull request on ZevyaDev/dqix-decomp. An address in a closed issue with no pull request, and not
    landed in `decomp-matching`, is free: its owner stopped or failed.
    [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) has the steps.
21. Never open a pull request from a stale checkout, on either repository. Immediately before
    `gh pr create`: `python kit_update.py`; in the decomp `git pull --rebase` onto the published
    `decomp-matching`, then `python tools/configure.py usa && ninja check` and re-gate what you
    changed; then `python prready.py kit` or `python prready.py decomp` must print `READY`. Fix every
    line it prints; never work around it. In Claude Code a PreToolUse hook refuses `gh pr create`
    until it passes; every other agent runs it by hand.
