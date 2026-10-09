# dqix-decomp-kit

Tools for matching Dragon Quest IX (Nintendo DS, USA) functions to byte-exact C++ in
[ZevyaDev/dqix-decomp](https://github.com/ZevyaDev/dqix-decomp), branch `decomp-matching`. Usable by
hand, from a single AI session, or as an autonomous fleet of Claude Code workers.

The kit compiles a candidate with the build's own `mwccarm` and flags, compares it against the
original ROM bytes, names the class of whatever still differs, applies meaning-preserving rewrites,
and lands matches through one serialized, gated path that commits to the decomp.

## Platform

Tested on Windows 11 (Git Bash, Python 3.10) and Ubuntu 24.04 (bash, Python 3.12). On Linux the
compiler runs through the decomp's own `wibo` (fetched by its build) and processes are read from
`/proc`; see [docs/SETUP.md](docs/SETUP.md#linux). The `frida/` tools stay Windows only.

## Quickstart

Clone both repositories side by side. The kit finds the decomp at `../dqix-decomp`, or at
`$DQIX_REPO`.

    git clone -b decomp-matching https://github.com/<you>/dqix-decomp.git   # your fork of ZevyaDev/dqix-decomp
    git clone https://github.com/ZevyaDev/dqix-decomp-kit.git

Build the decomp once. Supply your own base ROM and place it as the decomp README says
(`extract/baserom_dqix_usa.nds`).

    cd dqix-decomp
    python -m pip install -r tools/requirements.txt ninja
    python tools/configure.py usa
    ninja min

Initialise the kit:

    cd ../dqix-decomp-kit
    python -m pip install capstone pyelftools
    python kit_init.py --slow
    python selfcheck.py

`--slow` runs the end-to-end crack tests once; `selfcheck.py` fails until they have run.

The kit is updated continuously. Start every session with `python kit_update.py`; agents are told to
in `AGENTS.md`.

The checkout holds code only. Your attempts, logs, claims and staged matches live in a separate
state directory, `../dqix-kit-state` by default (`python kit_init.py --state <dir>` to put it
elsewhere), so no git command run in the checkout can touch them.

Match one function. Module is `main` or a 3-digit overlay; addresses are 8 hex digits without `0x`.

    export KIT="$(pwd -W 2>/dev/null || pwd)" SP="$(python kitpaths.py state)"
    python claim.py 017 --peek 5                    # next candidates in overlay 017; claims nothing
    A=021bb1a4                                      # one of them
    F="$SP/wip/ov017/$A.cpp"; mkdir -p "$SP/wip/ov017" "$SP/staging/ov017"
    python scaffold.py 017 $A "$F"                  # callees and data resolved, a stub to fill in
    python wlist.py 017 $A                          # the target, decoded from the ROM
    python wgate.py 017 $A "$F"                     # MATCH, or RESIDUE <CLASS> <metric> <detail>
    python wdiff.py 017 $A "$F"                     # only the differing instructions
    python colorsweep.py 017 $A "$F" --apply        # mechanical rewrites, stops on MATCH
    cp "$F" "$SP/staging/ov017/$A.cpp"
    bash finish_wave.sh 017                         # integrate, ninja check, commit, push

`wgate.py` and `wdiff.py` change into the decomp directory, so source paths must be absolute.
`finish_wave.sh` reverts uncommitted edits to tracked files under the decomp's `include/`, `config/`
and `src/` before it starts; commit your own changes first. [docs/WORKFLOW.md](docs/WORKFLOW.md)
explains every step.

## Layout

| path | contents |
|---|---|
| `wgate.py` `wdiff.py` `wlist.py` `scaffold.py` `residue.py` | the per-function gate, diff, listing and starting file |
| `colorsweep.py` `presweep.py` `vtry.py` `symfix.py` `fixundef.py` `autorepair.py` | mechanical rewrites and repairs |
| `claim.py` `poolsize.py` `nearmiss.py` `resumable.py` `sdkident.py` `dqtool.py` | choosing work |
| `finish_wave.sh` `integrate_fast.sh` `ov_recover.py` `integrate.py` `classify.py` `dataown.py` `countfix.py` | landing |
| `pull_all.sh` `pull_worker.sh` `supervise.sh` `health.sh` `fullstop.sh` `killfleet.sh` | the fleet |
| `selfcheck.py` `regress.py` `pipetest.py` | invariants, regression and gate behaviour tests |
| `worker_src/core.md` `worker_src/deadends.md` | the matching guide and per-address dead ends |
| `pad/` | diff, probe and search tools; `pad/renum/` compiler-numbering tools |
| `frida/` | Frida forcing of the compiler's colouring and scheduling decisions |
| `priors/` | the closest saved attempt per unmatched address, indexed in `priors/INDEX.tsv` |
| `refs/VERIFIED.txt` | reference DS decompilations to clone into `refs/` |
| `naming/` | the naming pass for matched functions; start at `naming/HANDOFF.md` |
| `merge_human.sh` `merge_port/` | upstream merges and API ports |
| `archive/` | finished experiments cited as evidence; see `archive/README.md` |
| `INVENTORY.md` | one line per script |
| `OPEN_RESIDUES.md` `REGALLOC_FINDINGS.md` `inv/*_FINDINGS.md` | recorded findings |
| `.claude/skills/` `.claude/workflows/` | Claude Code skills and workflows |

`kit_init.py` creates, under the state directory, `wip/`, `staging/`, `handwork/`, `wlog/`, `gated/`,
`clsbest/`, `claims/`, `scaffold/`, `doc_cache/`, `attempts/`, `quarantine/`, `refs/`.

## Docs

- [docs/SETUP.md](docs/SETUP.md) — prerequisites, environment, reference decomps, Frida
- [docs/WORKFLOW.md](docs/WORKFLOW.md) — one function from address to commit
- [docs/IMPROVEMENT_LOOP.md](docs/IMPROVEMENT_LOOP.md) — how every crack becomes a rule or automation the next session gets for free
- [docs/FLEET.md](docs/FLEET.md) — the autonomous pipeline, knobs, stopping, cost
- [docs/NATIVE_AGENTS.md](docs/NATIVE_AGENTS.md) — native-agent coordination and bounded crack/evolve procedures for hosts such as Codex
- [docs/LESSONS.md](docs/LESSONS.md) — compiler facts and pipeline rules learned the hard way
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — sending matches and tool fixes back

## AI agents

Run the agent with the kit root as its working directory; there is nothing to install beyond the
quickstart. [AGENTS.md](AGENTS.md) holds the instructions and hard rules every agent reads.

| agent | loads |
|---|---|
| Claude Code | `CLAUDE.md` (imports `AGENTS.md`), skills in `.claude/skills/`, the `dqix-crack` and `dqix-evolve` workflows in `.claude/workflows/` |
| Codex and other Agent Skills readers | `AGENTS.md`, and `dqix-hand-match`, `dqix-status`, `dqix-stop`, `dqix-coordinate` from `.agents/skills/`, which `kit_init.py` fills |
| any other agent | `AGENTS.md`; the per-function flow in `docs/WORKFLOW.md` needs nothing but a shell |

`dqix-plan`, `dqix-continue`, the workflows and the fleet use Claude Code features and stay Claude
Code only. Edit skills in `.claude/skills/`; `.agents/skills/` is a generated copy.
