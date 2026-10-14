# Lessons

What this project measured about mwccarm and about running the pipeline. Each line is a rule with
the minimum evidence needed to trust it. The full lever catalogue is `worker_src/core.md`; grep its
headings for a symptom rather than reading it whole. Findings in `inv/*_FINDINGS.md` and
`REGALLOC_FINDINGS.md` were measured on mwccarm 2.0/sp1p5; the ROM builds with 2.0/sp2p2, so
re-measure one of those before citing it.

## Compiler and codegen

### The build is one toolchain

- mwccarm 2.0/sp2p2, game code at `-O2`, linked with `-nodead`, dsd 0.10.2. `buildcfg.py` reads
  the compiler and flags out of `tools/configure.py`, and `srcdir.py` reads each module's source
  directory out of its `delinks.txt`. A script that hardcodes either measures the wrong thing
  without failing.
- A function that matches only under another flag set, another mwccarm build or a codegen
  `#pragma` has source the ROM never had: a redundant local, a dead store, a cached value the
  original re-read. Use `WGATE_FLAGS`, `MWCC` or `WGATE_ALLOW_PRAGMA` to locate the difference, then
  fix the C and match at the default. `tools/cc_overrides.txt` and `tools/cc_flag_overrides.txt`
  stay empty; `wgate.py` refuses codegen pragmas and `selfcheck.py` ratchets the committed count.
- The MSL C runtime is the measured exception: its library code matches at `-O4,p`. A library
  function that matches only under another flag set is pinned for a maintainer decision and
  skipped; it is never configured by a contributor. In game code the same symptom means the source
  is wrong.
- Command-line `-opt noprop`, `-opt nocse` and similar switches are accepted and ignored by mwccarm
  2.0. Only the `#pragma` form changes codegen, so a flag test can falsely show "no difference".
- Copy propagation is on in the ROM. When a function looks like it needs `opt_propagation off`,
  route the access through a plain local or an inline accessor instead.

### Registers

- Callee-saved colouring follows vreg numbering: simplify scans vregs in ascending order and the
  last node pushed takes the lowest free register. Declared locals are numbered after IR temps, in
  reverse declaration order. Declaration order and definition order are therefore both levers.
- The direction flips when a long-lived value is assigned inside a nested block: the whole function
  then allocates in forward order. Read the direction off a probe
  (`python pad/probe_cc.py archive/probes/probe_alloc.cpp`) or force a numbering
  (`pad/renum/renum.py`) instead of reasoning about it.
- A local that does not keep its own vreg (loop-body narrow loads, CSE'd loads, offset splits, pool
  loads) ignores declaration order. Check with `pad/renum/vdump.py` before moving declarations.
- Scratch registers (r0-r3, ip) follow operand order of the operation that feeds them; declaration
  order does nothing there. Of two values created in the same pass, the one created later gets the
  lower register.
- `n = f(); n += g(x);` splits `n` into a short fragment that steals a low register. Keep one
  continuous live range.
- Where a loop invariant is declared (inside or outside the loop) decides where it is hoisted, and
  that decides the loop counter's register.
- Type width and signedness are register levers.
- A register residue is solvable. Run `colorsweep.py`, then `frida/colorforce.py` (single flips,
  then `CF_PAIRS=1` pairs and groups) before calling one stuck. An operand-order difference (which
  value is `Rn` of an `add`) is not colouring and needs a source change.

### Scheduling, CSE and aliasing

- Statement order is the schedule. When the diff is load order or an instruction a few slots off,
  permute the independent statements that feed it before changing any expression; declaration
  order and assignment order are separate levers.
- Where the scheduler decides a tie itself, pass 1 breaks it by slack, successors, height, operand
  class, then IR order. A constant the ROM issues before an ALU op was a loop invariant hoisted
  after pass 1; write it inside the loop. `frida/schedforce.py` names the pick.
- mwcc ends a basic block after the statement where generated pcodes pass about 100; CSE and
  scheduling stop at that boundary. `pad/renum/genct.py` prints the counts.
- An address or index computation with one consumer is folded; with two it is materialised. One law
  behind several "offset" and "missing add" residues.
- A struct declared smaller than the real object puts every access to it in the worst-case alias
  set and adds dependency edges the ROM lacks. Give structs their true size.

### Stack, constants, data

- Stack slot placement ignores declaration order and block scope. Model a stack region as one
  struct (`archive/02061c04/lowregion.py` is the worked transform); find the undersized object with
  `pad/framemap.py`. A frame of the wrong size is never a colouring problem.
- A global's true size and field layout come from every access in the image, not from the offsets
  one function touches: `python pad/objmap.py <arm9|ovNNN> 0x<addr> [more modules]`.
- A small integer loaded from the pool next to overlay loading is an overlay ID. Write
  `OVERLAY_ID(n)` from `System/OverlayId.h`; the config carries `kind:overlay_id` relocations
  (`ovidrelocs.py`). No C constant reproduces a relocated load.
- An offset difference in a literal-pool load can be a wrong constant. `pad/poolmap.py` compares
  the values.
- Three ints marshalled through `add r1, sp, #0; ldmia r1, {r1-r3}` are a 12-byte struct passed by
  value. The callee's mangled name changes with it; update `symbols.txt`.
- `bl X; bCC` with no `cmp` comes only from soft-float compares: write the float relational
  operator.
- Real C++ virtual calls: declare placeholder virtual slots so the method sits at vtable offset / 4.
- A whole-struct `*dst = *src` above the inline threshold emits a hidden out-of-line helper in a
  second `.text` section. Copy a sub-struct of the contiguous words instead.
- Predication: mwcc predicates small guarded regions by default. A `switch`, a call inside the
  block, a shared-epilogue funnel or putting the predicated side in the `then` branch forces real
  branches. core.md "CONTROL-FLOW SHAPE".
- Instructions mwcc emits from no C (core.md lists them: `ldm`/`stm` over four registers, `ip` in a
  `push` list, THUMB `stmia`, ...) mean stop permuting that expression, not that the function cannot
  match. Move up a level: callee signatures, a flag diagnosis, enumerated definition order.

### Linking and the config

- `-nodead` is forced (mwldarm takes at most twelve `-force_active`; the LCF `FORCE_ACTIVE` block is
  parsed but is not a dead-strip root). Anything mwcc emits stays in the link, except an inline
  (multidef) function nothing in its object references: the decomp's `tools/mwcc.py` empties it after
  each compile, as the original `-dead` link stripped it, and the gate tools apply the same step
  through `buildcfg.postcompile()`.
- An out-of-line `Class::Class()` emits both the complete and the `[base]` constructor; the unused
  copy shifts every later function. Define the mangled symbol as a free function
  (`extern "C" Cls* _ZN3ClsC1Ev(Cls* self)`) or leave the range to the delinked binary.
  `grep -c '\[base\]()' build/usa/arm9.o.xMAP` counts duplicates.
- A static shared across functions: write it as `inline T& GetX() { static T s = ...; return s; }`.
  It emits `_ZZ...E1s`, which links from any object; a function-body static is local and does not.
  Only one file defines the accessor; others declare `extern "C" T _ZZ...E1s;`. `integrate.py`
  wires the data range through `dataown.py`.
- `.init` functions need `#pragma define_section initcode ".init" RX` and `__declspec(initcode)`;
  compiled alone they emit an `.init` section of exactly the slot size and no `.text`.
- Delinked ranges must start and end on 4-byte boundaries. The secure-area stubs below
  `0x02000800` are `.s` files padded with their adjacent fill, listed in `asm_allow.txt`, and land
  one per wave because their drift is cumulative.
- A `0x<hex>` token in a function name breaks dsd delinking (`BAD-NAME`).
- A curated name in `symbols.txt` is binding: `dsd check symbols` fails without it, and one wrong
  name fails every candidate in the same integration.
- Pool-word function addresses resolve to mangled symbols; declare those callees as C++, not
  `extern "C"`.
- A byte-exact, relocation-correct function can still shift its module's layout. It depends on
  what else is committed, so do not strike it; `ov_recover.py` culls it at the offset where the
  `expected/actual` deltas change and retries it later.
- Overlay 29's code is typed as rodata in the config. Finishing it is a config re-typing task, not a
  source file.

### Method

- "No C form exists" has been wrong for every family it was claimed for. An idiom is asm-only only
  when an already-matched function needed asm for it. Read matched source, never count failed
  attempts.
- Size the prize before cracking an idiom: count still-unmatched functions with the shape
  (`dqtool.py count`, `pad/findmnem.py`) and read a matched one. Idiom families are small; the
  largest cluster `diffmine.py` found was five.
- A residue that will not move points at code somebody invented: a macro system for field access,
  different spellings of one access picked per site, a variable reused for two things, one object
  cast to several struct types. Rewrite it the way a developer would and re-measure, even when the
  number gets worse. The right basin beats a lower number. `plausible.py` flags volatile locals,
  dead address-taking, self-assignment and wrapper chains.
- Wrong callee signatures change argument setup and live ranges and look like a register residue.
  Run `symfix.py` and `pad/symaudit.py` before concluding anything about colouring. A dead argument
  setup in the target means the callee takes more parameters than it uses.
- Probe a law in a minimal source compiled alone (`python pad/probe_cc.py <file.cpp>`; examples in
  `archive/probes/`), then confirm it on the real function; a standalone probe can omit the context
  that decides the result.
- When source rewrites are inert, force the compiler: `colorforce.py`, `schedforce.py` and
  `renum.py` name the decision, and the source lever follows from it.
- Look for the original form before inventing one: committed sources with the same ROM shape
  (`pad/findmnem.py`, `pad/mineshape.py`, `dqtool.py find`), the reference decomps and their notes
  (`refs/sm64ds-decomp/notes/mwccarm-codegen.md`), and the original SDK or runtime source for
  library code.

## Pipeline and process

### What counts

- Only a commit proves a match. `wgate.py` checks relocation targets but cannot see layout; a staged
  file, a `gated/` copy and a MATCH log line are not matches. After the first worker MATCH of a run,
  confirm a commit.
- objdiff can refuse to count a ROM-exact function. `countfix.py` fixes the config; when the report
  and `ninja sha1` disagree, run `objdiff-cli diff -p . -u <unit>` before touching the source.
- `ninja sha1` is green from the first build, because dsd fills unclaimed ranges with the ROM's own
  assembly. Progress is `cov.py`.
- Key on the address, never the `func_` name: matched functions carry curated names, and delink
  range starts are not a set of finished functions.

### Never lose work

- Every directory the pipeline moves source into must also be one it gathers from (`staging/`,
  `hold_*`, `quarantine/`, `attempts/`, `gated/`). `scan_stranded.py`, `stage_gated.sh` and
  `resumable.py` find stranded matches.
- Preserve before deleting. An untracked worker file is the only copy of that attempt.
- Pools (`staging/`, `hold_*`) are pipeline inputs. Never put scratch there; scratch goes in
  `handwork/`.
- Never write into the decomp's `src/` by hand; the build globs it and the integrator resets it.
- Nothing important goes in a path something else clears (`%TEMP%`, the decomp's `build/`).

### Serialize and stop safely

- One integration at a time. `finish_wave.sh` holds `wave.lock`; never run `integrate.py`, even
  `--dry`, while it is held. The integrator works on the whole repo and resets the tree.
- Run integrations in the background with a long timeout and a plain command line. A foreground
  timeout that kills one mid-run leaves `src/` emptied; `git checkout -- src/` restores it.
- Never edit a running bash script: bash reads by offset and the edit lands under a live process.
  Edit a copy and `mv` it over.
- Never kill a DQIX process by hand. Use `fullstop.sh`. Under MSYS a forked subshell looks identical
  to its parent; an ad-hoc `Get-CimInstance ... -match` query matches itself. `psq.sh` excludes its
  own ancestors.
- A stop signal is a deadline, not a flag the resuming process clears. A stop that waits for a human
  to resume is a bug unless a supervisor watches it.
- Wait for a job by its process exiting, not by a word in its log. Check a log's mtime before
  trusting it; per-wave globs purge their target first.

### Verify effects, not patches

- A patch that applied is not a fix. Name the observable that changes if it works and check that,
  later, on a real run. Wired is not exercised.
- A change to a doc or prompt measured nothing unless it reached the session. Grep the transcript;
  check `truncatedByTokenCap`. Worker file reads truncate near 58 KB.
- Reading a script cannot validate it: it cannot see a missing check or a broken contract between
  two files. `pipetest.py` feeds known-good and known-broken inputs through the real gate; run it
  after touching the gate.
- A repair is not a repair until the gate agrees: re-gate after any automatic rewrite and revert if
  the verdict got worse.
- `python … | tee | tail` returns `tail`'s status; check `${PIPESTATUS[0]}`.
- A restored or recovered script is suspect until `selfcheck.py`, `regress.py` and `pipetest.py`
  pass.
- A failure record written by a broken pipeline is not evidence. Re-gate before trusting it.

### Keep it small and automatic

- Never fork a code path; parameterise it. Every fork drifted.
- A new script is finished when something runs it on a timer and `selfcheck.py` asserts its outcome.
  Add it to `INVENTORY.md`.
- After editing `worker_src/core.md`, run `build_worker_docs.py` and grep the built doc.
- Write source and scripts with an editor or the Write/Edit tools, never a shell heredoc: escapes
  such as `\b` become control bytes, and the file still parses. `selfcheck.py` rejects control
  characters.
- When several items share one expensive verification, write them all and verify once.
- Worker verdict prose is a bug report. `toolgripes.py` mines it; workers work around broken tools
  silently.
- Suspect the harness before the model: limits that fire, the doc that arrives, what the worker is
  told to give up on, and which functions it is served.
