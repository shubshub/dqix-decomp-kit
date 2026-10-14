# Matching one function

The same loop for a human at a shell and for a single AI session. The fleet runs it unattended
([FLEET.md](FLEET.md)).

## Conventions

- **Module**: `main` (ARM9 main) or a 3-digit overlay number (`017`). Directory labels under the
  kit are `main` or `ov017`.
- **Address**: 8 lowercase hex digits, no `0x` (`021bb1a4`).
- **Paths**: `wgate.py` and `wdiff.py` change into the decomp directory before opening the source,
  so pass absolute paths. From the kit checkout in Git Bash:

      export KIT="$(pwd -W 2>/dev/null || pwd)" SP="$(python kitpaths.py state)"
      export DQIX_REPO="${DQIX_REPO:-$(cd ../dqix-decomp && (pwd -W 2>/dev/null || pwd))}"
      M=017; A=021bb1a4; F="$SP/wip/ov017/$A.cpp"
      mkdir -p "$SP/wip/ov017" "$SP/staging/ov017"

The commands below use `$M`, `$A` and `$F`.

## 1. Choose an address

| command | answers |
|---|---|
| `python claim.py --pools` | modules ranked by unclaimed functions |
| `python poolsize.py $M` | unmatched functions left in one module |
| `python claim.py $M --peek 10` | what the next claims would be; claims nothing |
| `python claim.py $M` | claims the next address (a directory under `claims/$M/`) and prints it |
| `python claim.py $M --release $A` / `--status` | give a claim back / list live claims |
| `python resumable.py` | unmatched addresses with a saved attempt, closest first, including `priors/` |
| `python nearmiss.py [max_bytes]` | addresses a gate recorded within N bytes (default 16; reads `wlog/blockers.tsv`) |
| `python sdkident.py sweep [$M]` | library functions with a reference implementation (needs `kit_init.py --refs`) |
| `python dqtool.py named [$M] [maxsize]` | functions with a curated ROM name not yet matched |

`claim.py` serves a priority file first (`wlog/priority_<main|ovNNN>.txt`), then rotates through
size bands, least-attempted first. It serves nothing while `levercheck.py` or `blockercheck.py`
fails ([FLEET.md](FLEET.md)). Claims are local to your machine; to keep other contributors off your
address, see [CONTRIBUTING.md](CONTRIBUTING.md).

`priors/INDEX.tsv` lists the shipped attempts (module, address, residue class, metric). A small
metric is the cheapest work in the project: the decode is done and one transformation is usually
left.

For library code, prefer in this order: port a C reference; port a hand-written assembly reference
only when `sdkident.py` scores it EXACT; use a partial match for names, types and semantics only.
A `.s` file wrapped in `arm_func_start`/`arm_func_end` is another project's splitter output, not
evidence.

## 2. Get a starting file

    python resumable.py $A                     # best saved attempt for this address, or nothing
    python scaffold.py $M $A "$F"              # otherwise: the generated starting file

Copy a prior into `$F` if one exists. Otherwise the scaffold carries:

- an `extern` declaration for every call and pool reference, under its current committed name,
  resolved from `relocs.txt` and `symbols.txt`
- the `// USA:` tag, the `ARM`/`THUMB` macro and a stub
- a call map by offset

Argument counts and types are guesses. `infer.py` derives arity from register liveness; a `push`
of `r3` for stack alignment is not an argument.

The definition must export exactly the symbol `symbols.txt` binds at the address. The scaffold
names the stub after a curated name, and `TODO_Name_<addr>` when the bound name is a `func_...`
placeholder; rename that one. Look the name up by address, never construct it (it can carry a
different overlay number than the module you are in):

    grep -i "addr:0x$A" "$DQIX_REPO/config/usa/arm9/overlays/ov$M/symbols.txt"   # main: config/usa/arm9/symbols.txt

A `func_...` name is defined `extern "C"`:

    // USA: func_ov017_021bb1a4
    extern "C" ARM int func_ov017_021bb1a4(Foo* self, int mode) {

A mangled `_Z...` name needs a C++ definition whose signature mangles to exactly it, or
`extern "C"` on the mangled name itself.

Read matched neighbours in `$DQIX_REPO/src/` before writing anything. Starting from a matched
sibling is the cheapest lever measured on this project. Reuse existing structs from `include/`
instead of casts.

## 3. Read the target

    python wlist.py $M $A                  # full listing of the slot from the ROM, pool words marked
    python dqtool.py dis $M $A             # the same bytes, plain

Read the listing once. Iterate on `wdiff.py` output, which is a few lines, instead of re-reading
the whole function.

## 4. Write the source

- Work in `$SP/wip/<main|ovNNN>/`. Never in `$DQIX_REPO/src/`: the build compiles every `.cpp`
  under `src/`, so a half-written file breaks `ninja check` for every module, and integration
  deletes, quarantines or commits it.
- Scratch (probes, variant generators, dumps) goes in `$SP/handwork/`. Nothing else reads it.
- Write files with an editor or the Write/Edit tools, never with a shell heredoc.
- Keep the tag `// USA: func_<addr>` (main) or `// USA: func_ov<NNN>_<addr>` (overlay) on the line
  above the definition. Every tool maps a file to its address through it.
- Callees: `func_...` callees `extern "C"`; mangled callees as plain C++ declarations. Pool-word
  function addresses resolve to mangled symbols. `python symfix.py "$F"` rewrites callee
  declarations that cannot resolve to the committed symbol, gating before and after.
- Not allowed: assembly (except addresses in `asm_allow.txt`), codegen `#pragma` (only
  `define_section`, `section`, `once` pass the gate), entries in `tools/cc_overrides.txt` or
  `tools/cc_flag_overrides.txt`, and a `0x<hex>` token in any function name you invent.
- A function in an `.init` section: `#pragma define_section initcode ".init" RX` and
  `__declspec(initcode)` on the definition.
- A small integer loaded from the literal pool next to overlay loading is an overlay ID:
  `OVERLAY_ID(n)` from `System/OverlayId.h`.

## 5. Gate

    python wgate.py $M $A "$F" [section]

`wgate.py` compiles with the build's compiler and flags (`buildcfg.py` reads them out of
`tools/configure.py`), compares the object against the pristine ROM with relocation bytes masked,
and then checks size, undefined symbols, the exported symbol name, and that every call and data
relocation resolves to the address the ROM targets. Exit 0 prints `MATCH` and copies the source to
`gated/<main|ovNNN>/$A.cpp`. Exit 1 prints `RESIDUE <CLASS> <metric> <detail>` and a message. Exit 2
is `ALREADY-COMMITTED`. The section is detected from the delinks table when omitted.

Route on the class:

| class | meaning | next |
|---|---|---|
| `PRAGMA` | the source carries a codegen pragma | delete it; core.md "PRAGMAS ARE A DIAGNOSIS" |
| `NO-COMPILE` | compile error | fix the first error only |
| `UNDEF-SYM` | a referenced name exists in no `symbols.txt` | use the committed name at the callee's address; `python fixundef.py $M $A "$F"`. A mangled callee declared `extern "C"` looks like this. It is checked before `WRONG-SYMBOL` and `RELOC-WRONG` and hides them: the bytes may already match |
| `OVERGEN` / `UNDERGEN` | object size differs from the slot | fix the shape first; no register rewrite helps. Define only this function |
| `LOOP-SHAPE` | branch targets differ | a loop or guard is built differently |
| `REGPERM` | only register numbers differ | `colorsweep.py`, then the register sections of core.md |
| `SCHED` | same instructions, different order | move definitions or uses; `colorsweep.py` |
| `OPERAND` | same mnemonics, different immediates | check offsets and constants; `pad/poolmap.py` |
| `SHAPE` | mnemonics differ | the C construct is wrong; do not permute declarations |
| `WRONG-SYMBOL` | bytes match, exported name does not | define exactly the bound name (a `V` before a pointer in the mangling means `volatile`) |
| `RELOC-WRONG` | a call or data reference resolves elsewhere than the ROM's | look up the ROM's target address in `symbols.txt` |
| `ALREADY-COMMITTED` | the address is delinked already | nothing to land |
| `UNKNOWN` | `NO-SLOT` or `BAD-NAME` | the message says which |

Diagnosis-only environment: `WGATE_FLAGS="-O4"`, `MWCC=<ver>/<sub>`, `WGATE_ALLOW_PRAGMA=1`. A source
that matches only under one of them carries something the ROM's source did not; find it and match
at the default.

## 6. Read the residual

    python wdiff.py $M $A "$F" [section]

Prints only the diverging instructions, target on the left, yours on the right, relocations masked,
followed by a `DIAGNOSIS` line naming the class and the recipe numbers in core.md. `WDIFF_CTX` widens
the context.

| tool | when |
|---|---|
| `python pad/shiftdiff.py $M $A "$F"` | size off by a few instructions; aligned by edit distance |
| `python pad/framemap.py $M $A "$F"` | frame size or stack slots differ; names the object whose size is wrong |
| `python pad/poolmap.py $M $A "$F"` | pool offsets differ; compares the VALUES each load reads (a wrong constant shows as an offset diff) |
| `python pad/objsize.py "$F"` | a file defining several functions |
| `python pad/objmap.py <arm9\|ovNNN> 0x<addr>` | a global's field offsets and widths from every access in the image, for its true struct layout |
| `python pad/findmnem.py "<regex>" ["<regex>" ...]` | committed sources whose ROM code has the same instruction run |
| `python pad/mineshape.py '<regex>' [window]` | the same over a window of consecutive instructions |

## 7. Mechanical rewrites and forcing

    python colorsweep.py $M $A "$F" [--depth N] [--budget N] [--apply]

A beam hill-climb over meaning-preserving rewrites (operand swaps, compound-assignment flips,
declaration moves, statement swaps, field re-reads, ...), each
scored by `wdiff.py`. Defaults: depth 3, budget 80 compiles. `--apply` writes the best improvement
back to the file. Exit 0 = MATCH, 2 = improved or unchanged, 1 = the base does not compile. Run it
after each new form you write, not repeatedly on the same one.

| tool | use |
|---|---|
| `python vtry.py $M $A <base.cpp> <variants.py>` | many textual variants at once; the variants file defines `ANCHOR` and a `VARIANTS` dict |
| `python pad/permorder.py $M $A "$F" <marker.txt>` | enumerate definition order of a block of reads exhaustively |
| `WGATE_FLAGS="-O4" python wgate.py $M $A "$F"` | whether a flag set moves the residue; a diagnosis, never a landing (`flagsweep.py --only $A` does the same over the `clsbest/` artifact) |
| `python frida/colorforce.py "$F" ov$M $A <size> [pool-offset]` | flips one colouring decision at a time and prints the flip that makes the function exact (overlays) |
| `python frida/schedforce.py "$F" <main\|ovNNN> $A <size> [pool-from]` | the same for scheduler picks and dependency edges |
| `python pad/renum/renum.py "$F" ov$M $A <size> X=<rank>` | force a vreg numbering to find the declaration rank that closes a colouring flip (overlays) |

Write sizes and offsets as hex with the `0x` prefix (`0xd8`); `schedforce.py` reads them as hex
either way. A forcing tool names the compiler decision; the source change follows from
it ([LESSONS.md](LESSONS.md), core.md). See [SETUP.md](SETUP.md) for Frida.

When a residue resists every form, read [LESSONS.md](LESSONS.md) "A residue that will not move".
The full catalogue of levers is `worker_src/core.md`; grep its headings for the symptom.
`worker_src/deadends.md` lists forms already ruled out per address.

## 8. Clean the source

    python pad/decomment.py "$F" "$F.clean" && mv "$F.clean" "$F"
    python wgate.py $M $A "$F"

`decomment.py` strips every comment except the `// USA:` tag. Re-gate after it. Write source a
developer would have written: typed structs with named members rather than casts and invented
macros, the conventions of neighbouring files, `#include <globaldefs.h>` first.

## 9. Land

Landing is not the end: record the lever and promote it as in
[IMPROVEMENT_LOOP.md](IMPROVEMENT_LOOP.md). The dispatcher stops claiming until you do.

One module:

    cp "$F" "$SP/staging/ov017/$A.cpp"
    bash finish_wave.sh $M

`finish_wave.sh <main|NNN>`:

1. takes `$SP/wave.lock` (waits up to 5 hours; clears a lock whose recorded owner is dead) and moves
   the integration worktree (`integ_tree.py sync`) to the tip of `decomp-matching`; every later step
   runs there, never in the decomp checkout
2. drops skiplisted addresses from staging; **reverts every uncommitted change to tracked files
   under `include/`, `config/` and `src/` of the integration worktree**; moves untracked `.cpp` of other modules to
   `quarantine/`; drops staged files whose address is already committed; copies the rest of
   `staging/<module>/` into the module's source directory
3. runs `ov_recover.py`: snapshots every candidate to `hold_<module>/`, classifies, wires
   `symbols.txt`, `delinks.txt` and `relocs.txt` through `integrate.py`, gates with `ninja check`,
   on red culls what the log names (`culprits.py`), retries a crashed tool once, otherwise culls
   drift or bisects; commits what is green
4. runs `ninja check`, `ninja rom`, `ninja sha1`, then `countfix.py`
5. `integ_tree.py publish`: pushes the new commits to `origin decomp-matching` and fast-forwards the
   decomp checkout
6. prints one line: `OK <module>: +N delinked ...`, `RED: ...` or `FATAL: ...`

Commit any header or config change your source needs to `decomp-matching` first, with `ninja check`
green; integration builds from the last commit in its own worktree and never sees uncommitted
edits. A wave takes a few minutes, more the first time the worktree builds. From an AI session,
launch it as a background task with a long timeout and pass the command plain (no `nohup`, no
trailing `&`).

Several modules at once:

    bash integrate_fast.sh

It refuses on modified tracked files under `config/` or `src/`, sweeps untracked sources out of
`src/`, holds back secure-area stubs (`< 0x02000800`, landed one per wave), pre-classifies, wires
every module, builds once, and commits and pushes on green. On red it rolls back
(`git checkout -- config/ src/`, `git clean -fdq src/`) and falls back to `integrate_all.sh`, which
runs `finish_wave.sh` per module.

Never run either while `$SP/wave.lock` exists, and never run `integrate.py` by hand (even `--dry`)
during a wave: it shares `src/` with the wave.

If a landing is killed: `git -C "$DQIX_REPO" checkout -- src/` restores the tree; read
`wlog/rec_<module>.log` (it often committed before dying); remove `wave.lock` only after the pid in
`wave.lock/pid` is confirmed dead; look for the source in `staging/<module>/`, `hold_<module>/` and
`gated/`.

### countfix.py

objdiff can refuse to count a ROM-exact function (an absolute pool word still carrying a
`kind:load` relocation, a Thumb symbol size including its alignment pad). Both landing scripts run
`countfix.py`. By hand:

    python countfix.py --dry-run          # what would change, for uncommitted src/ units
    python countfix.py --since=<rev>      # units changed since a revision
    python countfix.py --report           # complete units the report shows below 100%
    python countfix.py --restore          # undo the last run

Exit 3 means config files changed: run `ninja check` and `ninja sha1` again before committing.

## 10. What "matched" means

A function is matched when a commit containing it passes `ninja check` (module checksums and symbol
addresses; the landing scripts also require `ninja sha1`). A `wgate.py` MATCH is necessary, not
sufficient: a byte-exact, relocation-correct function can still shift its module's layout. A staged
file, a `gated/` copy or a log line saying MATCH is not a match.

    git -C "$DQIX_REPO" log --oneline -3
    python delinked.py $A $M && echo landed
    python cov.py                          # coverage from build/usa/report.json

`ninja sha1` reproduces the whole ROM from day one because unclaimed ranges are filled with the
ROM's own delinked assembly; coverage is the progress number, not the hash.
