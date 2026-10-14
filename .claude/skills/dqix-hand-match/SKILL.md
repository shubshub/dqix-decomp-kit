---
name: dqix-hand-match
description: Close a DQIX function to byte-exact in THIS session instead of spawning a worker — gate, read the residual, apply the transformation its symptom selects, land it. Use for a small residue, a function workers have already missed twice, or an idiom that is blocking a whole family, or when the user invokes /dqix-hand-match <addr>.
---

# Hand-match one function

This is main-thread work: the session doing the matching directly, no worker, no `resume_one.sh`. It
pays when the residual is small and the insight is the whole job — the small tier converts worst for
workers (35%), and cracking one idiom unlocks every function shaped like it.

    KIT   the kit checkout (scripts, docs, skills): $DQIX_KIT when set, else this session's
          working directory
    SP    the state directory (attempts, logs, claims, staging): `python $KIT/kitpaths.py state`
    REPO  $DQIX_REPO, or ../dqix-decomp

## Before anything: update the kit

    python $KIT/kit_update.py

Exit 0: re-read every `RE-READ` file it prints (this skill included). Any other exit: tell the user
the line it printed before going on.

## Then size the prize

If the goal is an idiom rather than one function, count how many unmatched functions it blocks
first, and read a function that already matched despite it — more than once that "matched example"
turned out to be asm-only, i.e. the idiom was never actually cracked. One address is worth hand
work; an idiom blocking twenty is worth a lot more.

## 1. Start from the best prior, never the scaffold

    python "$KIT/resumable.py" <addr>          # -> the best saved attempt, or empty

Empty means no prior exists; then take the scaffold (`python "$KIT/scaffold.py" <mod> <addr>
"$SP/scaffold/<addr>.cpp"`) — it has every callee and data name resolved. Also try the cheapest lever
measured on this project first: **clone a matched sibling.** All three first-try matches in the opus
window began by copying a neighbouring matched function and editing it.

## 2. Gate before reading anything

    python "$KIT/wgate.py" <mod> <addr> <file>

`wgate` is the integrator's own check, so a MATCH here is integrable. It prints exactly one of
`MATCH` / `COMPILE` / `OVERGEN(size)` / `BYTEDIFF@offsets` / `UNDEF-SYM[...]`. **Gate first, every
time** — the residual is usually far smaller than it looks, and reading the function before gating
buys context you may not need.

Then read the residual compactly:

    python "$KIT/wdiff.py" <mod> <addr> <file>      # only the diverging instructions, side by side

Never dump the full disassembly. Tool output is where the cost lives — 83.6% of worker context,
measured — and the same applies here.

## 3. Pick the transformation by symptom

Fetch the exact recipe rather than reasoning from memory:

    python "$KIT/recipe_select.py" --show "<part of the title>"

| symptom in the diff | what selects |
|---|---|
| same instructions, different registers | `colorsweep.py` — **23 meaning-preserving rewrites** now, not four. Always the cheap first try, but it is a statement-ORDER engine: on independent operations mwcc schedules the pair itself and discards the order you wrote, so an "only registers differ" diff can be beyond every rule it owns (five addresses, 250–300 compiles each, no movement) |
| an early `return K;` where the target sets K before the compare | `mov r0,#0`+`pophs` vs `movhs r0,#0`+`pophs`: rewrite single-exit — `int ok = K; if (!cond) { ...; ok = M; } return ok;`. `colorsweep` r23 does it when both returns are integer literals |
| `UNDEF-SYM` on a name that looks correct | the ROM symbol is MANGLED and your prototype says `extern "C"`. Drop the keyword. `wgate` checks UNDEF-SYM before RELOC-WRONG, so this one fault hides every later verdict — the file can already be byte-exact |
| callee-saved register order wrong | they are allocated in **reverse definition order**; register class is irrelevant |
| target branches where you emit straight-line code | change the control-flow shape (`core.md`: CONTROL-FLOW SHAPE). `#pragma optimize_for_size on` only as a diagnosis — `wgate` refuses any codegen pragma. If the TARGET is the predicated one, remove whatever made yours branch |
| target holds a constant you re-materialize | a plain local or an inline accessor (`core.md`: ROUTE A FOLDED `&global` THROUGH A PLAIN LOCAL); the pragma is a diagnosis only |
| target re-loads what you keep in a register | re-read it at the second use or read it through an accessor (`core.md`: `opt_common_subs off` closes it); the pragma is a diagnosis only |
| spill/temp mismatch | named local vs CSE temp; type width is a regalloc lever, and byte-param coercion beats source casts |
| block order differs | control-flow shape: if/else-if chains, tail sharing, diamond order |
| `UNDEF-SYM`, or a call to the wrong symbol | `extern "C"` and mangling — pool-word addresses resolve MANGLED |
| function lives in `.init` | `#pragma define_section initcode`, never asm |
| a `u64 * const` fold refuses | test `MWCC=2.0/sp2p2` to locate the difference, then fix the source; never land a function behind an override |

Try variants in a batch, not one compile at a time:

    python "$KIT/vtry.py" <mod> <addr> <base.cpp> <variants.py>    # ANCHOR + VARIANTS dict

## 4. Land it

A match that is not committed is not a match — `wgate` masks relocs, so a wrong-callee function can
pass the gate and still fail the overlay checksum.

    cp <file> "$SP/staging/<main|ovNNN>/<addr>.cpp"
    bash "$KIT/finish_wave.sh" <mod>        # background — a foreground Bash call is killed at 10 min

One module at a time. Verify the commit landed before calling it done.

Then promote what closed it, in this session: the `$SP/wlog/levers.tsv` row, and the lever turned into
a `colorsweep.py` rule, a repair, or a `core.md` rule citing the address, with its `regress.py` proof
(`$KIT/docs/IMPROVEMENT_LOOP.md` §3). The dispatcher stops claiming until it is promoted or declined.

## 5. If it will not close

* **Do not write it off.** Human-written code matched with the right transformation more often than
  not; "won't match" has been wrong here repeatedly.
* **Do not hatch asm.** The project goal is very little asm in the final product; asm is endgame
  residue only.
* **Before parking, ask whether a developer would have written your source, and backtrack.** Run
  `python $KIT/plausible.py <file.cpp>` and read the whole file for what `core.md` "A RESIDUE THAT
  WILL NOT MOVE IS POINTING AT CODE YOU INVENTED" lists. Go back to the last natural form (one typed
  struct, named members, no invented macros or casts) and try a different lever from there, even
  when it gates worse.
* Park it: `python $KIT/blocker.py <main|NNN> <addr> <file.cpp> <size>` records the measured residue
  class, and the function's handoff records every form already disproven, so the next attempt does
  not re-buy the same dead ends. Only the kit's maintainers add `worker_src/deadends.md` rows.
  `blocker.py` records a file `plausible.py` flags as `IMPLAUSIBLE` and exits 1: that is not parked.
* If a novel transformation DID close it, record it as a lever (one tab-separated line in
  `$SP/wlog/levers.tsv`: `<addr>\t<size>\t<bytediff before>\t<transformation>`) so it can be promoted
  into the worker doc — a lever that reaches workers is worth more than the single function.
