# The improvement loop

A match is not finished when the bytes agree. It is finished when the pipeline can produce the next
match of the same shape without anyone rediscovering it. Every session, fleet worker or human, feeds
the loop below, and the dispatcher refuses to hand out new work while a recorded lever has not been
written back.

    measure ──► record ──► promote ──► enforce ──► deliver ──► (next session starts further on)
    wgate       levers.tsv  core.md     levercheck   build_worker_docs
    residue     blockers    colorsweep  blockercheck recipe_select
                deadends    regress     selfcheck

## 1. Measure

`wgate.py` ends every run with `RESIDUE <CLASS> <metric> <detail>`, classified by `residue.py`:
`NO-COMPILE OVERGEN UNDERGEN LOOP-SHAPE REGPERM SCHED OPERAND SHAPE`. The class, not the prose, is what
gets recorded and ranked. Under the fleet, `gatelog.py` keeps each address's gate history and prints
`STOP` once the residue stops moving; that is the point to record and end.

## 2. Record — what every session writes

| outcome | write | format |
|---|---|---|
| MATCH | one row in `$SP/wlog/levers.tsv` | `<addr>\t<size>\t<bytediff just before the fix>\t<the transformation that closed it>` |
| miss | the measured blocker | `python $KIT/blocker.py <main\|NNN> <addr> <file.cpp> <size>` appends to `$SP/wlog/blockers.tsv` |
| miss | what the next session on this address needs | `## HANDOFF FROM THE PREVIOUS SESSION ON THIS ADDRESS` … `<!-- END HANDOFF -->` in the function's doc; `handoff.py` carries it forward |
| real budget spent ruling forms out | the handoff above; maintainers also add one row in `$KIT/worker_src/deadends.md` | `<addr>\t<what was tried against the real gate, and failed>` |
| a tool misbehaved | say so plainly in the verdict | `toolgripes.py` mines verdicts for tool complaints |

Name the transformation concretely: "bind the call result to its own local declared below the loaded
one", never "fixed regalloc". `pull_worker.sh` gives fleet workers these instructions and runs
`blocker.py` and `handoff.py` itself; a hand session (`/dqix-hand-match`) writes the same files.

## 3. Promote — where a lever goes

Promote in the same session that found it. Pick the most automatic home that fits:

| the lever is | it becomes | proof required |
|---|---|---|
| a source rewrite a script can apply mechanically | a `colorsweep.py` rule (`rNN_<name>`, added to `RULES`; `r17_decl_permute` stays last, it is the widest) | an instance in `pad/ruleprobe.cpp`; a `FUNCTIONAL` case in `regress.py` with its prior in `regress_fixtures/`; `python regress.py --slow` green; `python pad/rulecheck.py <file.cpp> <rule>` shows it proposes only what it should |
| a fault in a compiling source the tools can repair (wrong symbol, `extern "C"` on a mangled callee, unresolved callee) | `autorepair.py`, `symfix.py` or `fixundef.py` | a `regress.py` case that fails without the fix |
| a wrong fact in a starting file | `scaffold.py` / `infer.py` | regenerate one scaffold and check it |
| a tool defect | a fix in the shared tool, never a private workaround | a `regress.py` case; `pipetest.py` after touching the gate |
| a judgement a worker has to make | a rule in `worker_src/core.md` under the heading for its symptom, citing the address | the landed source with only that construct reverted, gated with `WGATE_ALLOW_COMMITTED=1`, no longer matches (if it still matches, the lever is not the cause: decline it); `plausible.py` is clean on the form the rule teaches; `python build_worker_docs.py`, then grep the built `worker_*.md` for the text |
| something ruled out, with no general rule | a `deadends.md` row | — |
| not worth promoting | a decline: `<addr> <reason>` in `$SP/wlog/levers_declined.txt` | — |

A `core.md` rule states the rule and the address it was found on, in a few lines. No history, no
narrative: workers read it on every function.

## 4. Enforce — the loop cannot be skipped

- `levercheck.py` fails while any `levers.tsv` row, or a landed function whose
  `$SP/handwork/<addr>_board.md` carries an evolve `RULE:`/`MATCH` line, has an address that neither
  `core.md` nor `deadends.md` cites. Promoted means cited; declined means listed in
  `levers_declined.txt`.
- `blockercheck.py` fails while one residue class has `BLOCKER_THRESH` (default 8) pending rows in
  `blockers.tsv` recorded after the last `core.md` citation of one of its members. A class is
  addressed when `core.md` cites one member: crack one, the family is free. Decline a class with
  `<CLASS> <reason>` in `$SP/wlog/blockers_declined.txt`.
- While either fails, `claim.py` serves nothing and `pull_all.sh` stops refilling slots; integration
  carries on. `leverwatch.sh` announces each unpromoted lever once; `blockercheck.py --verbose` ranks
  the classes.
- `selfcheck.py` holds the invariants: every captured lever has reached the doc workers read, and
  the dispatcher refuses to claim while a lever is unpromoted or a blocker class is over threshold.

## 5. Blockers are families

A blocker class is worth cracking once for all its members, so size it before spending:
`python $KIT/pad/findmnem.py <regex> ...` finds every function carrying an instruction shape, matched
or not. Shape families are small, typically 3–5 functions, so crack the cheapest member first.

Never park an idiom because it is rare. Escalate instead:

1. `/dqix-hand-match <addr>`, routing on the residue class (`core.md`, `docs/WORKFLOW.md` §6–7);
2. the `dqix-crack` or `dqix-evolve` workflow (capped by `evocap.py`);
3. on a plateau, ask the compiler: `frida/colorforce.py`, `frida/schedforce.py` and `pad/renum/`
   show which decision differs and what source change steers it.

Two stops are legitimate: a match that exists only under different compiler flags (a question for the
decomp maintainers, recorded in `skiplist_main.txt` / `skiplist_ov.txt`), and a BIOS syscall stub with
no C form (listed in `asm_allow.txt`).

## 6. Deliver

`build_worker_docs.py` assembles `worker_*.md` from `worker_src/`; `recipe_select.py` builds each
function's doc, injecting that address's `deadends.md` row and its earlier attempts. A rule that is
in `core.md` but not in the built doc has not been delivered, so grep the built doc.

## One crack, start to finish

1. Gate to `MATCH`; land it (`docs/WORKFLOW.md` §9).
2. Append the `levers.tsv` row.
3. Promote the lever into the most automatic home in §3, with its proof.
4. `python $KIT/selfcheck.py` and `python $KIT/regress.py` (`--slow` if `colorsweep.py`, `wdiff.py`
   or `wgate.py` changed).
5. Maintainers push the kit change to `main`. Anyone else sends only what AGENTS.md "Changing the
   kit" lists (`docs/CONTRIBUTING.md`).
