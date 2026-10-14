---
name: dqix-plan
description: Run the standing DQIX decomp plan. The agent takes ONE function at a time, round-robin through the large tiers, gates it, and either lands it or records why; the main thread cracks and automates whatever idiom is blocking. Free sweeps run in the background. Use when the user says "go ahead with the plan", "run the plan", "continue the decomp", or invokes /dqix-plan. Pass a phase name to jump (e.g. /dqix-plan free).
---

# DQIX standing plan

**Do not re-derive the findings below.** They were measured, they cost real money, and re-testing
them is the main way this project wastes a session.

**SPECULATIVE cracking is out of road. REACTIVE cracking is still the job.** The distinction matters
and it is easy to garble:

* **Reactive — keep doing this, it is Phase 3 and it is why the main thread exists.** A worker
  returns a SKIP naming a residue; you close that residue by hand and generalise it into a
  `colorsweep` rule the same session. Every crack this project owns came from a verdict in front of
  it. `020a1bb4`'s predicated early return became rule r23 on 2026-08-25 that way.
* **Speculative — stop doing this with no worker running.** Trawling the parked pool for something to
  crack has stopped paying: the free sweeps returned **0 hits across 179 parked candidates**, and the
  three named open residues each survived 250–300 `colorsweep` compiles. The biggest "idiom family"
  on the books turned out not to be one at all — of the 31 open duplicate-pool-literal functions,
  exactly ONE has a source on disk; the rest are simply undecompiled, and r13 already handles the
  shape whenever a source exists.

So: put authorised spend on a worker, and let the worker's verdicts choose what you crack. A main
thread with no worker feeding it has nothing to react to. `python $KIT/poolsize.py <mod>` sizes each
module's unmatched pool.

    KIT   the kit checkout (scripts, docs, skills): $DQIX_KIT when set, else this session's
          working directory
    SP    the state directory (attempts, logs, claims, staging): `python $KIT/kitpaths.py state`
    REPO  $DQIX_REPO, or ../dqix-decomp

## The job, in the user's words

1. **One function at a time, across the LARGE tiers WITH the XL tail open.** ONE sonnet worker,
   `PULL_BAND=large`, **`PULL_XL=1`**, a fresh session per function. It picks the next address,
   works it to a gate verdict, lands it or records why, and moves on. No batches: a batch hides
   which function is costing what. Arm a Monitor on the slot log so each verdict wakes you — see
   Phase 4 for both commands.

   **Why >256B only:** small+medium is ~7.5% of the remaining CODE (`pad/bytecov.py` prints the
   split). Not XL-only either — `claim.py` already rotates WITHIN the large band across
   `l- / l / l+ / xl / massive`, biggest first each cycle, so this one setting spreads over all of
   it. Steer by `$/byte`, not `$/match`: a 40-byte and a 4000-byte match count the same in the
   function tally.

   Each band has its own cap (`CAP_SMALL`..`CAP_MASSIVE`, see Phase 4), so a 4KB function is not
   funded like a 260B one, and since 2026-08-26 each band has its own OPEN flag as well:
   `PULL_SMALL PULL_MED PULL_LMINUS PULL_L PULL_LPLUS PULL_XL PULL_MASSIVE`, `1` or `0`, read as
   files on every claim. `STATE.md` prints them on a `bands open:` line, and
   `python $KIT/claim.py <mod> --peek 8` shows what the next claims would be without spending one.
2. **The main thread cracks and automates whatever idiom is blocking.** When a function stalls on a
   recognisable residue, close it by hand, then generalise it the same session (Phase 3). A crack
   that stays in the conversation is worth one function; the same crack as a `colorsweep` rule is
   worth every future occurrence, free.
3. **Every tool complaint is a finding.** `wlist` truncating at a literal pool, `infer` miscounting
   arity, a worker doc too big to Read — each was a tier-wide defect found by working one function
   properly. Fix the tool, then continue.

Paid workers are NOT part of this by default. Do not start `pull_all.sh` unless the user asks for it
in this session — /dqix-stop was invoked once precisely because a worker was running unasked.

**When the user's invocation DOES ask to resume the worker, the configuration is already decided —
one sonnet slot, `PULL_BAND=large`, xl open and massive closed. Set it and go; do not re-open the
band question.**

    echo 1     > $SP/PULL_SLOTS
    echo large > $SP/PULL_BAND
    echo 1     > $SP/PULL_XL
    echo 0     > $SP/PULL_MASSIVE
    : > $SP/MODEL_LARGE

**CHECK STATE.md FIRST: if it reports `drivers 1`, a fleet is already running — change the knobs and
stop there.** `claim.py` reads `PULL_BAND` and every band flag as FILES on every claim, so a knob
change takes effect on the next function with no relaunch.

The next functions are in `OPEN_WORK.md` and `wlog/evolve_queue.txt`. Strip any pragma first and
work the pragma-free source. Route a worker miss by its residue:

* REGPERM: `CF_PAIRS=1 python $KIT/frida/colorforce.py <src> ov<NNN>|main <addr> <size> [pool-off]`
  first (CPU only; pairs and groups catch rotations single flips miss, `CF_BASE` chains). Then map
  the decision to a declaration position with `pad/renum/renum.py` (core.md "A CALLEE-SAVED ROTATION").
  SCHED or order swaps: `python $KIT/frida/schedforce.py` with the same arguments. Chain them: the
  scheduler's leftovers are often register/slot swaps. A flip names the decision; turn it into
  source, put the reading on the evolve board, then `/dqix-evolve`.
* anything else: `/dqix-evolve`.
* an UNMATCHED attempt is suspect: `python $KIT/plausible.py <file>` flags volatile locals, dead
  address-taking pointers, self-assignments and invented inline-wrapper chains. Evolve's scorer
  penalises them; research briefs start from the cleaned form. A MATCH is never blocked for them.
* an evolve PLATEAU goes to a COMPILER-RESEARCH agent at once, never back into the evolve queue:
  brief it with the board and the open question, let it decompile/force the relevant mwccarm pass,
  and require a MATCH or a general rule. Group plateaus by blocker class, one agent per class.
  Colour/scheduler forcing for register or order residues; for UNDERGEN/OVERGEN the agent reads the
  backend (pyghidra MCP or Ghidra headless on `$REPO/tools/mwccarm/2.0/sp2p2/mwccarm.exe`) to learn
  what spills or skips code.

## Read nothing except what a step tells you to

Do NOT open `INVENTORY.md`, the worker docs, `core.md`, `colorsweep.py`, `ov_recover.py` or any
pipeline script at startup. Start with the commands below. Open a script only when a step fails and
you are debugging that script.

## Update the kit first

    python $KIT/kit_update.py

Before anything else, every time this plan starts or resumes. Exit 0: re-read every `RE-READ` file it
prints (this skill included), then go on. Any other exit: tell the user the line it printed; on 2 or 3
do not start or resume paid work until they answer.

## Where we are — READ THIS, do not re-derive it and do not edit it into this file

    $SP/STATE.md        GENERATED. Fleet counts, coverage in BYTES and functions, remaining work per
                        size band, HEAD, staged-but-uncommitted, selfcheck/regress, last 12 verdicts.
                        `pull_all.sh` refreshes it every 120s; `python $KIT/progress.py` rewrites it
                        on demand. Never hand-edit it and never quote a number from memory.

    $SP/OPEN_WORK.md    HAND-WRITTEN. Cracked idioms and how they were cracked, open residues with
                        what has already been ruled out, sized levers waiting for a decision.
                        Update it WHILE you work.

Never write a coverage number or a commit hash into a skill file — `selfcheck.py` fails on it.

* **Bytes, not functions, mean "how much of the game is decompiled"** — they diverge by more than
  half. STATE.md prints both plus the per-band split; steer by bytes.
* **`cov.py` reports the build's count when `build/usa/report.json` exists and a config estimate
  otherwise** — different denominators, so quote its label with the number. STATE.md always reads
  the config.
* **`selfcheck.py` and `regress.py` green is the floor.** A red means the last pipeline edit broke
  something. Run `regress.py --slow` after touching `colorsweep.py`, `wdiff.py` or `wgate.py`.

## Phase 1 — one function at a time, round-robin through the large tiers

Pick ONE address, take it to a gate verdict, land it or record why, move on. Rotate modules so no
one overlay starves; within a module take the large bands first, because that is where the
remaining coverage is and because a large function is the project's best bug finder.

    python $KIT/cov.py                          coverage, the authoritative number
    python $KIT/claim.py <mod> --status         what is already claimed; release stale ones
    python $KIT/claim.py <mod>                  the next address to work

Working one address:

    python $KIT/wlist.py <mod> <addr>           the FULL listing, never truncated
    python $KIT/scaffold.py <mod> <addr>        a starting file with callees resolved
    python $KIT/wgate.py <mod> <addr> <src>     THE verdict -- MATCH, BYTEDIFF, SIZE, WRONG-SYMBOL
    python $KIT/wdiff.py <mod> <addr> <src>     the decoded diff of only the diverging instructions

`wgate` honours `MWCC=<ver>/<sub>` and `WGATE_FLAGS`. A function that matches only under another
build or flag set means our source carries something the ROM's C never had: use the override to
locate the difference, then fix the source — never land a function behind an override. Land a
MATCH through `staging/<mod>/` and `bash $KIT/integrate_fast.sh` — never by hand-editing
`delinks.txt`.

**Stop conditions for one address.** Land it, or write the residue down and take the next one. Do
not grind: the measured cost of a stalled address is far above the cost of the next fresh one.
Record the residue so Phase 3 can generalise it.

**Never hatch to asm.** SKIP instead; hand asm is endgame residue only.

### The big function

`main:02061c04` is MATCHED; `OPEN_RESIDUES.md` has its notes. Two facts from it
apply to every function:

* **`tools/mwccarm/<dir>` names are NitroSDK versions, not compiler versions.** `2.0/sp1p5` is
  mwcc 3.0 build 131 and `2.0/sp2p2` is build 137 — decomp.me's `mwcc_30_137`. `dsi/*` are 4.0
  builds 1018-1051.
* **A decomp.me scratch fetch needs a browser `User-Agent` and a `Referer`**
  (`https://decomp.me/api/scratch/<id>`); the plain API call returns Cloudflare's 403 interstitial.

### The tools that came out of it, which apply to every function

    python $KIT/pad/casegrid.py <src> <case> <from.txt> <to-dir>   sweep one region's C, per-variant
    python $KIT/pad/findshape.py [--twonode]                       find COMMITTED sources whose ROM
    python $KIT/pad/findladder.py                                  code already has a shape you cannot
    python $KIT/pad/shapecat.py e4|e7                              produce -- their C is the answer
    python $KIT/pad/probe_cc.py <probe.cpp> --bytes                compile+disassemble a 10-line probe
    python $KIT/pad/bytemap.py <src>                               attribute a BYTEDIFF per region
    python $KIT/pad/romdis.py <addr> [n]                           disassemble unsplit code
    python $KIT/pad/caseresidue.py [src] [--all]                   WHICH case bodies differ and on
                                                                  which instruction pairs -- turns
                                                                  "27 bytes somewhere" into 2 cases
    python $KIT/pad/framemap.py <mod> <addr> <src>                 stack slots ROM vs ours, for ANY
                                                                  address: both frame sizes, every
                                                                  sp+N, and the first rank that
                                                                  diverges (= the wrong-sized object)

**Mine the corpus before guessing.** If the ROM has an instruction pairing you cannot reproduce,
search the committed sources for it: six matched files carried case `0xe4`'s shape. Map a source to
an address by its `// USA:` line as well as its filename — a filename-only map misses every
semantically-named file and will tell you a shape is absent when the corpus holds it.

**Bisect, do not guess.** Morph a known-good committed function toward the failing one one edit at a
time and read off the step where the codegen flips (`pad/probe_morph*.cpp`). That localised two
residues in an afternoon after a thousand blind variants had not.

**A standalone probe is not faithful.** Case `0xbf`'s winning shape came from a probe that omitted
the case's own tail, and applying it made the case worse. Re-measure every probe answer against the
real function with `casegrid`.

## Phase 2 — free levers, in the background, always

Zero model tokens. Launch them and leave them; they stage what closes and the wave lands it.

    for i in 1 2 3 4; do REPAIR_SHARD="$i/4" python -u $KIT/repairsweep.py > $SP/wlog/repairsweep_s$i.log 2>&1 & done
    python -u $KIT/poolsweep.py --apply > $SP/wlog/poolsweep.log 2>&1 &
    python -u $KIT/skipsweep.py --interval 90 > $SP/wlog/skipsweep.log 2>&1 &

`repairsweep` re-gates the parked pools and now writes `$SP/wlog/repair_verdicts*.txt` — the
per-address residues, which are the input to the next rule. `poolsweep` gates the `.cpp` left in
scratch directories nothing gathers from, repairs WRONG-SYMBOL and near-misses, stages the matches
and empties the pool. `skipsweep` applies the current rules to each new worker skip within ~90s.

**`pull_all` now launches `repairsweep` itself** every `SWEEP_EVERY` (default 3600s, `0` disables),
detached and never while an integration holds the tree. Nothing had run the free sweep automatically
since `run_all.sh` was retired, so it only ever ran when an operator remembered. Launch the shards by
hand only when you want the whole pool re-gated NOW — right after landing a new `colorsweep` rule.

**The parked pool is ground down: 0 hits from 179 candidates on 2026-08-25 evening.** Do not read a
sweep that stages nothing as a broken sweep — it means the remaining work is undecompiled rather than
parked. It is an argument for keeping a worker fed, NOT for skipping Phase 3: a new rule still pays,
it just pays on the functions a worker is working now instead of on the backlog.

Land what they stage with ONE build:

    bash $KIT/integrate_fast.sh      # per-module finish_wave is its fallback on red

## Phase 3 — turn each crack into an automatic rule

The only thing that bends the curve.

A residue is mechanical when the `wdiff` DIAGNOSIS says "only REGISTER NUMBERS differ", or when the
same diff offsets appear on two addresses. Close it by hand (`/dqix-hand-match <addr>`), then
**generalise in the same session**:

1. a `colorsweep.py` rewrite — keep `r17_decl_permute` LAST, it is the widest rule;
2. a `core.md` correction if the doc told workers something false;
3. a `FUNCTIONAL` entry in `$KIT/regress.py`, then `python $KIT/regress.py --slow`.

If the prior file for a `FUNCTIONAL` case is missing, **rebuild it by inverting the documented crack
on the committed match** — that is how all three were restored on 2026-08-25, and each reproduced its
documented symptom before it cracked.

Open residues with their evidence are in `OPEN_WORK.md` and `OPEN_RESIDUES.md`; the full list is
`$SP/wlog/repair_verdicts_*of4.txt`. `main:020a1bb4` and `main:020a1ccc` share diff offsets exactly,
so one crack should close both.

## Phase 4 — the steady state, and it is CURRENT (2026-08-25)

This is the operating mode: **one worker, one function at a time,
large tiers, recycled after every function — and the main thread cracks and automates whatever the
verdicts name.** The worker grinds; you turn what it finds into rules. It is Phase 1 with an agent
holding the loop instead of the main thread, and it needs the user's word before any worker starts.

    echo 1     > $SP/PULL_SLOTS      # ONE worker. Fleet count is 99% of spend; this is the knob.
    echo large > $SP/PULL_BAND       # 257+ only
    echo 1     > $SP/PULL_XL         # 2049-4096 open -- cheapest band per matched BYTE
    echo 0     > $SP/PULL_MASSIVE    # 4097+ closed -- see "the XL band" below
    : > $SP/MODEL_LARGE              # empty = sonnet, which is the standing choice
    rm -f $SP/MODEL_LARGE_LEFT $SP/STOP_PULL $SP/FLEET_STOPPED
    bash $KIT/pull_all.sh             # background, NEVER foreground

Then arm two monitors and one background watcher, so the work wakes you instead of you polling:

    Monitor:          tail -n0 -F $SP/wlog/pull_all.log $SP/wlog/pull_{main,{000..034}}_s{1..9}.log 2>/dev/null | grep -E --line-buffered " (MATCH|miss|SKIP|recycle|WARN) |HOLDING"

A `HOLDING` line means a lever is waiting for you: promote it (or decline it) right away, because
no worker claims until you do.
    Monitor:          bash $KIT/health.sh
    Background Bash:  bash $KIT/leverwatch.sh --once     (re-arm after it fires; never under Monitor)

The verdict monitor says what happened, the health monitor says when nothing is happening, and
`leverwatch` says when a worker's lever or a landed evolve crack has not reached `core.md` yet.

**The promotion is now ENFORCED, not requested.** `pull_all` runs `levercheck.py` before every claim
and HOLDS — logging `HOLDING: unpromoted lever(s)` and spawning nothing — while any lever is
unpromoted. Clear it by citing the address in `core.md`, or by writing the lever off in
`wlog/levers_declined.txt` with a reason. Doing neither stops the fleet, which is the intended
failure direction: every function claimed past an unpromoted lever pays full price to rediscover it.
The `selfcheck` invariant "the dispatcher refuses to claim while a lever is unpromoted" guards the
gate itself, so removing it fails the build.

**`leverwatch` exists because the promotion step is the one that rots.** A worker's `levers.tsv`
line is invisible to every other worker until it reaches `core.md`, and that copy was manual: twelve
notes accumulated and NOT ONE was ever copied. Writing them up is main-thread work — a note may need
a section core.md does not have yet, and a small model asked to do it either mis-files it or declines
it, which loses it while looking handled. So the TRIGGER is automated and the writing is yours: when
a line fires, promote it into `core.md` **citing the address** (that citation is what marks it done),
or record it in `wlog/levers_declined.txt` with a reason. The `selfcheck.py` invariant "every
captured lever has reached the doc workers actually read" stays red until one or the other happens,
and `STATE.md` shows that even in a session with no monitor armed.

**Three knobs are not what their names say. Measured 2026-08-25:**

* **`PULL_BUDGET` bounds nothing.** `pull_worker.sh` uses `$BUDGET` only in the log line
  (`slot $7.69/$5`). The per-slot budget was deliberately removed; the ONLY spend limit is
  `cap_for(size)` per session, one cap per size band, each a live knob file in `$SP`:

        CAP_SMALL 1.5 (<=64)   CAP_MED 3 (65-256)     CAP_LARGE 8 (257-1024)
        CAP_LPLUS 12 (1025-2048)   CAP_XL 18 (2049-4096)   CAP_MASSIVE 25 (4097+)

  Total burn = slot count x per-session cap. To spend less, lower the cap for the band you are
  serving, not `PULL_BUDGET`.
* **`MODEL_LARGE` needs a quota, and until 2026-08-25 it did nothing at all.** The spawn line read
  `--model "${PULL_MODEL:-sonnet}"` and ignored the `$MODEL` the knob computes, so every session
  logged `[opus]` and ran sonnet. Fixed. **Every opus-vs-sonnet number taken before that date is
  sonnet against sonnet — do not cite it.** To use opus deliberately:
  `echo opus > $SP/MODEL_LARGE ; echo 6 > $SP/MODEL_LARGE_LEFT` (it decrements per large claim and
  reverts itself to sonnet at zero).
* **`PULL_BAND` is read as a FILE, never from the environment.** `PULL_BAND=large python claim.py
  <mod> --next` silently falls back to mixed round-robin and serves a 20-byte function.

`claim.py` builds a round-robin over five FIXED size bands — biggest band first each cycle,
largest-first within each band, on top of least-attempted-first:

    l-  257-512     l  513-1024     l+  1025-2048     xl  2049-4096     massive  4097+

    massive open  ->  4684 3092 1884 1016 512   4668 3052 1840 1004 512
    massive shut  ->  3092 1884  960  512       3052 1840  952  512

The queue is rebuilt on every claim, so the rotation only advances because `claim.py` keeps a
PERSISTENT band cursor in `wlog/bandcur_<mod>.txt` — one band per actual claim, read without
advancing by `--peek` and `poolsize`. Before it existed, index 0 was always the biggest OPEN band's
least-attempted, largest function: four consecutive massive claims (4684, 4668, 4488, 4316) and a
sample of ZERO for the xl band it was blamed alongside. Closing a band is no longer the only way to
reach the one below it; the per-band flags are now for concentrating a sample, and `PULL_MASSIVE=0`
is the standing setting on cost grounds rather than to unblock the rotation.

Do not rebuild this and do not collapse it back to one `l` bucket. Ascending order through a single
large band is what capped the largest function ever served at **320 bytes** — which is why "the >1KB
band never converts" was a sample of zero rather than a finding. Name one band in `PULL_BAND` only
to concentrate a sample while tuning.

The pipeline already does the round-robin and the reset — do not rebuild them:

* `claim.py` hands out **ONE address per claim**, **least-attempted first**. That ordering exists
  because a deterministic queue re-served the same head-of-queue functions after every stop
  (`0204bc74` was attempted SEVEN times) and made the large band look like it converted nothing.
* `claim.py --best` picks the module each slot works, so modules rotate on their own.
* `PULL_BAND` empty = round-robin across small/medium/large. Set it to ONE band while tuning, because
  a 16-attempt window split three ways is ~5 per band and cannot tell an effect from noise.
* **Every function gets a FRESH `claude -p` session** — that is the recycle, and it is per FUNCTION,
  not per slot-budget. It matters because cost per message climbs with session length ($0.062/msg at
  10-39 messages against $0.178 past 220).
* `cap_for()` sizes each session's cap to the function, so a large function is not starved by a cap
  meant for a small one.
* **A worker writes its match into `src/`, and `pull_all` picks the module to integrate by counting
  `staging/<module>/*.cpp`.** `pull_worker.sh` now copies the file into `$STAGE` the moment the gate
  says MATCH; before that fix a match could sit untracked in `src/` forever, invisible to the
  integration trigger, until some other module's wave quarantined it as a foreign file.

**The loop you run in the main thread, per finished worker** — the verdict monitor fires, you work
the residue, the worker meanwhile starts the next function. You are never idle waiting on it:

1. Read the verdict — `$SP/wlog/pull_*_s*_*.json`, `result` field. One line to the user: addr,
   verdict, cost.
2. `python $KIT/blockercheck.py` is the queue, not the verdict prose. It ranks the MEASURED residue
   classes (`wlog/blockers.tsv`, written by `blocker.py` re-gating the preserved attempt) by pending
   count and bytes. **Work the top class, not the last function you read about.** A class counts as
   addressed once `core.md` cites the address of one member, so cracking one member frees the whole
   family; a class no recipe can reach goes in `wlog/blockers_declined.txt`. `pull_all` HOLDS while a
   class is over `BLOCKER_THRESH` (8), exactly as it holds on an unpromoted lever. A `miss` is still
   worth gating by hand before believing it: `ov017:021ab280` was logged as a $7.04 miss and was
   sitting at **BYTEDIFF 3**.
3. Turn the crack into a `colorsweep` rule + a `core.md` correction + a `regress.py` case, then
   `regress.py --slow`. A crack that stays in the conversation is worth one function; the same crack
   as a rule is worth every future occurrence, free. `pad/rulecheck.py` shows what a new rule
   proposes before a sweep spends its budget on it.
4. `python $KIT/toolgripes.py` mines the verdicts for TOOL complaints. Fix the tool, then continue —
   a 10KB function produced five tier-wide defects in one evening.

Keep an alerting Monitor on `$KIT/health.sh` for as long as a worker lives. **Stop and ask the user
if:** 10 consecutive misses, or cost per match exceeds $5 sustained. A usage limit is handled by the
pipeline, not by waiting — `pull_all` harvests the lockout free, lands it, and stops; a fresh session
resumes with `/dqix-continue`. At a WEEKLY limit, kill every worker; never spawn into a lockout.

### The XL band — open it; `massive` is what is withheld

`PULL_XL=1 / PULL_MASSIVE=0` is the standing setting (`claim.py` defaults both closed: `echo 1 > $SP/PULL_XL` opens it). **Judge a band by `$/matched-BYTE`, never
`$/match`** — coverage is ~80% by function against ~38% by byte, so `$/match` flatters small
functions by an order of magnitude and is the metric that produced the old "close the tail" advice.
`python $KIT/bandcost.py` prints both from the worker logs. Measured 2026-08-26:

    band             tried  matched     spend     $/match  $/matched-byte
    med 65-256           8        3     14.71       $4.90       $0.01916
    large 257-1024      17        3    108.89      $36.30       $0.03654

Fold in the opus window below and `massive` reads 6 attempts / 1 match / $42.53 for 4796 matched
bytes = **$0.0089 per byte, 4x cheaper than large and 2x cheaper than med** even counting four
misses. It is n=6, so treat it as a direction rather than a result — but it is the opposite
direction from what `$/match` says.

But retire the old claim that the tail never converts. Measured 2026-08-25, opus, three attempts:

    ov017:0219e384  6400B  SKIP  $7.69   frame 0x298 vs 0x1e0 -- structural, not colouring
    ov017:0218b688  5444B  SKIP  $7.80   48 bytes over on SIZE, never reached BYTEDIFF
    ov017:021d4e38  4796B  MATCH $7.72   first >1KB function ever landed (committed 89200ccc)

`021d4e38` then sat in `staging/ov017/` for a day, re-gating MATCH, while every wave silently
skipped it: `wlog/drift_ov017.txt` held `021d4e38 2`, and a drift-parked address is dropped from the
candidate set BEFORE the gate runs, so the only symptom is `committed 0` with the address never
named. **When a known-MATCH file will not land, read `wlog/drift_<mod>.txt` before re-gating
anything.** The park ages down one per wave; a count of 2 means two more waves of silence.

The two failures came BEFORE the frame section existed in `core.md`; the match came 6 minutes after
it landed. Its lever, from `wlog/levers.tsv`: `pragma opt_dead_assignments off` kept a dead cursor
increment alive, then commutative-operand order (`cmp`/`and`/`orr` take the FIRST-popped operand as
Rn). Re-open the tail deliberately when another structural lever lands — not as a round-robin
default.

## Hard rules

1. **ONE wave alive at any moment — verify it, never assume it.** A `finish_wave.sh` wrapper
   survives the death of its `ov_recover.py` child and will spawn another. Two concurrent
   `ninja check` builds on one tree is the worst state this project can reach.
2. **Never kill a `finish_wave`/`ov_recover` in its git phase.** It spends no tokens, and killing it
   mid-commit is the one action that can leave a partial commit or an empty `src/`.
3. **Launch long jobs as a harness-tracked background Bash call** (`run_in_background`), with
   `python -u` so the log is not buffered into silence. **Pass the command PLAIN — no `nohup`, no
   trailing `&`.** Wrapping it makes the outer shell exit instantly, the harness reaps the child, and
   the job dies mid-flight: that killed a `finish_wave` right after it had copied a staged match into
   `src/` and wired `delinks.txt`, leaving a stale `wave.lock` that blocks every later wave.
4. **A wait-loop or monitor must never match its own command line.** Build the pattern from
   variables, or watch a FILE instead (`until grep -q "s1 done:" $SP/wlog/pull_<mod>_s1.log`). A
   self-matching poll never exits — a `pull_worker` pattern counted 6 processes, all of them itself.
   The same trap makes `ps`-style verification lie: `fullstop.sh` reported "none running" while two
   `health.sh` monitors and eight orphaned `tail`/`grep` watchers were alive.
5. **`python $KIT/selfcheck.py` and `python $KIT/regress.py` after ANY script edit**, and
   `regress.py --slow` after touching `colorsweep.py`/`wdiff.py`/`wgate.py`. `pipetest.py` after
   touching the gate — it is the only test that can fail honestly, because it runs known-good and
   known-broken inputs through the real gate.
6. **A repair is not a repair until the gate agrees.** `autorepair` verified only its own exported
   symbol and silently broke callees.
7. **No hand asm** except the addresses in `$KIT/asm_allow.txt`. Workers SKIP instead; the integrator
   parks anything else in `asm_park_<module>/`.
8. **Never edit a shell script while a run of it is in flight.** bash re-reads by offset. Edit a copy
   and `mv` it over the original.
9. **Never write anything that matters into a clearable path.** `%TEMP%` is swept by Windows temp
   cleanup, `$REPO/build` by `ninja -t clean`. `$SP` is the durable home.
10. **A killed wave hides its own casualty in the drift list.** `ov_recover` keeps
    `wlog/drift_<mod>.txt` of functions that disturb the link layout, skips them for a few waves and
    ages the count down by one per wave. A wave killed mid-flight can put a perfectly good match on
    that list, and the only symptom afterwards is `committed 0` with the address never mentioned. If
    a known-MATCH file does not land, read that file before re-gating anything.
11. **The stop path only became true on 2026-08-25 — three scripts had rotted onto retired names.**
    `supervise.sh` was relaunching the deleted `run_all.sh`, so auto-resume after a usage limit had
    been dead since 2026-08-20 (the failure that once cost five idle days); it now launches
    `pull_all.sh` and REFUSES while `STOP_PULL` or `FLEET_STOPPED` exists, so it can no longer undo a
    `/dqix-stop`. `killfleet.sh --all` — the escalation `/dqix-stop` falls back to — killed only
    `run_all`/`run_overlay`/`run_main` and never `pull_all`/`pull_worker`, so it reported success
    with the fleet still spending.

## If this session dies or gets long

A fresh session runs `/dqix-continue`. Update `$SP/OPEN_WORK.md` **while you work**, not at the end:
it is the only reason the next session does not start from zero.
