# DQIX decomp pipeline — what exists and why

Written 2026-08-19 after consolidating **339 scripts → 48**. If you add a script, add a line here.
`archive/` holds finished experiments kept as the evidence behind findings cited below; see
`archive/README.md`.

`selfcheck.py` enforces the rules this document depends on: every script parses, nothing invokes a
file that does not exist, nothing keys symbol lookups on the `func_` name, nothing builds a
"done" set from delink range starts, nothing points at a previous session's scratchpad.

---

## Kit

| file | job |
|---|---|
| `kitpaths.py [kit\|state\|repo]` | `KIT` (this checkout: code only), `SP` (the state directory outside it: `$DQIX_STATE`, else `state.path`, else `../dqix-kit-state`), `REPO` (`$DQIX_REPO`, default `../dqix-decomp`), `CLAUDE_PROJECTS`; every script reads its paths here, shell scripts through the CLI |
| `export_priors.py <source-sp> [-j N] [--dry-run]` | gate every saved attempt at an unmatched function in a pipeline directory's pools and keep the closest per address in `priors/<main\|ovNNN>/<addr>.cpp` + `priors/INDEX.tsv`; `resumable.py` reads `priors/` as a pool, so a fresh kit starts from the work already paid for |
| `regress_fixtures/` | the prior sources `regress.py --slow` cracks end to end |
| `kit_update.py [--hook]` | fetch the published kit and move onto it, keeping your unpublished commits on top (rebase; a conflict aborts with exit 3), print `RE-READ` for every changed agent instruction file, re-run `kit_init.py` (`--slow` when the gate or its tests changed); refuses while the fleet or an integration runs (exit 2) or while tracked files are modified (exit 3). `.claude/settings.json` runs it on SessionStart and, with `--hook`, on every Stop: exit always 0, and a JSON block makes the agent re-read changed instructions. `kitpaths.py` warns `KIT IS N COMMIT(S) BEHIND` (fetch at most every 10 min, silent while busy; `DQIX_NO_FRESHNESS=1` turns it off) |
| `prready.py kit\|decomp\|--hook` | last step before any pull request: fetches the published kit and `decomp-matching` and refuses (exit 1) a checkout behind either, a decomp change outside `src/`/`include/`/`config/`, conflict markers, an added source no `delinks.txt` names, a symbol renamed in one region but not another, any `deadends.md` change, a `core.md` citation of an unlanded address. `--hook` is the PreToolUse hook in `.claude/settings.json`: it blocks `gh pr create` (exit 2) until READY |
| `kit_init.py [--refs] [--slow]` | check dependencies and the decomp checkout, create the state directories and `OPEN_WORK.md`, build the worker docs, copy the agent-neutral skills (`PORTABLE_SKILLS`) from `.claude/skills/` to `.agents/skills/` for Codex and other Agent Skills readers; `--refs` clones and indexes `refs/VERIFIED.txt`, `--slow` runs `regress.py --slow` |
| `pick_wave.py <main\|ovNNN> [-n N] [--all] [--md]` | propose a batch of free functions ranked by SIBLING PROXIMITY (matched and already-sourced code within 0x400 bytes, counted double for source), smallest-first as the tiebreak so a wave is never led by the secure-area stubs whose link drift is cumulative; drops zero-byte and `_dup` phantom symbols; reuses `claim.py`'s own notion of free, blocked, skiplisted and worth-retrying rather than defining a second one. A PROPOSAL, never a reservation |
| `audit_batch.py <addr>... \| --file f \| --mine NAME \| --open` | SWARM-IDENTITY.md §3 made mechanical: still unmatched in the decomp, not claimed in this state directory, no open kit issue mentions it, no open decomp PR touches a file for it, and the checkout is not behind upstream. Exits 1 naming WHO holds each address. Read-only; needs no token (public API), and prints that the issue/PR checks were INCOMPLETE rather than clean when GitHub is unreachable or rate-limited. Addresses are normalised to 8 lowercase hex digits, and a token only counts if it is a function in the ROM's own `symbols.txt`, so a commit SHA is never read as an address |

---

## The autonomous pipeline

Call graph, top to bottom:

```
supervise.sh                     keeps pull_all alive across crashes and usage limits
└── pull_all.sh                  keep PULL_SLOTS slots busy (per-session cost caps by size band), integrate on a timer
    ├── levercheck.py / blockercheck.py   hold claiming while a lever is unpromoted or a blocker class is over threshold
    ├── presweep_watch.sh        sweep the addresses claim.py is about to serve
    ├── repairsweep.py           re-gate parked candidates (calls colorsweep, autorepair, fixundef)
    ├── progress.py              regenerate STATE.md
    ├── pull_worker.sh           ONE budget-bearing slot: claim, presweep, doc, session, verdict
    │   ├── claim.py             atomic address claiming, TTL reaping
    │   ├── presweep.py          colorsweep the best prior before a session is paid for
    │   ├── scaffold.py          per-address starting file with callees resolved (calls infer.py)
    │   ├── recipe_select.py     the per-function worker doc
    │   ├── nearmiss.py          near-miss resume list
    │   ├── wlist.py / wdiff.py / wgate.py    what the workers themselves run
    │   ├── gatewatch.sh         kill a session still gating after STOP
    │   ├── blocker.py           record the measured blocker class of a miss
    │   └── handoff.py           carry a miss's verdict into the next session's doc
    └── finish_wave.sh           preserve -> integrate -> gate -> sha1 -> push
        └── ov_recover.py
            ├── classify.py      TRUSTED / RISKY / BAD before spending a 6-minute gate
            ├── integrate.py     ONE integrator for every module
            │   ├── autorepair.py
            │   └── dataown.py   place the .data/.rodata/.bss a matched source defines
            └── modsize_check.py link-size preflight (main only)
```

Run by hand or from the sweeps, not from the dispatcher: `recover_sweep.sh` (zero-token harvest:
`wgate_scratch.sh`, `synth.py`, `translate.py`, `recoverable.py`, `ov_recover.py`), `irosweep.py`
(re-gate parked candidates with the IR optimizer switched off), `tucheck.py` (byte-compare a
multi-function translation unit against its ROM range), `proppurge.py` (re-match committed sources
without `opt_propagation`), `poolsize.py` (unmatched count per module), `delinked.py` (exit 0 when an address lies inside a
delinked range; `integrate_fast.sh` uses it), `plausible.py` (flag
constructs developers would not write; the evo_score penalty).

### Core (read end to end, and what was verified)

| file | job | verified |
|---|---|---|
| `supervise.sh` | relaunch `pull_all` when it is not running and no operator stop is set | parses |
| `finish_wave.sh` | the only supported path into the repo | lock now refuses instead of running unlocked |
| `ov_recover.py` | gather, classify, bisect, cull drift, commit | read in full; preserve-before-delete added |
| `integrate.py` | wire matched source into `symbols.txt` + `delinks.txt` (+ `relocs.txt` when it defines data) | identical decisions to both retired originals; landed ov026:021d8ba0 in a pre-landing worktree to a green `ninja sha1 check` |
| `dataown.py` | place a source's own data: derive each section's address from its relocations, prove bytes and relocs, give the range to the file, rename/retire symbols, rewrite interior references as `add:` | `regress.py` replays the 021d8ba0 hand landing exactly and refuses a body static (LOCALREF) and a flipped byte (BYTEDIFF) |
| `classify.py` | pre-gate verdicts so one bad function cannot red a wave; SIZE/BYTEDIFF (never COMPILE, which a crashed compiler also reports) are cached in `wlog/classify_cache.tsv` by source, compiler, flags, committed headers and classify.py itself (no cache while `include/` or `libs/` is dirty) | TRUSTED on two committed matches, SIZE on a mutation |
| `wgate.py` | THE gate: compile, byte-compare, verify relocations | MATCH on a committed function; no longer leaks objects |
| `buildcfg.py` | the compiler and flags read out of `tools/configure.py`, so no gate can measure on a toolchain the ROM is not built with | prints `2.0/sp2p2` and the build's own flag line |
| `srcdir.py` | where a module's new work goes, read out of its `delinks.txt` | resolves ov033 to `src/Filesystem/Overlay_33` |
| `wdiff.py` | decoded diff of only the diverging instructions | used continuously this session |
| `scaffold.py` | per-address starting file, callees resolved | ROM-symbol hint added and confirmed in output |
| `selfcheck.py` | 21 invariants | every new rule tested to DETECT its fault, not just pass |
| `pipetest.py` | BEHAVIOUR tests: known-good must MATCH, deliberately-broken must fail with the right reason | found 1 real gate hole and 1 false alarm on its first run |
| `regress.py` | REGRESSION tests for the scripts AROUND the gate (ranking, resume prompt, verdict parsing, placement filter, colour sweep). Each case pins a fault that actually happened | 9 cases; mutation-tested — reverting any of the three 08-20 fixes turns it red |

### Supporting (single job each, invoked by the above)

`recover_sweep.sh` zero-token harvest · `repairsweep.py` re-gate parked work ·
`skipsweep.py` watch `attempts/` and gate+colorsweep each NEW worker skip within a minute, staging
what closes (repairsweep only reaches these on a full pass, hours later) ·
`harvest_repairwork.sh` re-gate repairsweep's own HIT copies and stage the ones that still match
(they are stranded in `repair_work/`, which no integrator gathers from) ·
`fix_verdicts.py` classify a resume session from its own JSON and repair `resume_done.txt`
(the sweep used to file a `PASS` sign-off as a miss, discarding a paid-for match) ·
`land_harvest.sh` integrate what the harvest staged, one wave per secure-area stub ·
`stage_gated.sh` stage the wgate-PROVEN sources in `gated/` that are still uncommitted
(801 archived there, 90 still unlanded, and no dispatcher read the directory) · `colorsweep.py`
mechanical register-colouring rewrites · `autorepair.py` static repairs (verifies the symbol it
emits) · `fixundef.py` resolve callee names · `ovidrelocs.py [--apply]` give every pooled overlay ID its `kind:overlay_id` relocation ·
`psq.sh [--count|--list|--kind work|job]` how many DQIX jobs are ALIVE — excludes the querying
process and its own ancestors by PID, because an ad-hoc `Get-CimInstance ... -match 'finish_wave'`
matches its own command line and reports a busy machine when nothing is running ·
`procq.py [--match|--worker|--watchers ...] [--count|--list|--kill|--cpu]` the process table on
Windows (one CIM query) or Linux (`/proc`), filtered; every fleet script's process query goes
through it ·
`stagepurge.py [--apply]` drop staged sources whose address is already landed (a live delink range
covers it AND a tracked `src/` file defines it) — the ONE implementation of that test, called by
`integrate_fast.sh` before it copies anything into `src/` ·
`recoverable.py` count recoverable · `poolsize.py` unmatched count · `nearmiss.py` near-miss list ·
`wlist.py` full target listing · `build_worker_docs.py` docs ·
`modsize_check.py` link preflight · `synth.py` accessor synthesis · `translate.py` ARM->C
transliteration · `infer.py` derive facts for scaffolds · `wgate_scratch.sh` gate stray candidates ·
`stopat.sh '<date>'` sleep until the operator's authorised deadline, then `fullstop.sh --hard` (the
Windows task `dqix-fleet-hardstop` runs `hardstop.cmd` for the same deadline if this session dies) ·
`limit_guard.sh` usage-limit probe · `killfleet.sh` kill workers only · `lockout_harvest.sh`
zero-token work during a lockout · `until_reset.py` sleep to reset · `addr2mod.py` address -> module

### Porting from reference decompilations (identification is free; only porting costs tokens)

DQIX links the same NitroSDK, the same Metrowerks C runtime and the same BIOS syscall stubs as every
other DS game, and four projects have already matched that code. Four are cloned under `refs/`:
`pokediamond`, `pokeheartgold`, `pokeplatinum`, `SonicRushAdventure-Decomp`. Our `_fadd` and
`_s32_div_f` are instruction-for-instruction pokediamond's; `func_02001b2c` is MSL `__fill_mem`.

- `sdkident.py` — `index` builds a function index over `refs/` (140k definitions, asm and C flagged);
  `match <mod> <addr>` ranks reference implementations against ours; `sweep <mod> [addrlist]` does a
  whole module. Identification is by **instruction shape**, not by name: names are a weak signal
  (ours are descriptive where the SDK's are official) and 973 of 1101 unmatched main functions have
  no name at all. Mnemonics are canonicalised first, so `andeqs`/`andseq`, `sublo`/`subcc` and
  `lsr rD,rM,#n`/`mov rD,rM,lsr #n` compare equal, and literal pools are recorded rather than
  truncating the fingerprint.
- `worker_sdkport.md` — the spec agents follow. Points them at `sdkident`, then `wgate`.
- `gate_staging.sh <mod>` — gate everything in `staging/<mod>/` and move failures to
  `staging_rejected/<mod>/`, so a wave only integrates pre-gated files.
- `sweep_overlays.sh` — `sdkident sweep` over every overlay, one at a time.
- `audit_asm.py <mod> [--apply]` — THE GATE on assembly. Re-checks every staged `asm` file against
  HAND-written provenance and quarantines anything backed only by a splitter dump, or untagged.
  Run it before every wave: workflow agents do stage files outside the addresses they were given.
- `asmgen.py` — transcribes a ROM function into an `asm` block. **Only legitimate when `sdkident`
  scores EXACT against a reference that is itself assembly.** It is not a fallback for "C is hard":
  run blind over the library band it matched 5 of 320, and among those five were a game function and
  `WaitForVCountZero`, which was four bytes from a clean C match. Its predecessor `sdksweep.py`,
  which automated exactly that, is archived and must not come back.

Order of preference, most readable first:
1. the reference is **C** → port the C, adapt names and types, gate it;
2. the reference is **asm** and `sdkident` says EXACT → port their assembly, keeping their labels
   and signature;
3. the reference is asm but only CLOSE/PARTIAL → use it for names, types and semantics only, and
   decompile as C;
4. nothing found → NEEDS-RESEARCH. It goes back to the normal fleet as C. Never asm.

Address bands (`< 0x0200e000`, `>= 0x020c0000`) are where library code clusters, so they are swept
first — but they are a priority hint, not a filter. NNS graphics and sound, DWC wifi and the
filesystem sit outside them and in the overlays, so every module is swept.

### Cost measurement and dispatch

`autotune.py` $/function per batch size from real sessions (restored from `_archive` 2026-08-19 --
it is the only thing that can answer "is the current sizing optimal", and archiving it made that
question unanswerable). Measured 168h to 2026-08-19: 391 functions, $2,828.69, **$7.23/function**,
and cost per message rising with session length ($0.0623/msg at 10-39 messages, $0.1777 past 220).

**Budget-driven pull dispatch — the live dispatcher, replacing run_module's batch spawning.**
The batch driver fixed functions-per-worker, seconds-per-worker and workers-per-wave in advance, and
none of the three bounded spend: two sessions took $1,135 of one week's $2,828 without exceeding any
of them, because a fast worker burns turns well inside its time limit. The only hard limit here is
money per unit of work; everything else is a live knob.

- `pull_all.sh` — the loop. Keeps `PULL_SLOTS` slots busy, picks each slot's module by largest
  remaining pool, and runs `finish_wave` on a timer. Knobs are re-read every cycle, so they change
  without a restart. `PULL_BUDGET` is now a display figure only: spend is bounded per session by the
  size-band caps in `pull_worker.sh`. `touch STOP_PULL` ends
  it; lowering `PULL_SLOTS` just stops refilling, so running work is never killed mid-function.
- `pull_worker.sh` — one budget-bearing slot. Claims ONE address, runs a fresh session on it, reads
  the real `total_cost_usd` from `--output-format json`, decrements, repeats. The session restarts
  per function on purpose: cost per message climbs with conversation length ($0.0623/msg at 10-39
  messages against $0.1777 past 220), and re-entry is cheap now that the doc is ~5.4k tokens.
- `pull_fleet.sh` — one bounded run (`<module> <budget> <slots>`), for measuring. First live run:
  6/6 matched, $2.73, **$0.46/function** — but on 4-byte secure-area stubs, the easiest work there
  is, so that figure is not comparable to the $7.23 all-tiers baseline.
- `claim.py` — atomic claiming (`mkdir` is the primitive; a TTL returns a dead worker's address to
  the pool). `--pools` / `--best` rank modules by remaining work.

Batch size is NOT the lever it looked like: with a per-session doc cost, one function per session
pays that fixed cost per function. Pull dispatch is worth it for balance and for the spend ceiling,
not for shrinking batches.

### Operator

`progress.py` regenerates **STATE.md**, the generated answer to "where are we?" — fleet counts (by
command line AND parent, so a forked sweep is not miscounted as a second driver), coverage in BYTES
as well as functions, remaining work per size band, HEAD, staged-not-committed, selfcheck/regress,
and the last dozen verdicts. `pull_all.sh` refreshes it every `STATE_EVERY` (120s). The `dqix-plan`
and `dqix-continue` skills point at it INSTEAD of carrying a hand-edited state block ·
`cov.py` live coverage from the config (the monitor used to scrape a log that froze whenever the
fleet stopped) · `health.sh` the monitor (alerts, not heartbeats; honours `FLEET_STOPPED` and then
alerts on STRAYS instead) · `fullstop.sh [--hard|--dry]` two-tier stop: token
spenders (workers + every driver that launches them) killed immediately in one pass, CPU-only jobs
(finish_wave, integrators, sweeps) reported and left to finish unless `--hard`
· `dqtool.py` analysis subcommands (`dis obj count find named staged stubs`) · `diffmine.py` +
`sdiff.py` rank blocking idioms from real attempts · `vtry.py` score source variants ·
`ccsweep.sh` every compiler build · `flagsweep.sh` optimisation settings ·
`sweep_free.sh [nshards]` launch the free repair sweep across N shards, truncating the per-shard
logs first so a stale log cannot read as live progress · `scan_stranded.py [pools...]` list every
candidate source whose address is in no delinks range, i.e. work a pool holds and no commit does

Big-switch functions (written for `main:02061c04`, now matched; the `pad/` ones take a source path and are the starting point for the next dense-dispatch function, the `archive/` ones are that campaign's case-specific grids, probes and disproofs):
`pad/casediff.py <case>` diffs ONE case body against the ROM aligned at its own start -- the global
diff is useless once an early body is the wrong size, because every later body reads as a diff ·
`pad/reorder_cases.py <in> <out> [order.txt]` moves whole case clauses into the order the ROM's jump
table proves the original source used (numeric only by habit: DQIX put 0x99 next to 0x6d) ·
`archive/02061c04/tblsizes.py` per-case body sizes, target versus ours · `pad/probe_cc.py` compile one scratch
file and disassemble it, for testing a codegen shape without recompiling a 1500-line function ·
`pad/declperm.py <mod> <addr> <template.cpp> <decls.json> [indent]` hill-climb the order of bare
declarations substituted at `/*DECLS*/` · `pad/cf_multi.py <src> ovNNN <addr> <size> <pool_from> '<json>' [--order]`
colorforce with several `moves`/`choices` at once, printing the colouring order · `pad/findmat.py [imm]`
committed functions that keep a derived pointer in its own callee-saved register ·
`pad/shiftdiff.py <mod> <addr> <file.cpp>` edit-distance aligned diff for a function whose size is
off by a few instructions, where `wdiff` shows every later row as different ·
`archive/02061c04/casetable.py [src] [--show=c1,c2]` EVERY case's delta from ONE compile, plus the full body of
any case named -- `casediff` costs a compile per case, so the whole-function survey that finds
OVER-emitting cases was never affordable before · `archive/02061c04/headdiff.py [src] [n]` the prologue and
dispatch head, the region `casediff` cannot reach (frame size, parameter colouring) ·
`pad/fulldiff.py [src] [--list KIND]` classify EVERY differing instruction per case as
reg / sp / shape / pool -- the only way to tell one colouring decision from a hundred bugs ·
`archive/02061c04/stackmap.py [src]` each case's lowest stack slot, target against ours, which attributes a
frame gap to one object · `archive/02061c04/ccmatrix_c04.py [src]` compile with all 22 mwccarm builds and
report length, frame and distance · `archive/02061c04/hoist_c04.py <in> <out>` hoist every case-local to
function scope in a chosen order, which is what decides the stack layout ·
`pad/casescore.py <file.cpp>` per-case aligned, noise-classified score for a big-switch
candidate -- `colorsweep` uses it when `CS_SCORER=case` is set, because `wdiff`'s whole-function
alignment stops tracking progress once one case body is the wrong length ·
`archive/probes/probe_veccopy.cpp` the three struct-copy forms side by side (copy-init inlines as
ldm/stm, assignment emits an out-of-line helper, memcpy stays a call) ·
`pad/rulecheck.py <file.cpp> [rule] [n]` print what each colorsweep rule PROPOSES, one line
per candidate -- a rule whose regex is one character too greedy fires on comments and spends
the whole budget on candidates that cannot compile · `pad/ruleprobe.cpp` one instance of each
new rule's shape, which `regress.py` checks the rules against ·
`pad/ccscore.py <file.cpp>` the per-case classified score under ALL 22 mwccarm builds -- the
build is a variable to search, and scoring a build sweep by byte distance hides it ·
`pad/poolmap.py <mod> <addr> <file.cpp>` every `ldr [pc]` with the VALUE it reads in the ROM and in
our build, relocated pointers resolved through symbols.txt, loads moved by scheduling paired in
order -- a pool-offset diff can be a WRONG CONSTANT, and on `021ebb90` two were ·
`pad/mineshape.py '<regex>' [window]` committed sources whose ROM code already has a shape no source
form reproduces; the regex runs over a window of consecutive instructions, backreferences work ·
`pad/findorr.py [gap]` the accumulator-OR shape feeding a frame store, the mineshape case that
cracked r45 ·
`pad/decomment.py <in.cpp> <out.cpp>` strips every comment except the `// USA:` tag, for a hand
match about to be staged; re-gate the output ·
`pad/repool.py [--apply] [--check] [--rev R] [--exclude a,b] [paths]` re-points parked sources after
an upstream merge: renamed callees by ADDRESS (symbols.txt at R against HEAD), the declaration made
`extern "C"`, a missing prototype synthesised from R's header, a deleted type's old definition when
it is self-contained, moved includes; `merge_human.sh` runs it on every merge and selfcheck fails
while any pool file would still change ·
`pad/mapsyms.py <mod> <obj> <func> <base>` each undefined callee in a compiled object -> the ROM
reloc at the same offset -> the committed name at that address, for a WRONG callee ·
`pad/framemap.py <module> <addr> <src.cpp>` the SAME slot-by-slot comparison as `spslots`, but for
ANY address instead of only `main:02061c04` -- prints both frame sizes, every sp+N each side touches
with the widths that touch it, and the first rank where the two lists diverge, which names the
object whose size is wrong · `pad/spslots.py <file.cpp>` every distinct sp+N the ROM references against every one ours does,
printed as ROM-only / ours-only -- `stackmap` shows each case's LOWEST slot only, so an object no
case reaches at its base is invisible to it, which is the exact shape of a missing or undersized
local · `archive/02061c04/lowregion.py <src> <out>` fold 02061c04's five low-frame locals into one struct laid
out at the ROM's offsets, because mwcc ignores declaration order AND block scope when it places
them · `pad/vary.py <src> <out> <from-file> <to-file>` one-shot variant maker that refuses unless
the pattern occurs exactly once -- a silently-missed replace reads as "the compiler ignored my
change" · `archive/02061c04/e7ref.py <src> <out>` bind 02061c04's 0xe7 snapshot pointer as a C++ reference;
kept as the DISPROOF that a reference defeats address folding (it does not) ·
`pad/pragmasweep.py <file.cpp> [anchor]` score the candidate under each mwcc optimisation PRAGMA,
one compile each -- the build and the source get searched routinely and the pragma state does not,
even though `opt_propagation off` was itself worth 16 bytes on 02061c04 ·
`archive/02061c04/e7rec.py <src> <out>` move 02061c04's `rec` to function scope; kept as the DISPROOF that a
declaration (rather than a definition) starts a live range ·
`archive/02061c04/e7fnbattle.py <src> <out>` the same disproof for `battle` ·
`pad/bytemap.py <file.cpp>` attribute a candidate's BYTEDIFF to individual CASES -- wgate prints one
total and eight offsets, so a residue spread over four cases reads as one number and nobody knows
which case is worth working ·
`archive/probes/probe_alloc.cpp` what mwcc keys its callee-saved ORDER on: reverse definition order, except
that a long-lived value defined in a nested block flips the whole function to forward order ·
`archive/probes/probe_d3.cpp`, `archive/probes/probe_d3b.cpp` why an address lands in r1 or r2 (ONE IR node vs TWO), and
that mwcc's one-node split is always `C & ~0xfff` on every 2.0 build ·
`archive/probes/probe_e4.cpp`, `archive/probes/probe_e7.cpp` the two remaining 02061c04 register clusters in isolation,
each carrying its disproof set ·
`pad/pragmasweep.py` (above) is the pragma axis of the same search ·
`pad/flagsweep.py <file.cpp>` score the candidate under 29 COMMAND LINES (-proc, -O, -opt, -char,
-inline, …) -- the build and the pragmas get searched and the flags never did, though -proc and
-opt change scheduling and allocation ·
`archive/02061c04/d3sweep.py`, `archive/02061c04/d3sweep2.py` emit 512 and 40 source variants of one read-modify-write into a
SINGLE translation unit, so a whole syntactic grid costs one compile ·
`archive/02061c04/romwords.py <case-hex> [src]` the raw ROM words of one case beside ours with the encodings --
when a residue survives every axis, doubt the disassembler's rendering next ·
`archive/02061c04/regpin.py`, `archive/probes/probe_regvar.cpp` the DISPROOF that mwcc supports GCC explicit register
variables (local: "after code has been generated"; file scope: "illegal storage class") ·
`pad/casegrid.py <src> <case-hex> <from.txt> <to-dir>` swap ONE case's body against every
`to_*.txt` in a directory, compiling the REAL function each time, and report that case's
reloc-masked byte residue -- a standalone probe is not faithful (02061c04's 0xbf got WORSE when a
probe's winner was applied, because the probe omitted the case's own tail); generators for the grids
are `archive/02061c04/gen_*grid*.py` ·
`archive/02061c04/coupling.py <file.cpp>` perturb ten unrelated cases and report whether any watched case's
register names move -- proves 02061c04's naming is per-case, not whole-function ·
`pad/findshape.py [--any] [--twonode] [--all]` scan the pristine ARM9 for an `add`/`ldr` register
pairing and cross-reference every hit with the committed sources -- reading an already-matched
source that HAS the shape beats another thousand guesses, and finding that none does is itself the
answer; `--twonode` keeps only genuine two-node addresses (decoded add immediate not a multiple of
0x1000) with a real field offset ·
`symfix.py <file.cpp> [...] | --audit | --all` rewrite callee declarations that cannot resolve to the
committed symbol — an `extern "C"` declaration of a name the ROM carries mangled. The mangled name
encodes the parameter list, so the correct signature is derivable; the struct it names is forward
declared. Every rewrite is GATED before and after and rolled back if it does not compile, because a
call site written against the wrong signature must be fixed by a human, not guessed at (56
declarations landed across 11 artifacts, 18 refused on exactly that). This class is invisible until
the bytes match ·
`flagsweep.py [--only <addr>] [--sets "-O3|-O4|..."]` gate every parked artifact under alternative
COMPILER FLAG SETS. The build could always swap the mwccarm BUILD per file but never the FLAGS, so
every "no C form reaches this" verdict was measured on one configuration — and it is not one:
main:020b7ba0 is REGPERM 14 on the default and byte-exact under `-O4`, and 32 parked addresses
improve under a non-default set. A hit is a DIAGNOSIS, NOT a result: the original build used one
flag set (-O2, measured by `pad/globalflag.py`), so a function that matches only under -O4 means our
source carries something -O4 deletes. Fix the C; do not land it behind an override. The table
`tools/cc_flag_overrides.txt` exists and is read by configure.py,
wgate and classify all read ·
`ovrfix.sh <mod> <addr> [model]` works a function that only matches under a non-default compiler.
A separate lane because claim.py can never serve it: the address is COMMITTED, and the pool is
unmatched work. A session must produce a source that matches with NO override, or name the construct
the two compilers disagree on. `mwccfix.sh` asked whether sp2p2 was the game's real toolchain; it is,
and the tree is on it, so the script is in `_archive/`. NOTE the lane design lesson it left: its gate
was `ninja check` (3-6 min) and two sessions spent their whole budget on one build and edited
nothing -- a build-gated lane needs a fast inner loop, which pad/objsize.py now provides ·
`pad/objsize.py <file.cpp> [<mwcc>]` per-symbol sizes for a MULTI-FUNCTION file against the config,
in seconds. wgate compares ONE address's slot, so it structurally cannot judge a class file -- it
answers OVERGEN or WRONG-SYMBOL for reasons unrelated to the candidate ·
`pad/globalflag.py [n] [--sets ...]` ask the COMMITTED corpus which optimisation level the game was
built with, by gating byte-exact functions under alternatives. 56 of 60 compile identically under
-O4/-O3; the 4 that discriminate all match -O2 ·
`pad/permorder.py <mod> <addr> <src.cpp> <marker.txt>` exhaustively search DEFINITION ORDER for a
block of field reads that feeds arithmetic. Which value gets which register is decided by definition
order, and that is a bounded space: main:020b7ba0 -- REGPERM 14 at the default, MATCH under -O4, and
written off twice as a colouring wall -- matched at the DEFAULT flags on the 47th of 720 orderings.
Statement order, operand commuting and ROM-load-order binding all made it worse first. Enumerate
before concluding anything about colouring ·
`pad/preflight.py [<module>]` run BOTH gates over everything in staging/ and print any disagreement.
wgate and classify are independent implementations and have disagreed — ov000:0215858c gated MATCH
and its own wave rejected it as BYTEDIFF because classify could not see the per-file compiler
override. A wave costs ~6 minutes and its rejection reads like a bad match, so check before, not
after ·
`pad/symaudit.py [<file.cpp> ...]` (default: every clsbest artifact) list callee declarations that
cannot resolve to the committed symbol — an `extern "C"` declaration of a name the ROM carries
mangled, or a name that exists nowhere. No compile. This class is INVISIBLE to the gate until the
bytes already match, because wgate reports BYTEDIFF first and never reaches the link: 33 of 113 kept
artifacts carry one, and ov015:0218ee38 cost two extra rounds to it. recipe_select puts the list in
the worker doc under START HERE ·
`pad/findmnem.py <regex> [<regex> ...] [--all] [--limit N]` the general form of findshape: one
regex per consecutive instruction, matched against `mnemonic operands` over the whole disassembled
ARM9, reporting the delink range and committed source of every hit and the smallest UNMATCHED
functions carrying the shape -- use it to size an idiom family before spending on it ·
`archive/02061c04/gen_e7prod.py`, `archive/02061c04/gen_d3prod.py`, `archive/02061c04/gen_e4prod.py`, `archive/02061c04/gen_types.py`,
`archive/02061c04/gen_externs.py` the product grids for casegrid: conjunctions, pointed-to types, and the
callee signatures ·
`archive/02061c04/e7flat.py` rewrite 02061c04's 0xe7 guard as an early return; kept as the DISPROOF, and as the
worked example of casescore's `reg` count dropping only because the case went unaligned ·
`pad/shapecat.py e4|e7` find COMMITTED sources whose ROM code carries a stuck case's micro-shape --
their C is the answer; both this and findshape.py map a source to an address by its `// USA:` line
as well as its filename, because a filename-only map misses every semantically-named file and
reports a shape as absent when the corpus holds six examples ·
`archive/probes/probe_morph.cpp` bisect from a known-good committed shape to a failing case one edit at a time
and read off the step where the registers flip -- how 0xe4's trigger was localised to the mere
presence of a return value ·
`pad/findladder.py` the corrected search for 02061c04 case 0xe7's shape (a pointer derived from a
saved call result taking a LOWER callee-saved register); the first two versions matched nothing
because one required the intermediate `add` to target a callee-saved register and the other masked
the destination field out of the `mov rA, r0` test -- when a ROM scan returns zero, check it against
the target it was written for before believing it ·
`archive/probes/probe_morph2.cpp` the same bisection for 0xe7, which isolates the trigger to PARAMETER vs CALL
RESULT in a single step ·
`pad/romdis.py <start-hex> [n]` disassemble an arbitrary ARM9 range with no config entry -- the
only way to read code `symbols.txt` has no function for ·
`archive/02061c04/gen_e4explicit.py`, `archive/02061c04/gen_e4shift.py`, `archive/02061c04/gen_e4perm.py`, `archive/02061c04/gen_e4nest.py`,
`archive/02061c04/gen_d3shift.py`, `archive/02061c04/gen_e7plus.py` the casegrid generators behind those results ·
`archive/02061c04/bfsweep.py`, `archive/02061c04/bfsweep2.py` the copy-loop grid that cracked 0xbf; the second carries the
case's tail, which is what made it faithful ·
`archive/probes/probe_d3c.cpp`, `archive/probes/probe_d3d.cpp`, `archive/probes/probe_d3e.cpp` nested-member, block-boundary and
inlined-helper forms of the same address, each carrying its disproof ·
`scratch_disasm.py <src.cpp> <addr> <slot>` compile one scratch source and disassemble it as THUMB
beside the pristine ROM bytes of that address -- the thumb counterpart of `pad/probe_cc.py`, which
decodes ARM; both take the build's own compiler from `buildcfg.py` ·
`archive/02061c04/layoutsweep.py <src> [--rounds N] [--only a,b,c]` hill-climb the function-scope declaration
ORDER, or exhaustively permute a named subset · `archive/02061c04/caseperm.py <src> <case>` permute the
declarations inside ONE case, swapping only declarations that do not reference each other ·
`archive/probes/probe_copyforms.cpp` the copy forms that reach an EXISTING object (a struct whose member
is an array is the only one that inlines as ldm/stm with no guard and no helper) · `archive/probes/probe_layout*.cpp` locals of distinct sizes with
escaping addresses, to read mwcc's layout rule off the offsets instead of guessing it ·
every pad/ tool takes MWCC=<ver>/<sub> to pick the compiler build

### Upstream merge (only when pulling human decomp)

`merge_human.sh` + `fix_includes.py`, `merge_fixups.sh`, `relink_undefined.py`, `rename_symbols.py`,
`union_merge.py`

`merge_port/` — the 2026-09-15 upstream `BattleStruct`→`GameState` port, reusable for the next API refactor:
`port_gamestate.py` (type + accessor→member rewrite, prototype deletion, include swap, mangled renames in
`symbols.txt`), `fix_local_protos.py` (drop a local prototype a header now declares, cast its call sites),
`fix_header_conflicts.py` (local `extern "C"` prototype vs header: cast args to the header's types),
`fix_by_error.py` (rename a member on exactly the lines mwcc reported), `move_include.py`, `bridge_port.py`
(revert a file to HEAD and rename only its mangled bridges), `compare_report.py` (functions non-matching now
but not in a baseline report)

---

### Added after the consolidation (2026-08-20 dispatcher rework, 2026-08-25 recovery)

| file | job | verified |
|---|---|---|
| `pull_all.sh` | budget-driven pull dispatcher: `PULL_SLOTS` / `PULL_BUDGET` live knobs | superseded `run_all.sh` as the way work is dispatched |
| `pull_worker.sh` | spawn ONE worker on a claimed address, record verdict + cost | |
| `claim.py` | atomic address claiming (`mkdir`), TTL reaping, `--status` / `--release`; serves nothing (exit 3) while the kit is behind and no fleet or integration runs | |
| `regionblocks.py` | the rename used by `rename_symbols.py` and `relink_undefined.py`: inside `#if defined(jpn\|eur)` only a define's name is renamed, never its target address, and a define the region's config already binds is dropped | `regress.py` replays the JPN break |
| `pullstat.py` | per-band stats and `--alerts` | |
| `integrate_fast.sh` | clear the whole staged backlog with ONE build; on red, cull the culprits the log names and rebuild (3 rounds), then per-module fallback | |
| `culprits.py <log> [--cull]` | name the candidates a red build log blames (compile error, duplicate or undefined symbol, missing object, malformed config line, unplaced object); `--cull` moves their staged copies to `hold_<mod>` | `ov_recover` culls them before any bisection; `regress.py` covers each class |
| `integ_tree.py path\|sync\|publish\|report` | the worktree integrations run in (`$DQIX_INTEG`, default `<decomp>-integ`; `off` = in place): created on first use, moved to the `decomp-matching` tip, pushed, main checkout fast-forwarded | `finish_wave.sh`, `integrate_fast.sh` |
| `wavelock.sh` | `wave_lock_acquire <tries>` / `wave_lock_release`, sourced by both integrators | |
| `regionsync.py [tree]` | after a landing, port matched files from `config/usa` to every region whose port tool has `--sync` (EUR: `tools/port_eur_config.py`; JPN: `tools/port_jpn_config.py`, which reads the JPN objects, so JPN is built first) and whose ROM is extracted; commit the port when that region's `ninja check` and `sha1` pass; when not, restore the modules whose check failed and build again, and restore the whole region when still red; run by both integrators | `selfcheck.py` holds that both run it; `regress.py` holds that a red module does not cost the others their port |
| `evocap.py <addr>` | spend and progress cap for `/dqix-evolve`: sums every evolve run's cost from the workflow journals and agent transcripts, and prints `EVOCAP STOP` once the address has spent `EVOCAP_USD` (default 150) or its latest `EVOCAP_FLAT` (default 2) runs found no better best than earlier runs. The workflow's scorer runs it every generation and stops on STOP; `force: true` bypasses it | replayed: stops 021d8ba0 after run 2 of 9 and 020042a8 after run 1; passes 0205faf4, 0219e384, 021d8c30, 02065990, 02061c04 |
| `countfix.py` | make objdiff count a ROM-exact landing: an absolute pool word gets `module:none` in `relocs.txt`, a Thumb symbol size drops its alignment pad. Default = uncommitted `src/` units, `--since=REV`, `--report`, `--dry-run`, `--restore`. Both landing paths call it; exit 3 means config changed and must be re-gated | 33a3ff65 took the report from 11932 to 11963 functions |
| `purge_skiplisted.py` | drop skiplisted addresses out of staging before an integration | run clean before every integrate |
| `mkresume.py` | build a resume prompt that hands over prior verdicts without authorising a skip | `regress.py` pins the ruled-out block (needs `wlog/` verdicts) |
| `recipe.py`, `recipe_select.py` | build the per-function worker doc, under the Read truncation limit; preserves the handoff block across regeneration | |
| `handoff.py` | write a failed session's verdict into its function's doc under `## HANDOFF FROM THE PREVIOUS SESSION ON THIS ADDRESS` ... `<!-- END HANDOFF -->`, so a function too large for one context window continues instead of restarting from the scaffold | `pull_worker.sh` miss path |
| `levercheck.py [--verbose\|--keys]` | fail while a lever has never been promoted into `worker_src/core.md` or `deadends.md`: a `wlog/levers.tsv` row, or a LANDED function whose `handwork/<addr>_board.md` carries an evolve `RULE:`/`MATCH` line; promoted = a doc CITES THE ADDRESS, declined via `wlog/levers_declined.txt`; `--keys` prints `<addr> <text>` per unpromoted one; `LEVERCHECK_*` env overrides for testing | `selfcheck.py`, the claim gate in `pull_all.sh`/`claim.py`, `leverwatch.sh` |
| `leverwatch.sh [--once] [interval]` | emit one line per unpromoted lever that `levercheck.py --keys` reports (a worker's `levers.tsv` row, or a landed evolve board whose address `core.md` does not cite), once EVER per address (announced set kept in `wlog/leverwatch_seen.txt`, `LEVERWATCH_SEEN` overrides it); `--once` exits after the first firing | armed as a BACKGROUND Bash with `--once` by `/dqix-plan` and `/dqix-continue`; under Monitor it idles out every 30 minutes |
| `nearmiss_watch.sh [interval]` | regenerate the near-miss priority queue from `blockers.tsv` on a timer, so it does not decay as verdicts land; honours `wlog/nearmiss_reserved.txt` | belongs in `pull_all.sh` next to the repair sweep at the next quiesce |
| `presweep.py <mod> <addr>` | run colorsweep on the address's best artifact BEFORE a worker is spawned; prints `MATCH <file>` (no session needed) or `IMPROVED a b` after replacing `clsbest` | called by `pull_worker.sh` between the claim and the doc build |
| `verdictwatch.sh [interval]` | emit one line per worker MATCH/miss across EVERY module, by re-globbing the per-module slot logs and remembering each file's byte offset; a fixed tail is blind to whichever module the dispatcher picks next | arm as a Monitor whenever a fleet runs |
| `presweep_watch.sh [interval]` | sweep the addresses `claim.py` is about to serve, so the claim-time call finds a marker and returns instantly instead of making a slot wait | run detached alongside `pull_all.sh` |
| `permute.py` | source permutation helper for the sweeps | |
| `transweep.py` | gate `translate.py` output, rebind `Trans_<addr>` on WRONG-SYMBOL | |
| `toolgripes.py` | mine worker verdicts for TOOL complaints | |
| `integrate_all.sh` | run `finish_wave` for every module that has staged work, biggest backlog first | the per-module fallback `integrate_fast.sh` calls on a red combined build |
| `resumable.py` | rank every unmatched address that already has a saved attempt; `skips()` is the verdict reservoir | reads `wlog/*.log`, so it depends on the rebuilt attempt history |
| `poolsweep.py` | gate the .cpp left in ungathered scratch pools and stage the matches | written 2026-08-25; addresses read from the `// USA:` tag |

### The blocker loop (2026-09-06) — a worker stops on a MEASURED verdict, and the class is the queue

The PASS side already had a durable loop: `levers.tsv` -> `levercheck` -> `core.md`. The miss side had
none. `SKIP <free text>` put every failure in one bucket, so "regalloc" covered at least five
different cracks and nothing could rank them; and the only documented stop rule ("colorsweep has run
and the diff has not moved for 3 gates") presupposes a compiling, size-exact artifact, so a session
that never reached one had no rule in scope and stopped on model judgment alone — 45 turns in one
case, 193 in another, same configuration.

| file | job | verified |
|---|---|---|
| `residue.py` | classify a residue into one fixed class: `NO-COMPILE OVERGEN UNDERGEN LOOP-SHAPE REGPERM SCHED OPERAND SHAPE`. `wgate`, `wdiff` and `blocker` all route through it, so there is one classifier, not three | `020b25c4` -> LOOP-SHAPE 47, `0205f9cc` -> SCHED 10, matching what both were documented as |
| `gatelog.py` | per-address gate history (`wlog/gates/<mod>_<addr>.tsv`), and the phase-aware stall test over it. `python gatelog.py <mod> <addr> <session>` prints `STALL <phase> <n>` or `OK` | STOP fires on the 5th unchanged gate; a TIE is not an improvement (the first version scored ties as progress and could never stall) |
| `blocker.py` | record what actually blocked a function, by re-gating its best file rather than believing the worker's prose. Appends to `wlog/blockers.tsv` | run on three real attempts; a missing file records `NO-ARTIFACT` |
| `blockercheck.py` | rank classes by pending count and hold the dispatcher while one is over threshold. A class is ADDRESSED when `core.md` cites the address of one member — crack one, the family is free. Decline in `wlog/blockers_declined.txt` | holds at `BLOCKER_THRESH=1`, releases on a cited member |
| `gatewatch.sh` | backstop: kill a session still gating `GATEWATCH_GRACE` seconds after the STOP banner | `gatelog.py` reports STALL, which is what it polls |

Phases and their stall limits, all env knobs: `STALL_NOCOMPILE` 6, `STALL_SIZE` 6, `STALL_DIFF` 4.
`wgate` counts a gate only when `WGATE_SESSION` is set, which `pull_worker.sh` prefixes onto the
`claude` invocation alone — every sweep and every post-session verification gate is therefore
uncountable and cannot trip a false stall.

## Rules that keep being violated

1. **Never fork a module code path.** Every fork drifted and each twin ended up with fixes the other
   lacked. Three pairs were merged on 2026-08-19; `ov_recover.py` says "never fork this" for a reason.
2. **Key on the ADDRESS, never the `func_` name.** 146 functions carry curated ROM symbols and are
   invisible to a name-keyed scan.
3. **A curated name in `symbols.txt` is binding.** Renaming it deletes a symbol `dsd` requires and
   reds the whole build.
4. **Preserve before deleting.** Every untracked worker file is the only copy of that attempt.
5. **Never edit a running bash script.** Bash reads incrementally; use the `USAGE_LIMIT_STOP`
   graceful stop, then swap the file.
6. **Verify the effect, not the patch.** A repair that compiles can still emit the wrong symbol; a
   check that passes may be incapable of failing.
7. **Reading a script cannot validate it.** Line-by-line review confirms a file does what it says.
   It cannot see a MISSING check (`wgate` never verified its exported symbol — there was no wrong
   line to find), it cannot see a broken CONTRACT between two files (`translate.py` names its output
   `Trans_<addr>`, correct alone, fatal against `symbols.txt`), and it cannot tell a correct comment
   from a confident wrong one (`sanitize` stripped `0x800C` from a real symbol exactly as documented).
   All three reached waves after a full review. Only `pipetest.py` — running known-good and
   known-broken inputs through the real gate — can fail honestly, so run it after touching any gate.

### Added for the kit

`pad/objmap.py <module> <0xsymbol_addr> [more_modules...]` reconstruct a global object's field layout
from every load and store off its address in the image -- the evidence for a struct's offsets and
widths, instead of a guessed `char pad[N]` ·
`bandcost.py` cost per size band from the worker logs, in `$/match` and `$/matched-byte` -- judge a
band on the second ·
`naming/` the naming pass (name every identifier in matched functions); `naming/HANDOFF.md` first
