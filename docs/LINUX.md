# Linux

Everything except the fleet runs on Linux. This is the whole of the difference from
[SETUP.md](SETUP.md): the compiler is a Win32 binary, and the scripts used to assume Windows twice
over — that `mwccarm.exe` can be exec'd directly, and that a bare `python` exists.

Tested on Debian 13, Python 3.13. The decomp itself was already Linux-capable; the kit now is too.

## What runs, and what does not

| | Linux |
|---|---|
| `wgate.py`, `wdiff.py`, `wlist.py`, `scaffold.py`, `claim.py`, `colorsweep.py`, `presweep.py` | yes |
| `classify.py`, `integrate.py`, `finish_wave.sh`, `integrate_fast.sh`, `gate_staging.sh`, `stage_gated.sh` | yes |
| `progress.py`, `countfix.py`, `culprits.py`, `relink_undefined.py`, `cov.py`, `sdkident.py`, `resumable.py`, `pad/` | yes |
| `selfcheck.py`, `regress.py`, `kit_init.py`, `kit_update.py` | yes |
| the fleet (`pull_all.sh`, `pull_worker.sh`, `supervise.sh`, `fullstop.sh`, `killfleet.sh`, `psq.sh`, the `*_watch.sh` watchers) | no — they list processes with PowerShell `Get-CimInstance` to catch Windows `claude.exe` workers |
| `frida/*.py`, `pad/renum/*.py` | no — they hook the compiler's own x86 code inside its process, at addresses that belong to that exact binary |

`selfcheck.py` skips the one fleet invariant off Windows rather than failing it: there is no fleet
here for it to be true of.

## The Win32 toolchain

`mwccarm.exe`, `mwldarm.exe` and `mwasmarm.exe` are PE binaries on every platform — including in a
Linux build of the decomp, which is why `build.ninja` invokes `./wibo "./tools/mwccarm/.../mwccarm.exe"`
here. Something has to run them. The kit does not pick a runner of its own: `buildcfg.py` reads the
one the build uses out of the decomp's `tools/configure.py` (`wibo` by default on Linux, or whatever
`-w` was given), falling back to `$REPO/wibo` and then to `wine`/`wine64` on `PATH`. Measuring on a
different compiler than the ROM was built with is how a gate ends up explaining a difference that is
not in the source, so the runner is the build's.

That runner is applied in one place. `buildcfg.launcher()` writes a two-line launcher under `$SP/bin`
and hands it back in place of the `.exe`, so every script that compiles a candidate — `wgate.py`,
`wdiff.py`, `classify.py`, `integrate.py`, `autorepair.py`, `tucheck.py`, everything in `pad/` —
passes one executable to `subprocess.run` and none of them knows the host is not Windows. On Windows
`launcher()` returns the `.exe` and writes nothing.

## Setup

Both repositories side by side; `kitpaths.py` finds the decomp at `../dqix-decomp`, or at
`$DQIX_REPO`.

    git clone -b decomp-matching https://github.com/<you>/dqix-decomp.git dqix-decomp
    git clone https://github.com/ZevyaDev/dqix-decomp-kit.git dqix-decomp-kit

A virtualenv, because the kit needs `capstone` and `pyelftools`, the decomp needs `requests`, and
Debian marks the system interpreter externally managed:

    python3 -m venv --without-pip .venv
    curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
    .venv/bin/python -m pip install capstone pyelftools ninja requests

Then the decomp, with your own base ROM at `extract/baserom_dqix_usa.nds` and, if you want
`ninja sha1` to pass, an ARM7 BIOS dump at `arm7_bios.bin`:

    cd dqix-decomp
    ../.venv/bin/python tools/configure.py usa
    ../.venv/bin/ninja extract          # the ROM -> extract/usa/arm9/*.bin the gate compares against
    ../.venv/bin/ninja check            # full build; ~10 min on 4 cores

`ninja` fetches `dsd`, `objdiff-cli`, `mwccarm` and `wibo` itself. There is no `ninja min` target on
this branch; `check`, `rom`, `sha1`, `report` and `extract` are the named ones.

Then the kit:

    cd ../dqix-decomp-kit
    ../.venv/bin/python kit_init.py --slow
    ../.venv/bin/python selfcheck.py

`kit_init.py` fails with a named reason if the runner is missing or the decomp was never extracted.

## Running the scripts

The `.sh` scripts do not assume `python` exists (Debian has `python3`, and a virtualenv that has been
activated has `python` only while it stays activated). Each resolves its interpreter once, at the
top:

    PY="${DQIX_PYTHON:-$(python3 "$KIT/kitpaths.py" py)}"
    export PATH="$(dirname "$PY"):$PATH"          # ninja lives there too

so `bash finish_wave.sh 017` works from any directory, activated or not. `$DQIX_PYTHON` overrides it.

## Two names that change with the platform, and one that does not

| tool | Windows | Linux | why it matters |
|---|---|---|---|
| `dsd` | `dsd.exe` | `dsd` | `merge_human.sh`'s stale-symbol sweep. It runs in a pipe, so the wrong name fails to an empty file and a silently skipped pass |
| `objdiff-cli` | `objdiff-cli.exe` | `objdiff-cli` | `countfix.py`, which both landing scripts call with output to a log. `FileNotFoundError` there meant countfix stopped correcting counts and the build carried on |
| `mwccarm.exe`, `mwldarm.exe`, `mwasmarm.exe` | as named | **as named** | they stay PE binaries everywhere; only the runner in front of them changes |

## Build logs

`finish_wave.sh` and `integrate_fast.sh` wrote `ninja check` output to fixed names under `/tmp`, which
is world-readable on a shared Linux host and shared between two runs of the same script. They now go
to `$SP/wlog`, with the names unchanged.

## Keeping up to date

The port lives on the `linux-port` branch of this checkout. `kit_update.py` fetches the published kit
and rebases the local commits onto it, so run it as usual and resolve conflicts as they come:

    ../.venv/bin/python kit_update.py

To track a fork instead of upstream, `DQIX_KIT_URL` and `DQIX_KIT_BRANCH` point it elsewhere.