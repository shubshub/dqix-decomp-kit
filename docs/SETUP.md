# Setup

## Platform

| component | tested | notes |
|---|---|---|
| OS | Windows 11 | fleet scripts list processes with PowerShell `Get-CimInstance`; Windows only |
| OS | Debian 13 | everything but the fleet, `frida/` and `pad/renum/`; see [LINUX.md](LINUX.md) |
| shell | Git Bash | the `.sh` scripts are bash; most take Windows paths from `pwd -W`, with a POSIX fallback |
| Python | 3.10 | `kit_init.py` requires 3.10 or newer; the decomp README asks for 3.11 or newer |
| `git`, `ninja`, `bash` | on PATH | `kit_init.py` checks for them; on Linux `ninja` may live in a virtualenv beside the interpreter, which the `.sh` scripts put back on `PATH` themselves |
| Claude Code CLI `claude` | on PATH, logged in | needed for the fleet and the workflows; Codex and other agents read `AGENTS.md` and `.agents/skills/` instead |

The per-function tools run `tools/mwccarm/<version>/mwccarm.exe` from the decomp directly. That is a
Win32 binary on every platform: on Linux the build runs it under `wibo` and so does the kit, through
the runner `buildcfg.py` reads out of the decomp's own `tools/configure.py`.

On Windows, verify `python`, `ninja`, and `bash` in the process environment. Use Git's actual
`usr/bin/bash.exe`, not the WSL `bash.exe` in System32 or Git's `bin/bash.exe` launcher.
Killing the launcher after a subprocess timeout may leave its child holding output pipes
open. Put Git's `usr/bin` before other Bash providers on PATH; keep these settings process-local.

## Layout on disk

    <parent>/
      dqix-decomp/        decomp checkout, branch decomp-matching     $DQIX_REPO
      dqix-decomp-kit/    this kit: code, docs, skills only           $KIT
      dqix-kit-state/     everything the pipeline produces            $SP

`kitpaths.py` resolves all three, so nothing has to be set. The state directory is `$DQIX_STATE`,
else the path written in `$KIT/state.path` (`python kit_init.py --state <dir>` writes it), else
`../dqix-kit-state`. It sits outside the checkout so that no git command run in the checkout can
delete an attempt. Keep it somewhere nothing cleans automatically (not `%TEMP%`, not the decomp's
`build/`): it holds the only copy of attempts and matches.

## The decomp checkout

Use [ZevyaDev/dqix-decomp](https://github.com/ZevyaDev/dqix-decomp), branch `decomp-matching`. It
carries all matched work plus configuration the kit depends on (`tools/cc_flag_overrides.txt`,
`include/System/OverlayId.h`, the `-nodead` link). Upstream `DQIX/dqix-decomp` does not have these.

1. Fork `ZevyaDev/dqix-decomp` on GitHub.
2. Clone your fork as `origin` and add ZevyaDev as a second remote:

       git clone -b decomp-matching https://github.com/<you>/dqix-decomp.git dqix-decomp
       cd dqix-decomp
       git remote add zevya https://github.com/ZevyaDev/dqix-decomp.git

   `finish_wave.sh` pushes to `origin decomp-matching` and refuses to push from any other branch.
3. Supply your own base ROM and place it as the decomp README says: `extract/baserom_dqix_usa.nds`.
4. Optional, needed for pushing from the landing scripts: an ARM7 BIOS dump at the repo root as
   `arm7_bios.bin` (decomp README). Without it `ninja sha1` cannot pass. The integrator still
   commits locally on a green `ninja check`, but `finish_wave.sh` then stops with
   `RED: sha1 mismatch — NOT pushing`, and `integrate_fast.sh` never takes its one-build path.
5. Build:

       python -m pip install -r tools/requirements.txt ninja
       python tools/configure.py usa
       ninja min        # module checks and ROM; fetches dsd and mwccarm; no GCC or BIOS needed
       ninja report     # objdiff progress report; fetches objdiff-cli, which cov.py and countfix.py use
       ninja sha1       # only with arm7_bios.bin

   The kit's scripts always name a target (`check`, `rom`, `sha1`, `report`). Bare `ninja` also
   generates a decomp.me context for every source with GCC, which is slow. Rerun
   `python tools/configure.py usa` whenever a source file is added or removed; the landing scripts
   do that themselves.

## Python packages

    python -m pip install capstone pyelftools

| package | needed by |
|---|---|
| `capstone`, `pyelftools` | every gate, listing and diff |
| `frida` | optional: `frida/*.py` and the `pad/renum/` forcing tools |
| `pyyaml` | optional: `frida/schedforce.py` |

## kit_init.py

    python kit_init.py              # checks, state directories, OPEN_WORK.md, worker docs
    python kit_init.py --refs       # also clone and index the reference decomps
    python kit_init.py --slow       # also run regress.py --slow (end-to-end colorsweep cracks)
    python selfcheck.py             # pipeline invariants; must pass before any work

`kit_init.py` checks:

- Python 3.10+, `capstone`, `pyelftools`; notes a missing `frida`, `pyyaml` or `claude`
- `git`, `ninja`, `bash` on PATH
- `$DQIX_REPO` holds `tools/configure.py`, `config/usa/arm9/symbols.txt`, `config/usa/arm9/delinks.txt`,
  `build.ninja`, `extract/usa/arm9/arm9.bin`, and the compiler `buildcfg.py` reads out of
  `tools/configure.py`

It then creates the state directories, copies `OPEN_WORK.template.md` to `OPEN_WORK.md` if missing,
and runs `build_worker_docs.py`. It ends with `ready: run python selfcheck.py` or
`not ready: fix the FAIL lines above` and exits non-zero on failure.

All of these are under the state directory `$SP`, never under the checkout.

| directory | holds |
|---|---|
| `wip/<main\|ovNNN>/` | sources being worked |
| `staging/<main\|ovNNN>/` | gate-proven sources waiting to land |
| `handwork/` | scratch: probes, variant generators, evolve boards |
| `gated/<main\|ovNNN>/` | a copy of every source that gated MATCH |
| `clsbest/` | the best kept artifact per parked address |
| `scaffold/` | generated starting files |
| `doc_cache/` | per-function worker docs |
| `attempts/`, `quarantine/`, `hold_<module>/` | preserved attempts and sources swept aside by integration |
| `claims/` | one directory per claimed address; `claims/INTEGRATING` lists the modules being landed |
| `wlog/` | logs, per-address gate history (`wlog/gates/`), `blockers.tsv`, `levers.tsv`, priority queues |
| `priors/` | shipped: the closest saved attempt per unmatched address, listed in `priors/INDEX.tsv` |
| `refs/` | reference decompilations |

## Environment variables

| variable | default | effect |
|---|---|---|
| `DQIX_REPO` | `../dqix-decomp` | the decomp checkout, for every script |
| `DQIX_STATE` | `state.path`, else `../dqix-kit-state` | the state directory, for every script |
| `DQIX_KIT` | the working directory | where skills and workflows find the checkout when the session runs elsewhere |
| `CLAUDE_PROJECTS` | `~/.claude/projects` | where `evocap.py` and transcript readers find Claude Code sessions |
| `DQIX_KIT_BRANCH` | `main` | the kit branch `kit_update.py` pulls |
| `DQIX_PYTHON` | the interpreter running the kit | the `.sh` scripts resolve their interpreter with `kitpaths.py py`; set this to override |
| `DQIX_WINE` | the decomp's own runner | the program that executes the Win32 toolchain here. Read out of the decomp's `configure.py` (`wibo`, or whatever `-w` was given); set it to diagnose |
| `MWCC` | the build's version | `<ver>/<sub>` picks another mwccarm build for `wgate.py`, `wdiff.py` and `pad/` tools; diagnosis only |
| `WGATE_FLAGS`, `WDIFF_FLAGS` | none | extra compiler flags for one gate or diff; diagnosis only |
| `WGATE_ALLOW_PRAGMA` | unset | let `wgate.py` compile a source that carries a codegen pragma; diagnosis only |
| `WGATE_ALLOW_COMMITTED` | unset | gate an address that is already delinked (pipetest, pragma-removal experiments) |
| `WGATE_SESSION` | unset | set by `pull_worker.sh`; records gate history and prints the STALL/STOP banner |
| `WDIFF_CTX`, `WDIFF_MAXRUNS` | 1, 12 | context instructions and differing runs `wdiff.py` prints |
| `CS_SCORER` | unset | `case` scores `colorsweep.py` candidates per switch case, for big-switch functions |
| `CF_JOBS`, `CF_PAIRS`, `CF_PAIR_TOP`, `CF_BASE` | 8, off, 40, none | `frida/colorforce.py` parallelism, pair flips, pair candidates, flips carried forward |

Fleet knobs are in [FLEET.md](FLEET.md).

## Reference decomps

DQIX links the same NitroSDK, Metrowerks C runtime and BIOS syscall stubs as other DS games, and
several projects have matched that code already. `refs/VERIFIED.txt` lists the hand-checked
repositories (directory name, clone URL, note). DQIX itself is excluded.

    python kit_init.py --refs                      # shallow-clone each into refs/<name>/, then sdkident.py index
    python sdkident.py match <main|NNN> <addr>     # reference implementations ranked by instruction shape
    python sdkident.py sweep [main|NNN]            # every unmatched function, ranked by identifiability
    python sdkident.py show <proj/file> <name>     # print one reference implementation

Identification is by canonicalised instruction shape, not by name. Read the projects' notes as well
as their code: `refs/sm64ds-decomp/notes/mwccarm-codegen.md` catalogues mwccarm codegen rules by
shape class.

## Frida (optional)

    python -m pip install frida pyyaml

These tools spawn the build's `mwccarm.exe` (2.0/sp2p2) under Frida and hook its backend while it
compiles one source, so they need a system that runs the compiler natively.

| tool | hooks |
|---|---|
| `frida/colorforce.py` | register colouring: replays it, then flips one decision at a time |
| `frida/schedforce.py` | scheduler picks and dependency edges |
| `frida/schedtrace.py` | logs scheduler blocks, edges and picks |
| `frida/forcereal.py`, `frida/forcenoalias.py` | store-to-store alias edges in scheduler passes |
| `pad/renum/renum.py`, `forcemerge.py`, `vdump.py` | virtual-register numbering and coalescing |
| `pad/renum/schedwhy.py`, `pressforce.py`, `genct.py` | scheduler tie-breaks, pressure mode, basic-block split counts |
| `pad/renum/aliasdump.py`, `ifcvtrace.py` | alias queries, if-conversion decisions |

`colorforce.py` and `schedforce.py` first check that the hooked replay reproduces the native compile.
The hook addresses belong to that exact compiler binary; another mwccarm build needs new addresses.
`colorforce.py` and `renum.py` read the ROM through `forcereal.rom` and accept overlays only
(`ovNNN`); `schedforce.py` also accepts `main`.

## Keeping up to date

Decomp, on a clean tree:

    cd ../dqix-decomp
    git pull --rebase zevya decomp-matching
    python tools/configure.py usa
    ninja min

Kit, at the start of every session (`AGENTS.md` makes agents do this themselves):

    python kit_update.py
    python selfcheck.py

`kit_update.py` fetches the published kit and fast-forwards to it, then re-runs `kit_init.py` (with
`--slow` when the gate or its tests changed). It pulls nothing while `pull_all.pid`, `wave.lock` or
`claims/INTEGRATING` exists, or while tracked files have local changes or local commits diverge;
it says which and exits non-zero. A fork of the kit still updates from `ZevyaDev/dqix-decomp-kit`;
`DQIX_KIT_URL` and `DQIX_KIT_BRANCH` point it elsewhere.

Run `python build_worker_docs.py` after every edit to `worker_src/core.md`; workers read the built
docs, not the source.
