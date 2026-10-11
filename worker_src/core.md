## NEVER WRITE SOURCE WITH A SHELL HEREDOC — USE Write/Edit
`cat > f.cpp <<'EOF' ... EOF` breaks on quoting that is ordinary in C++: nested `'` and `"`,
backslash escapes, `$` inside a string. The failure is a truncated or missing file plus a misleading
`unexpected EOF while looking for matching`. Shell redirection is fine only for a one-liner like
`echo ok > flag.txt`.

## YOUR FUNCTION GOES IN THE `wip/` DIR YOUR PROMPT NAMES — NEVER IN `src/`
The build globs every `.cpp` under `src/`, so an in-progress file there — a `goto` whose label you
have not typed yet — fails `ninja check` for EVERY module and every other worker, and an integration
running in that module will delete, quarantine or commit your half-finished file. `wgate` compiles
whatever path you give it, so nothing needs your file in the repo until it MATCHES; the pipeline
moves it in for you then. Read siblings in `src/` freely — that is the cheapest lever there is — but
write only to `$SP/wip/<module>/`.

## SCRATCH GOES IN `$SP/handwork/`, NEVER IN `$SP` ITSELF
Probe scripts, variant generators and disassembly dumps written into the `dqix-sp` root fail the
pipeline's own inventory check on every run and have to be swept by hand. Put them in
`$SP/handwork/` — nothing there is read by another tool, and it survives the session.

## THE GATE NAMES YOUR RESIDUE — ROUTE ON THAT CLASS, NOT ON A GUESS
Every `wgate` run ends with `RESIDUE <CLASS> <metric> <detail>`. Route on it:

| class | what it means | do this |
|---|---|---|
| `NO-COMPILE` | it does not build | read the first error only; it is usually one declaration or cast |
| `OVERGEN` / `UNDERGEN` | wrong length | no colouring rewrite can help while the size is wrong — fix the shape first |
| `LOOP-SHAPE` | branch targets differ | a loop/guard is built differently. Declaration order is INERT here |
| `REGPERM` | only register numbers differ | `colorsweep --apply`, then recipe #9 (callee-saved) or #15 (scratch) |
| `SCHED` | same instructions, different order | move the DEFINITION, do not renumber. `colorsweep --apply` generates these |
| `OPERAND` | same mnemonics, different immediates/offsets | check offsets and constants (#4) |
| `SHAPE` | mnemonics differ | the C construct is wrong. Do NOT permute declarations |

`wgate` also prints a `GATE` line with your best result and how many gates since it improved, and a
`STOP` directive when it stops moving. STOP is the decision — write your verdict and end. Never
stop before one while the gate is still improving.

**WHAT HAS ALREADY BEEN RULED OUT ON YOUR ADDRESS** is injected below under its own heading
when there is any, straight from `worker_src/deadends.md`. If the section is absent, nothing
has been ruled out yet. Add a row there when you spend real budget disproving something.

**INSTRUCTIONS mwcc WILL NOT EMIT FROM ANY C.** Not a residue to permute — if the target needs one of
these, the function is `.s` work or not ours. Each was driven to exhaustion against the real gate.

* `ldm`/`stm` over more than four registers. Block copy is hardcoded to `{r0-r3}` with writeback on
  both sides at every optimisation level; explicit word loads stay plain `ldr`/`str`, and `memcpy` is
  a real `bl`.
* A register in an `ldm`/`stm` list that block copy never uses — `r8`, `ip`.
* `ip` in a `push`/`pop` callee-save list.
* An `stmia` in THUMB. Consecutive ascending register stores to consecutive addresses stay unfused at
  every level.
* An un-predicated early exit. A conditional return folds to `bxeq lr`; the ROM's
  `beq <own bx lr>` / `b` / `bx lr` triple cannot be reached, and `optimize_for_size off` does not
  change it.
* A branch INTO another function's interior, at an address no symbol names.

**Those are exhausted FORMS, not a verdict on the function.** The list above says "stop permuting the
expression", not "this cannot match" — that claim has been wrong every time it was made here.
`ov015:0218ee38` was called a scheduling wall and then closed in three steps; `main:02079cf8` was
parked at SHAPE 22 and a later session took it to REGPERM 3. When the expression space is spent,
move UP a level — these are the angles that have NOT been tried on any of them:

- **Your callee signatures.** 31 of 119 kept artifacts declare a callee with the wrong parameter
  list, and a wrong signature changes argument setup and live ranges — which is exactly what a
  register residue looks like. Run `python $KIT/symfix.py <your.cpp>` and re-gate BEFORE concluding
  anything about colouring.
  `0218ecd8` — a `sub r3, sp, #4` / `stm` / `ldr r3, [r3]` block in the target is the tell that a
  struct argument STRADDLES r3 and the stack. Size the parameter to produce it: two 12-byte structs
  by value put the second across r3/[sp]/[sp+4]. Do not copy a sibling's parameter list on faith.
- **Compiler FLAGS — this one is PROVEN, try it early.** The build could always swap the mwccarm
  build per file but never the flags, so every residue in this project was measured on one flag set.
  32 parked addresses improve under a non-default set. Cost: seconds. `main:020b7ba0` is the worked
  example both ways: REGPERM 14 on the default, byte-exact under `-O4`, and then MATCHED at the
  default once the flag had shown which redundancy to remove.

      WGATE_FLAGS="-O4" python $KIT/wgate.py <mod> <addr> <your.cpp>     # also -O3, -opt speed
      python $KIT/flagsweep.py --only <addr>                            # sweeps the useful sets

  A flag match is a DIAGNOSIS, never a result: the ROM was built at one flag set (-O2 globally), so
  matching only under -O4 means YOUR source carries something -O4 deletes — a redundant local, a
  dead store, a cached value the original re-read. Locate the difference with the flag, then fix the
  C and match at -O2. Never land it behind an override; `wlog/flag_leads.txt` records these and the
  sweep deliberately does not stage them.
- **Definition ORDER, enumerated rather than reasoned about.** When a run of field reads feeds
  arithmetic and the residue is registers, the order those values are DEFINED is the whole answer,
  and the space is bounded — `020b7ba0`'s was the 47th of 720:

      python $KIT/pad/permorder.py <mod> <addr> <src.cpp> <marker.txt>

- **The values around the residue, not in it.** A register pair that will not swap is often decided
  by a live range that starts somewhere else — a value the ROM re-loads and you cache, or one you
  re-load and it caches. Look for the DEFINITION that owns the register, not the use.

Write what you ruled out into the handoff section of the doc this prompt named, then take the next
address.

## NEVER WRITE HAND ASSEMBLY. STOP INSTEAD.
`asm { ... }` reaches the bytes by definition, so it is not a match — it is the question restated.
The point of this project is C source, and a hand-asm candidate is refused at integration: the
integrator moves it to `asm_park_<module>/`, drops it from the trusted set, and serves the address to
another worker, so the money you spent produced nothing. Measured 2026-08-25: `020c19b8` gated MATCH
as `asm`, cost $1.52, and was parked minutes later.

If you cannot reach the bytes with C, write `BLOCKED <addr> <CLASS> <metric> <your best file>` and
stop. A cheap honest BLOCKED is worth more than an asm file: the next session reads it
and starts past your dead ends. The only addresses allowed to land as assembly are the handful in
`asm_allow.txt` (BIOS syscall stubs and NitroSDK routines the SDK itself ships as asm), and you are
not the one who decides an address belongs there.

## STATEMENT ORDER IS THE SCHEDULE (promoted from opus matches, 2026-08-20)
When the diff is register numbers, load order, or an instruction sitting a few slots early/late, the
lever is almost never a different expression -- it is the ORDER of the statements that feed it. Four
280-byte functions closed on exactly this after colorsweep had already failed:

- `ov002:02168d54` — a field loaded into a callee-saved register BEFORE a call whose result is another
  argument of the same call (`ldr r4, [r2, #0x3cc] ; bl … ; and r2, r4, #0xff`) needs BOTH locals:
  `int v = field; int r = Get(); f(x, (signed char)r, (unsigned char)v);`. With only `v`, or with
  everything inline, mwcc moves the load after the call.
- `func_0205dd08` — the order of four setup assignments IS the emitted ldr/ldrsh order. Reordering
  the assignments reordered the loads; no expression changed.
- `func_021c3fb4` — moving a plain store (`evt.tag = 2;`) BEFORE an adjacent bitfield store changed
  which register held the value.
- `021e5a6c` — the same arithmetic on two fields emits in PHASE order, not field order. Write it as
  three passes over both fields (both divides, then both multiplies, then both fixups) rather than
  one nested expression per field; finishing field y before starting z reorders the whole block.
- `func_020b33f0` — declare the group pointer and index up front, then assign `index = 0` AFTER the
  pointer. Declaration order and assignment order are separate levers; this needed both.
- `func_0204bc74` — build a value by reassigning the PARAMETER in place (`val &= 0xfff; val |= pal;`)
  rather than composing a new local, so the OR lands in the parameter's register.

So: before concluding regalloc is inert, permute the order of the independent statements around the
diff -- assignments, declarations, and stores -- and re-gate each permutation. colorsweep automates
five such rewrites but only adjacent ones; a three-statement move is still yours to make.

More instances of the same law, all large-band matches:
- declare the loop counter INSIDE each `for`-init rather than one shared `int i = 0;` at the top --
  scope moves where the counter is materialised.
- declare the loop counter as the FIRST local declaration (decl hoist) to swap a callee-saved pair.
- move `int i = 0;` AFTER an adjacent memset/zero store so mwcc rematerialises `mov r1,#0` instead
  of copying a held register.
- parameter-as-cursor: replace `p = args + 2;` with `args += 2;` -- a fresh local sinks its
  definition to first use and lands at the bottom of the ladder.
- hoist the call-argument reads into locals defined BEFORE an intervening `%` so the object dies
  early and its register gets recycled.
- dummy pre-loop inits declared BEFORE the counter demote the counter to the last assignment.
- `0218558c` — REGPERM 6 to 0 by swapping ONE adjacent declaration pair inside the loop body
  (`int missing = 1;` / `int offset = ...`), giving offset r6 and the flag r5. Try the adjacent pair
  bracketing the wrong registers before permuting the whole list; `colorsweep --apply` does this
  rewrite for you on any source that already exists.
- `020dbae0` — outgoing-argument copies (`mov r1, r8` …) emitted ABOVE the stores before a call, where
  the ROM has them below: write the two stores of one value as a chained assignment,
  `b = a = value;`. Inert: bare declarations in either order, `int` locals, casts, store order.
- `021b1e24` — two field copies `dst->a = src->x; dst->b = src->y;` where the ROM loads `y`, then `dst`,
  then `x`, then stores both: pass both values through an inline setter, `Set(dst, src->x, src->y)`.
  A struct-member copy, locals in either order and reversed stores all miss.
- `0207dcf0` — writes into a struct follow SOURCE statement order, not member-declaration order.
  If the ROM stores `field8` before `field2`, write them in that order.
- `02163ccc`, `020d03fc` — when a whole callee-saved GROUP is wrong, split every local into a bare
  declaration at function scope and permute that list alone, leaving reads and calls in
  chronological order below. The ladder follows bare-declaration position, not assignment order,
  first declared taking the HIGHEST of the group: `base, alloc, slot, obj, list, node, idx` gave
  `base=sb alloc=r8 slot=sl obj=r7 idx=r6 node=r5 list=r4`. One list to permute, no instruction
  moved.
- **THE DIRECTION OF THAT LADDER IS NOT SETTLED — try both before permuting blindly.** Two of the
  three measurements run the OTHER way from `02163ccc`, first-declared taking the LOWEST:
  `021869b8` (396B, 103 bytes closed) put twelve bare declarations on `r4 … fp` in declaration
  order, and `02184354` (1816B, 14 bytes) put seven on `r4, r5, r6, r7, r8, sb, fp` the same way.
  Parameters take part in the same ladder and merge into the neighbour that outlives them, which is
  the likeliest reason the three disagree. Write the list, gate it, reverse it once; one compile
  either way.
- **A LOCAL THAT TAKES A LOWER REGISTER THAN THE PARAMETER IS DECLARED LAST OF ALL.** The parameter
  keeps `r4` whatever order the locals are in, while the ROM gives it `r5` and a long-lived local
  `r4`: bare-declare EVERY local at the top in its existing order and put that one local last
  (`021a51a4`, 23 bytes to MATCH; colorsweep and every permutation of the top four were inert).
  mwcc's simplify scans nodes in vreg order, and a local declared last is numbered lowest, so it is
  visited while its neighbours are still in the graph, survives that pass, and is coloured first.
- **A SINGLE BARE-DECL HOIST ROTATES A REGISTER CYCLE BY EXACTLY ONE STEP.** When the residue is an
  n-cycle among callee-saved registers (`r7->r8 r8->sb sb->r7`), you do not need to search the
  permutations: moving one bare declaration up one position advances the whole cycle one step, so
  n-1 hoists at most reach any rotation of it (`02184354`).

## ANOTHER PROJECT ALREADY WROTE DOWN THIS COMPILER'S RULES — GREP THEIR NOTES FIRST
`refs/sm64ds-decomp/notes/mwccarm-codegen.md` is ~4700 lines of measured mwccarm codegen rules for
the SAME compiler, organised by shape class, each entry citing the function it was cracked on; its
siblings are `ask-the-compiler.md`, `mwccarm-pragmas.txt` and `arm9-endgame.md`. Grep it for your
residue's shape before inventing a lever — it also records which classes it could NOT crack, which
tells you when to stop. Examples it settles outright: an equal-arm ternary on a call argument flips
argument emission order; loads-before-stores batching fires only in the ELSE arm of a guard; a fresh
loop counter per loop stops one counter's web spanning the function; register-web priority follows
VARIABLE IDENTITY, so the lever is swapping which NAME holds which value, not the declaration order.

## START FROM A SIBLING, NOT FROM THE LISTING — the cheapest lever measured
Three of the large-band matches landed on the FIRST attempt, and every one of them started by
copying an already-matched neighbour instead of composing C from the disassembly:

- the recursive mutex lock/unlock idiom copied verbatim from `RunAtExitHandlers_020015e8.cpp`
- `func_020d5b5c.cpp` cloned wholesale as a template -- same module, same
  `GetBattleContext` / `func_020d424c` / `func_020d40bc` / Invalidate+Clean call shape
- a wlist-style function transcribed literally, arguments and hoists included
- `021894b8` — the ov012 twin `func_ov012_02185af0` cloned wholesale, then three edits: one list
  base constant, one guard wrapped round an existing block, and one array shortened. A twin in a
  SIBLING OVERLAY is as good as one in the same module; diff the two listings and change only what
  differs.

**Do not cache a field the ROM re-reads.** `ov017:021b1d44` matched on the FIRST compile by reading
`self[0x21]` again at each of the two post-call sites instead of holding it in a local — that is what
emits the `ldrb` reloads. A call may clobber what you cached, so the ROM re-loads; a local does not.
Model a bitfield root as padding plus the field (`struct { char pad[0x5d00]; BitField area; }` over
`base + 0x26c`) and the `add`/`add`/`ldrh`/`lsl`/`lsr` sequence falls out.

Before writing any C, search the module (and `src/` at large) for a function whose call sequence,
prologue shape, and struct accesses resemble the target, and start from ITS source. A sibling
already encodes the project's idioms, its calling conventions, and the compiler settings that
matched -- reconstructing those from scratch is what burns tries. This is the highest-yield opening
move on the large band; treat "find the sibling" as step one, not a fallback.

## INLINE ACCESSORS ARE WHAT `opt_propagation off` WAS FAKING (main:02061c04, matched without it)

The ROM was built with propagation on; the developers kept values materialised with small `static
inline` accessors, whose return value mwcc does not fold into the arithmetic that follows it.

* `SubB(SubA(b)) + 0xa4` (each returning `p + k`) emits three adds; `b + 0x104 + 0x7400 + 0xa4` folds to two.
* An `int`-returning accessor over a `u16` load keeps `/ 2` signed (`add r,r,r,lsr #31; asr`); the raw load gives `lsr`.
* A `u8` field read through an accessor is loaded before the call's argument setup, which restores the ROM's staging order.
* `&((struct Blk *)p)[1]` leaves a stride temp mwcc cannot fold; it is materialised once and coloured first.
* An offset split across a cast and a member (`((Sub *)(p + 0x840))->bar` with `bar` at 0x1000) makes the
  backend create the combined add late, so it is coloured first — the address lands in the lower register.
* `*(short *)p -= lim;` keeps `sub r0,r1,r0`; `*(short *)p = x - lim;` becomes `rsb` + `add`.
* Fixed-point products through an `FX_Mul`-style inline keep the ROM's evaluation order.
* A derived pointer the ROM forms ONCE (`add r6, r5, #0x9c`, then `mov r1, r6` at every use) is an
  array element reached through an index: `&res->allocator_array_38[index]` in a `static inline` called
  with `5`. `res + 0x9c`, `&res->allocator_array_38[5]`, a member, a named offset local, a round trip
  and every pragma are re-formed at each use (`ov023:021eeaac`). A member-function accessor on a pad
  struct over the base works the same: `struct H { char pad[0x164 - sizeof(SafeAllocator)];
  SafeAllocator allocs[2]; SafeAllocator* GetAllocator(int i) { return &allocs[i]; } };` then
  `((H*)g)->GetAllocator(1)` keeps `add r7, r5, #0x164` live across calls (`main:0206f81c`).
* A field read twice through one accessor call that the ROM loads ONCE into a register is one call
  bound to a local (`EntFlags* fl = GetEntFlags(ent, 1); a = fl->bit0; b = fl->bit0;`), with the
  accessor indexing an array of fixed-size blocks, `(EntFlags*)&ent->blk[i]`, rather than a flat
  offset; a ternary store `x = !c ? 0 : 2` that mis-schedules is an `if/else` (`ov008:02189c70`); `o->f = a ? a : b` split into if/else stores swapped an sb/fp pair (`021bc77c`, colorsweep r19); with the first-defined local assigned first it closed an r7/r8 swap colorforce had traced to push order (`021a86d0`). A copy the ROM emits BEFORE the following compare is written in BOTH arms of the if/else, not once after it (`021e80e4`, colorsweep r32).
* A flag set to 1 early in a case (`flag = 1;`) shares a block with another `= 1` store, is CSE'd with
  that constant temp and coloured wrong; move it down to just before its use to get `mov sl, #1 ;
  mov r1, sl` (`main:0206f81c`).
* Two `unsigned char` values the ROM computes into callee-saved registers before intervening calls
  (`lsl r0, r6, #2 ; and r7, r0, #0xff ; add r0, r0, #1 ; and r8, r0, #0xff`) come from an accessor
  `static inline unsigned char GetScriptIndex(int slot, int offset) { return slot * 4 + offset; }`
  called with 0 and 1; plain `unsigned char a = j * 4;` locals are forwarded into the call
  (`ov017:0219bfb4`).

Strip the pragma first and fix what moves; a source that matches only under the pragma is in the wrong basin.

## PRAGMAS ARE A DIAGNOSIS — `wgate` REFUSES ANY SOURCE THAT CARRIES ONE
The ROM was built with every pass on, so `wgate` answers `RESIDUE PRAGMA` for any `#pragma` but
`define_section`. A pragma is only ever a scratch probe, read through `wdiff`, that names WHICH pass
moved the code. Then delete it and write the source that pass cannot reorder: a worse number
without the pragma is the right basin, a better one with it is not.

- **`optimize_for_size off` closes it:** if-conversion predicated an arm the ROM branches. The arm
  is usually one instruction short (the size boundary is under "Predication vs branch"), or the arms
  are inverted (`r24_arm_invert`).
- **`opt_propagation off` closes it:** propagation sank a constant past a store the ROM puts it
  before, staged nothing where the ROM holds a constant in a callee-saved register across a call,
  folded a variable index into the base add (ROM: two immediate adds and a register-offset load), or
  folded an accessor's result into the next constant. Use a small inline accessor (above), the
  pointer round-trip (below, `r27`), or a re-read (`reread`).
- **`opt_common_subs off` closes it:** CSE cached a value the ROM re-loads. Re-read it at the second
  use (`reread`) or read it through an accessor.
- **`opt_dead_assignments off` closes it:** the ROM kept a store mwcc deletes, often the one that
  puts an extra callee-saved register in the `push`. Make that store non-eliminable
  (`r44_volatile_split_store`) or give the value a later reader.

### PREFER THE POINTER ROUND-TRIP TO `opt_propagation off`
When the target materialises `&global` early and keeps that pointer live, but your build folds the
address into the use, a pragma is not the only answer — and it is not what the developers wrote.
Give the pointer a pending increment and take it back at the use:

```c
Struct* instance = &data_020fdc20;
instance++;
(instance - 1)->warp->CreateOpcode6aEntry(entry);
```

Propagation cannot fold `&g` through the increment; a later pass cancels `++` against `- 1`, so the
arithmetic emitted is unchanged and only the address formation moves. Two ZoneFeatures functions
that previously matched only under a file-wide `off` match byte-exact at the `-O2` default this way.
`colorsweep` r27 generates both this and the `instance--` / `(instance + 1)` mirror, so run the sweep
before reaching for the pragma. The same trick on an integer (`v++` … `v - 1`) does NOT survive —
that folds at propagation time; it is pointer arithmetic that outlives the pass.

Caching a field in a local is the same lever pointed the other way: re-reading `p->b` at its second
use site, instead of holding it in a named local, is what settles a callee-saved swap between the
two values (`021538e4`, REGPERM 8 to size-exact). `colorsweep`'s `reread` rule generates it, so the
sweep reaches this without a hand edit.

## A DEAD ARGUMENT SETUP MEANS THE CALLEE TAKES MORE PARAMETERS THAN IT USES
Target sets up an argument register the callee never reads (`mvn r1, #0` before a one-parameter
call)? The source passed it anyway. Declare the callee `extern "C"` under its exact mangled symbol
with the extra parameter — `extern "C"` fixes the symbol as written, so the signature is free:

    extern "C" void _Z29ProcessRefAndDispatch0204ffc0P11Obj0204ffc0(struct Obj0204ffc0* a, int b);
    _Z29ProcessRefAndDispatch0204ffc0P11Obj0204ffc0((struct Obj0204ffc0*)entry, -1);

Adding a parameter to a normally-declared C++ callee re-mangles it and gates UNDEF-SYM. Copy the
name from `symbols.txt` by callee ADDRESS — the number in `_ZNN` is the identifier's LENGTH, so a
retyped name gates UNDEF-SYM and looks like the approach failed.

The VALUE matters as much as the arity. Passing `0` where a nearby block store also writes zero lets
the two share one constant: the store's zero CSEs into the argument register and pushes the object
pointer off r1, which is the whole residue (`021d9340`, 2332B, 8 bytes). Read what the surrounding
code already materialises before choosing the extra argument.

**The reverse: a STALE argument register means the call passes FEWER arguments** (`02178910`). If r1
still holds an unrelated pointer at the call and the value you expected there lives only in r2 (for
an adjacent `strb`), the source passed ONE argument to a callee whose real signature takes two.
Declare the callee `extern "C"` under its mangled name with the one parameter, as above.

**A TWO-REGISTER REGPERM CAN BE A MISSING CALL ARGUMENT — check the arity before permuting.** For a
callback held in a struct, type it varargs and pass the extra value on the ONE path that has it:

    void (*Report)(int, const char*, ...);      // was void(*)(int, const char*)
    ctx->Report(0x8000000, str520, mode);       // busy path only

The vararg pins `mode` to r2 instead of letting mwcc reuse the dying state pointer's r1, and the
occupied r2 pushes that block's function-pointer temp to r3. Both "wrong" registers were one absent
argument (`0223a374`, 264B, REGPERM 5 -> MATCH). No declaration order reaches this.

**A CALL RESULT FED STRAIGHT INTO THE NEXT CALL shows no setup at all** (`bl A ; bl B`), yet it
changes the schedule LATER: written as two statements, `ldr r1, [pc]` / `mov r0, sb` at the next
three-argument call came out swapped. `B(A())` with `B` re-declared `extern "C"` taking one argument
closed it (`ov017:021abba8`, 8 -> 0). When a `bl` follows a `bl` with nothing between, try passing
the first result.

**AN ARRAY ELEMENT INITIALISED FIELD BY FIELD** addresses each store as `add r1, sp, #(base+field) ;
str rV, [r1, rOff]` with `rOff = k * size`, and materialises `&arr[k]` only afterwards for bitfields
and calls. That is `arr[k].field = v;` per field, not a `T* p = &arr[k];` (which stores off one base
register) and not a `static inline` init (too big: mwcc emits it out of line) (`ov017:021abba8`).

## SCRATCH REGISTERS (r0-r3, ip, lr) ANSWER ONLY TO OPERAND ORDER

**CHECK THE FUNCTION'S OWN RETURN TYPE, NOT JUST ITS CALLEES** (`ov017:021ce5b4`).
A scaffold's `void` is a guess: if the target leaves a derived pointer in r0 on
success and each already-null call result in r0 on early exits, returning that
pointer can close a scratch-register residue. Two inline nested-member accessors
preserved the 0x134 adjustment here; changing `void` to `void*`, returning the
fields pointer, and returning the checked pointer on each null path closed
REGPERM 17 at the default flags. Literal `return NULL` instead added two redundant
`moveq r0,#0` instructions. Check the value in r0 on every exit before inferring
a return contract; a forced diagnostic object is not evidence of a source match.

A scratch-register swap in the diff is fixed by swapping the operands of the commutative operation
that feeds it (`pad/probe_scratch.cpp`):

    (s64)a * b   ->  smull r3, r1, r0, r1 / subs r0, r3, r2 / sbc r1, r1, r2, asr #31
    (s64)b * a   ->  smull r0, r3, r1, r0 / subs r0, r0, r2 / sbc r1, r3, r2, asr #31

Byte-identical and therefore useless: binding the product or the result to a local, one inline
expression, hoisting an operand first. Declaration order is a CALLEE-SAVED lever and does nothing
here. Each site is independent — with several such blocks the answer may be swapping one and not the
other, so give `colorsweep` depth for the combinations instead of rewriting by hand.

### A CALLEE-SAVED ROTATION IS VREG NUMBERING — set it by declaration order (`0215d63c`, `021615bc`)
Simplify scans vregs in ascending order and the last node pushed takes the lowest free register, so
"X must colour before Y" means X needs a HIGHER vreg number. Declared locals are numbered in REVERSE
textual order regardless of scope: to raise a number, declare it EARLIER; to land between two
block-scoped locals, move the group to function scope in the order you need (`021615bc`, 75 -> 0).
`python $KIT/pad/renum/renum.py <src> <mod> <addr> <size> X=<rank>` forces a numbering in the real
compiler (`X=86.5` = between v86 and v87) and tells you which rank closes it.
Declaration order only moves locals that KEEP their own vreg. Check first with
`pad/renum/vdump.py`: if a local's number does not change when you move its declaration, the lever
is dead for it. Two kinds ignore it: loop-body shorts/chars and CSE'd loads that become IR temps (a
low band), and backend temps (the split of an out-of-range `ldrh` offset, pool/constant loads),
numbered above every IR vreg. Hoisting ALL loop-body locals to the top of their branch, C89 style,
with the counter declared last, is a lever of its own (`021ddf5c`, 106 -> 54).
When the callee-saved registers are over-subscribed, the clique's last optimistic-spill victim is the
CHEAPEST node, cost = number of basic blocks that reference it; the victim takes the last register
(`fp`). To move the victim from one local to another, change which one a block reads: `lo = 1.0f`
instead of `lo = hi` (same asm after CSE) shifted one reference and flipped it (`021fa7ec`, 67 -> 6).
A compound float op (`hi *= 0.8f`) puts the constant in r1; `hi = hi * 0.8f` puts it in r0.
**A FIELD LOAD SCHEDULED BEFORE THE ARGUMENT MOVES = AN INLINE GETTER THAT RETURNS THROUGH A LOCAL**
(`02157d40`). Under register pressure the scheduler breaks ties by IR order, and call arguments are
lowered in argument order; `return u->id;` (or a plain local) folds back into the argument, but
`inline int UnitId(U* u) { int id = u->id; return id; }` is its own statement ahead of the moves, so
the load wins the tie: `InitObj(p->obj8, UnitId(comb))`, `SetIndexedName(msg, 2, UnitName(target))`.

**A NARROWING `and` SCHEDULED AFTER A RUN OF TABLE LOADS = INITIALIZER-ARRAY COPIES IN THE SOURCE**
(`021eb578`). Each initializer-array copy lowers with a throwaway stack-address instruction that
delays the `and` past the loads, which keeps `screen` live across them so it colours r0. Write the
copies `*(Bytes2*)priorities = *(Bytes2*)&data_ov023_021fd844[8];` and store an int `screen` straight
into the bitfield; no separate narrowed `screen8` local.

**THE ROM RECOMPUTES EVERYTHING AFTER EVERY CALL = THE IR OPTIMIZER WAS OFF FOR THAT FUNCTION**
(`0218cc24`, decompiled 0x4f4110 / 0x545a20; a local `char buf[0x80]` in `0207568c`). mwcc skips ALL IRO passes (global CSE, copy/const
propagation, LICM) for a function containing inline asm or touching any object whose type carries an
alignment qualifier. Signature: an index product like `mov #0xac; mla` re-done after each call,
constants re-materialised, nothing hoisted, only per-block CSE. Source: the buffer it uses is
declared `__attribute__((aligned(4)))` on the object (`extern int data_0211e33c __attribute__((aligned(4)));`, consistent with its 0x...33c address). Any N works, on the object or via `typedef T A __attribute__((aligned(N)))`; on a STRUCT type it does nothing. colorsweep r62 tries it.
Try that FIRST when a function shows this signature; every other lever is inert until it is right.

**A load that cannot move past a store = an alias edge mwcc added** (`021de124`, decompiled). An
access through an unresolved pointer is worst-case; an access to a known object (local static,
const, local) is exact unless its alias falls outside the object. Alias propagation double-counts
the immediate on `ldr rX,=s; strh [rX,#k]`: the access lands at (2k, size), and if 2k+size >
sizeof(s) the WHOLE object joins the worst-case set and every pointer load now depends on it. So
a struct declared smaller than the real object makes stores look aliasing: give the struct its true
size (look for other files touching higher offsets). The reverse: when the ROM HAS an edge between
two stores, they hit the same object; write both through one base (`*(u16*)&((Oam*)&tbl[0].b)[n]`
instead of a second extern at +4), and a narrow local (`char`/`short`/`bool`) spilled with a 4-byte
`str` runs past its object and goes worst-case, so two such spill stores depend on each other: type
them to get or lose that edge (`0218d2f4`, `0218c04c`). `schedforce` names it as an `mwar`/`mraw` edge;
`pad/renum/aliasdump.py` shows the alias behind each query.

How the numbers are assigned (decompiled): IRO temps `@N` first (newest first, the low band), then
declared locals in reverse declaration order, then lowering temps (operands before the op, a for
condition after the body), then pcode-expansion temps (offset splits, constants). An IRO temp appears
when an expression is REUSED, including a short promoted to int twice; its number overrides
declaration order, so remove the reuse (hold the narrowed value in a wide local:
`int key = (short)(idx[i] + 12);`, `021ddf5c`). `x = e` is a temp plus a copy that CSE deletes unless
a call or a block with 2+ predecessors intervenes. Coalescing merges copy-related vregs and keeps the
LOWER number, so an unreachable rank is usually a missing or unwanted coalesce:
`pad/renum/forcemerge.py <src> <mod> <addr> <size> A:B` tests one, `stagedump.py` shows the IR.
Write a loop base as a plain expression inside the address, not a variable or helper
(`(Elem*)(lists + 0x8000) + j`, `021e0638`).
A counter that ignores declaration order was renumbered below every local by common-subexpression
elimination re-reading a dominated `ids.v[i]`: type the counters and the count `long` (SDK `s32`)
so the conversion is not merged (`0215d63c`, 185 -> 0). A leftover `cmp a; cmpne b` is operand
order, not colouring: sweep the condition spelling.

### TWO SCRATCH VALUES SWAPPED WITH NO OPERATOR BETWEEN THEM — the value created LAST gets the LOWER register
Colouring pops a stack that simplification fills in value-creation order, so of two values
simplified in the same pass, the one your source creates later is coloured first and takes the
lowest free register. `int state = self->state; G.elapsed += dt;` gives state r3 and the loaded
`elapsed` r2; the ROM has state r2, fixed by `G.elapsed += dt; int state = self->state;`
(`ov023:021f4c04`). A single-use plain load is forwarded to its store and cannot be reordered this
way; bind it through an inline accessor instead (`Entry* e = obj->GetEntries();` before
`obj->GetTable()->SetEntries(e, n)`), which creates the value before the base (`02184a4c`).
Same for a compare operand: `signed char cv = cells[cur]; if (cv == n)` swaps the reloaded byte
and `n`; field reads bound through inline accessors before the index load create the base temps in
ROM order (`02169eec`).
The scheduler still emits the state read where the ROM does, UNLESS the store
in between may alias it: a store into a non-const global does, and pins the read below it, so first
make that object a static in an inline accessor (see "A CONSTANT HOISTED ABOVE A GLOBAL'S POOL
LOAD"). colorsweep `r5_stmt_swap` makes the move. To find which value to move,
`python $KIT/frida/colorforce.py <src> <mod> <addr-hex> <size> <pool-offset-hex>`
flips one colouring decision at a time and names the flip that makes the function exact.

### TWO LOADS IN SWAPPED SCRATCH REGISTERS — load both into their locals, THEN transform each
Two back-to-back loads (`ldrsh r3,[r0,#0xac] ; ldrsh r2,[r0,#0xae]` where the ROM has r2/r3), each
feeding its own shift or cast, with statement order, declaration order, raw locals and getters all
inert: load both values first, then transform them (`ov023:021e63bc`, 6 bytes -> MATCH):

    int x = pos[0];
    int ytop = pos[1];
    x = (short)(x << 3) + 0xa;
    ytop = (short)(ytop << 3) + 0x10;

The second load then stays live across the first transform, whose temp is coloured earlier and
takes the lower register. Loading and transforming x before loading y does not do it. colorsweep
`r55_load_then_transform`.

### A VALUE/ADDRESS SCRATCH PAIR THAT OPERAND ORDER CANNOT SWAP — define the address twice
A loaded value and a computed address in r1/r2, swapped against the ROM, with every operand order
inert: write the address, or its offset, as a variable with a SECOND definition (`0219e384`):

    int off = entry->slotIndex;
    off *= sizeof(struct GrottoSlot);
    struct GrottoSlot* slot = (struct GrottoSlot*)(ov + 0x3a9c + off);

    unsigned short* warpSlot = (unsigned short*)(ov + 0xae);
    warpSlot = (unsigned short*)((char*)warpSlot + 0x4400);

Inert: plain `+=` on a `char*`, a named index or base local, casts, struct/array views.
colorsweep `r54_two_def_offset` does the indexed form.

### AN OPERAND ORDER THAT DRAGS ITS REGISTERS ALONG — read the value through a getter WITH A LOCAL
When swapping `a == b` also swaps which operand gets the higher register (the `cmp` still wrong,
now at double the bytes), read one side through a static inline that copies into a local and
returns it. The local decouples the operand's position from its register (`0205faf4`, four
`cmp` sites, 8 -> MATCH):

    static inline int Field950(struct C* c) { int v = *(int*)(SUB150(c) + 0x950); return v; }
    return CU16(2) == Field950(c);

The same getter WITHOUT the local is inert (12), as are named locals at the call site, casts, a
struct view, `!(a != b)` and an inline `Eq(a, b)`. The same idiom orders a stack argument's store
after a register argument's field load (`0219e384`):

    short GetId() const { short id = s4; return id; }      Call(c->GetId(), x, y, z, 0);

### TWO STRUCT COPIES THE ROM INTERLEAVES — the destinations are in two stack objects
The ROM forms both copies' source and destination addresses before the first `stm`; ours finishes
the first copy before forming the second's (`0219e384`). Splitting the one frame struct in two, with
the two destinations in different structs and the frame size unchanged, gives the ROM's order.
Inert: a copy helper in any argument order, a pointer local per address.

### A SINGLE-DEFINITION LOCAL IS RE-DERIVED AFTER A CALL — give it a second definition
`int prev = i - 1;` is forward-substituted: mwcc reloads `i` and recomputes `i - 1` after the `bl`,
where the ROM kept it in callee-saved `fp`. Assign it to a variable that already has another
definition (`idx = i - 1;`, reusing a dead parameter) (`021d8c30`). The same variable split in
place also works: `last = shown; last -= 1;` instead of `last = shown - 1;` (`021db634`, colorsweep r63).
A function-wide swap decided by one high-pressure loop: bind the ELEMENT address once
(`SafeAllocator* alloc = &self->allocs[2]; alloc->Reset();`, pass `alloc`) instead of a base plus
`&alloc[2]` at each use (`02155e28`).
A pointer coalescing onto a base's register (`entries = tbl + 4` sharing `tbl`'s r7) is the same
fix: share ONE function-scope variable between that case and another loop so it has two
definitions (`021f2e6c`). Same when two values sit in each other's callee-saved registers and
declaration position is inert: store the late call result into an EARLIER function-scope local
(the loop's `node`) instead of a fresh one (`0216033c`).

### A LICM TEMP TAKES ITS SPILL SLOT AFTER THE LOCALS DECLARED BEFORE IT
Two spilled values in each other's slots: a named pointer local (`codes = row->codes;`) is slotted
in declaration order; deleting it and writing `row->codes[i]` makes the hoisted address a compiler
temp created later, which swaps the slots (`021d8c30`). For a pair fed from one value, the
assignment chain is the store order: `keepDefault = textKind = x;` stores `textKind` first.

### A ZERO WRITTEN AFTER CONSTANT STORES TAKES THE OTHER SCRATCH REGISTER
`generic = 0;` written after `soundId = 3; textKind = 1;` is hoisted above the compare into r1 and
swaps r0/r1 through the block; written BEFORE them it lands in r0, as in the ROM (`021d8c30`).

### A NAMED ADDRESS LOCAL MOVES THE INVARIANT HOIST — write the address at each use
`flags = self + 0x195b; ... *flags |= 1;` hoists the loop invariants in a different order from
`*(self + 0x195b) |= 1;` written at the use. A copy-modify-store (`int cur = F; cur += step * 16;
F = cur;`) is an in-place update in the ROM, with the copy's only shift folded into its
initialiser: `int old = F >> 4; F += step * 16; int n = (F >> 4) - old;` (`02065990`). colorsweep
`r52_inline_address_local`, `r53_update_in_place`.

### A MULTIPLY-ACCUMULATE PICKS ITS ACCUMULATOR FROM THE SOURCE — bind the multiplicand
`int x = A * K + B;` lets mwcc make the ADDEND the accumulator: it loads `B` first and makes `B`'s
register the `mla` destination. When the ROM loads the MULTIPLICAND first (often into `ip`) and
writes the result to a fresh register, bind the multiplicand to the result local and self-assign:

    int cur = A;                 // main:0205f9cc — ldr ip,[r2,#0x488] / ldr r0,[r2,#0x48c]
    cur = cur * K + B;           //                 mla r3, ip, r1, r0

Commutative-operand swap (`B + A * K`) does NOT reach this — mwcc canonicalises it back. Neither do
a named local for either operand, a struct or pointer local for the base, or any pragma. This is
`colorsweep`'s `mlaacc` rule, so a sweep finds it; what it leaves behind is a plain colour that
`declmove` closes.

## AN IDENTITY OP IN THE TARGET (`sub Rd, Rs, #0`) IS A NULL-POINTER DIFFERENCE
The front end folds `x - 0` in every spelling — literal, enum, `static const`, template non-type
parameter, propagated local, all 19 compiler builds — so no arithmetic source form produces it.
Pointer minus a NULL POINTER is not folded:

    int i = (int)(p - (char *)0);   ->  sub Rd, Rs, #0

Take the parameter as `char *p` and bind it back. That instruction is also a live-range split: it
copies the parameter out of its incoming register, so every later scratch assignment shifts by one
and the target's remaining register roles fall into place. WHERE the binding sits in the declaration
run decides which register the copy lands in — put it after the flag/accumulator declarations, not
before. `colorsweep` r25 tries this automatically.

**RUN IT THE OTHER WAY TOO — r25 only ADDS the round-trip.** When the ROM has a plain `mov fp, r3`
where we emit the `sub`, our source is carrying a round-trip the ROM does not have: delete
`int b = (int)(b_p - (char*)0);` and take `b` as a plain `int` parameter. The spurious `sub` is also
a live-range split, so removing it rotates the r5/r7/r8/fp ladder back by one at the same time
(`02218604`, 268B, 18 -> 14 bytes before the final decl hoist).

**Hoisting a BARE declaration above an INITIALISED one swaps that pair.** `int len;` moved above
`TextBuffer* buf = &self->buf;` gave `buf=r6, len=r5` (`02218604`). `colorsweep` does NOT generate
this — `r4`, `r8`, `r11` and `r14` all return zero candidates for a bare/initialised adjacent pair —
so it is a hand edit until that gap is closed.

`orr Rd, Rs, #0` and `and Rd, Rs, #0` are the same class of survivor from a 64-bit operation whose
constant has a zero word: `(int)((long long)x | 0x100000000LL)`. A flag-setting `adds`/`subs Rd, Rs,
#0` means the carry into the high half is still live. `add Rd, Rs, #0` is the zero-accumulator form
`acc = acc + f(...)` with `acc` still 0 (colorsweep r12), or `add rN, sp, #0` — the address of a
local at frame offset 0, which is never folded either.

## `stm rD, {rX, rY}` NEEDS TWO DISTINCT VALUES — a repeated constant cannot fuse
mwcc fuses consecutive word stores into `stm`/`stmib` only when the values sit in distinct,
consecutive registers (`pad/probe_stmpair*.cpp`):

    void f(S* p, u32 x, u32 y) { p->a=x; p->b=y; ... }   ->  stm r0, {r1, r2}
    void f(S* p) { p->a=0; p->b=0; p->c=0; p->d=0; }     ->  mov r1,#0 + four str

Two zero locals collapse to ONE register, and `stm {r1,r1}` does not exist. Inert against it:
`opt_common_subs off`, `opt_propagation off`, a walking pointer, `*(u64*)&p->a = 0`,
`optimization_level 4`, `opt_unroll_loops on`. Struct assignment is worse — in a `-lang=c++` TU it
emits a real `operator=` call.

So if the ROM shows an `stm` pair and your fields are all zero, the source did NOT store literal
zeros: those registers hold two different variables that happen to be zero. Work out what they are
from the callers; no zero-fill shape fuses.

## A PARAMETER'S CALLEE-SAVED REGISTER IS ITS POSITION — nothing in the body moves it
Parameters take callee-saved registers in REVERSE parameter order, immovably
(`pad/probe_calleesaved*.cpp`):

    f(a, count)     -> mov r5, r0   mov r4, r1
    f(a, b, c)      -> mov r6, r0   mov r5, r1   mov r4, r2

Use order, rebinding a parameter to a local, that local's declaration position, and pointer-vs-int
type all leave it identical. So a whole-function swap of two parameters' callee-saved registers means
**the signature is wrong, not the allocation** — wrong parameter order, or an extra/missing
parameter shifting every position. Re-derive it from which of r0-r3 feeds each callee-saved register
in the ROM listing; the scaffold's ARGS line is a heuristic, not evidence. No sweep rule reaches this.

**An extra parameter shifts the ladder only when it is LIVE.** A dead one is stripped before
allocation, so padding the signature to move a register does nothing: on `ov017:021941fc` the
one-parameter form and `(ov, int arg1, int arg2)` gate BYTE-IDENTICAL.

**When the ROM holds a PARAMETER one or more rungs higher than you do, promote the locals that
outrank it into trailing parameters** (`ov017:021941fc`, 4120B, **112 -> 17** in one edit). Declare
them after the real parameter and leave their assignments exactly where they were in the body:

    -void f(unsigned char* ov) {
    +void f(unsigned char* ov, unsigned char* mode, void** list) {
         /* delete `unsigned char* mode;` and `void** list;` from the local declarations */
         ...
         list = *(void***)(ov + 0x3000 + 0x6fc);      // assignment stays put

The incoming r1/r2 are dead-stored and never read, so the prologue stays byte-identical and existing
one-argument callers remain ABI-safe. Reach for this when the residue is a pure pair swap involving
the parameter and no declaration order fixes it — measured over all six permutations of the competing
locals, the parameter kept the same low rung in every one.

With locals competing, the ladder is DEFINITION order, highest register first (one parameter plus
four call results gives `r8, r7, r6, r5, r4`). A parameter counts as defined at ENTRY, so it takes
the HIGHEST. If the ROM holds it in a LOW one, the ROM does not keep it live from entry — it
re-loads the value later, which defines it late. Find that reload rather than permuting declarations.
Caveat: a value whose live range spans a loop can outrank definition order, so treat the ladder as a
first guess. That holds with FEW locals competing; with many it inverts. On `ov017:021941fc` (eleven
named locals) the parameter took the LOWEST rung in all six permutations of the competing locals, and
the ROM performs no reload — the load appears exactly once, in the schedule slot we already emit. If
the listing shows a single load, stop hunting for a reload and promote instead (below).

**To swap the pair WITHOUT moving any code, split the second one's DECLARATION off its definition
and hoist just the declaration above the first** (`ov000:0215858c`, 4816B, 253 bytes of pure
pair-swap). Two pointers each defined by an expression in source order give the first the higher
register; declaring the second uninitialised on the line above the first, and assigning it where it
already was, reverses that pair for the whole function:

    struct CombatantAilments* ailments;                  // declaration only, hoisted
    struct Combatant* combatant = GetCombatantFromList(list, i);
    ailments = &combatant->ailments;                     // definition stays put

An empty declaration is not a definition, so nothing else in the ladder moves and no statement
changes order — this is the one edit that touches colouring alone. `colorsweep`'s `r7_decl_split`
plus `r8_decl_hoist` generate it, so try the sweep before doing it by hand.

**Only for an ADJACENT pair.** Hoisted past other definitions it moves that local to the TOP of the
ladder and rotates every register in between instead of swapping two: on `main:020227dc` the pair
sits four definitions apart and this turned `r8<->sb` into `r6->r7 r7->r8 r8->sb sb->r6`, 14 bytes
to 20. If the two are not adjacent, this is not the lever.

The full ladder is **`sl, sb` for the parameters, then `r8 r7 r6 r5 r4` descending for the locals,
then `fp` last** (`pad/probe_r9.cpp`, eight call results chained into one sum). Two consequences:
`sb`/r9 IS allocatable — a ROM that uses it is not using a register our build reserves, so never
reach for a compiler flag; and `fp` is taken AFTER r4, not in numeric order, so a ROM holding a value
in `fp` has defined it later than everything in r4-r8, not earlier. When your set and the ROM's set
differ by WHICH registers appear rather than by their order (`0201edf0`: the ROM uses `sb` and never
`r6`, we use `r6` and never `sb`), the ladder start has shifted because something is live at entry
that is not live at entry for us. That is a signature or a live-range difference, not a colouring
one, and permutation sweeps cannot reach it — two of them have already failed on it.

Four measured consequences, each from the function that proved it:
- `0204bab4` — hoisting ONE declaration out of a `for`-init to function scope, leaving the others in
  place, swapped the whole r4/r5/r6/r7 assignment. `colorsweep` spent 80 compiles and found nothing;
  the hoist did it. If a permutation sweep stalls, move a declaration across a SCOPE boundary.
- `0222170c` — declaring the odd-one-out local LAST rather than first rotated the stack slots back
  by one and closed the residue.
- `020b3bac` — two SAME-SIZE stack objects take their slots in declaration order, so swapping them
  decides which lands at `sp+0` and which at `sp+0xc`. (Differently sized objects do not obey this;
  model that region as one struct instead.)
- `0208ec78` — hoist a local that SHARES a callee-saved slot with another value into its own
  declaration statement above it. Declaration order, not just assignment order, decides which freed
  register mwcc reuses.
- `02037d88` — a call that both branches of a block share belongs BEFORE the dispatch, not after.
  Written after, it takes a different callee-saved register and every local behind it shifts.

## A LOOP COUNTER'S DECLARATION POSITION COLOURS THE POINTERS AROUND IT
When callee-saved pointers come out as a register permutation (REGPERM on r7/r8/sb) and permuting
their own declarations is inert, move the LOOP COUNTER. A counter declared at function scope, placed
among the pointer declarations, changes which pointer's register it coalesces with; declared after
every pointer, or inside the `for`, it pairs with the wrong one.

    void* axis; int i0; int i1; int i2; Modes* modes; char* battleCtx; ...    MATCH  (02021578)
    void* axis; Modes* modes; char* battleCtx; ...; int i0; int i1; int i2;    22 bytes

Move the counters as a BLOCK — moving one of the three got 10 of the 22. A `for (int i ...)` counter
must be hoisted first: a bare `int i;` emits nothing, so no instruction moves, and any position after
the first pointer matched `020227dc`. Moving a declaration that carries an INITIALIZER moves the
instruction with it and costs more than it saves. On `02021578` all 36 orders of the pointers were
inert.

### A CALLEE-SAVED LADDER FOLLOWS DECLARATION ORDER — unless a post-increment renames the variable
Declared scalars are numbered in reverse declaration order, and the busiest ones take callee-saved
registers highest number first: declared earlier = lower register. `x++` inside an index
(`ids[count++] = c;`) splits `x` into a new, late-numbered variable that is coloured after every
declared one; write `ids[count] = c; count++;`. Two loops that share one counter share its register;
if the ROM gives them different registers, give each loop its own counter (`02169b4c`). A flag
accumulated alongside a call (`mask |= N;`) goes BEFORE the `Store(g, count++, ...)` call when the
ROM sets it first; same for a local assigned in an arm before its call, and for two increments
ahead of a call taking `&idx`: `count++; idx++;` puts the plain counter first (`02192700`). Hoist the block's
scalars to bare declarations and search their order with
`python $KIT/pad/declperm.py <mod> <addr> <template.cpp> <decls.json>` (template holds `/*DECLS*/`);
`ov023:021eeaac` 68 -> 19 that way. Check a hypothesis before writing it:
`python $KIT/pad/cf_multi.py <file.cpp> ovNNN <addr> <size> <pool_from> '{"moves": [[idx, pos]], "choices": [idx]}' --order`
reorders or flips mwcc's colouring and prints the node order.

### THE SAME LEVER ORDERS SPILL SLOTS — declare bare, assign where the ROM computes it
Scalars that spill get stack slots in declaration order, first declared = highest address. A local
declared with an initializer at its point of use lands in the LOWEST slot and shifts every spill slot
above it by 4 — a diff full of `[sp,#0x5c] | [sp,#0x58]` rows. Declare it bare where its slot says it
belongs (ROM slot above `battle` => declare it before `battle`) and keep the assignment in place:

    unsigned char* codes;                        // before the first scalar
    ...
    codes = node->codes;                         // where the ROM loads it

`ov025:021ebb90` (4096B) 84 -> 49 in one edit. Moving the INITIALISED declaration instead drags the
load with it and costs more than it saves.

### A LITERAL-POOL OFFSET DIFF CAN HIDE A WRONG CONSTANT — compare the VALUES, not the offsets
`ldr r0, [pc, #0x584] | ldr r0, [pc, #0x58c]` reads like pool-layout noise and usually is. It can
also mean ours loads a DIFFERENT constant: mwcc lays the pool out in first-use order, so one wrong
literal early in the function reorders the whole pool and every later load shifts with it. Run
`python $KIT/pad/poolmap.py <mod> <addr> <file.cpp>` before chasing any of them as a schedule: it
prints every `ldr [pc]` with the VALUE it reads in the ROM and in our build (relocated pointers
resolved to their symbol's address), and marks each row VALUE (a wrong constant — fix the source)
or offset (same constant, different slot). `ov025:021ebb90` had two constants swapped
(`1.8f` passed where the code used `&data`, `4096.0f` multiplied where the code used `1.8f`); fixing
the VALUES cleared four "offset" runs at once, 31 -> 24.

### WRITE A LOOP-INVARIANT INSIDE THE LOOP WHEN THE ROM COMPUTES IT AFTER THE LOOP INIT
If the ROM initialises the loop counter FIRST and only then stores an invariant (`j = found` reload,
then `codes = node + 0x14` to its slot), a `codes = node->codes;` written before the `for` comes out
in the wrong order. Write it as the FIRST statement of the loop body instead: mwcc's loop-invariant
pass hoists it into the preheader, which comes AFTER the `for`-init. Flags reset next to it may have
to be `unsigned char` (or `bool`) — with `int` flags the same move changed the size on `021ebb90`:

    unsigned char found = 0; unsigned char blocked = 0;
    for (int j = 0; j < node->codeCount; j++) {
        codes = node->codes;                                // hoisted after j's init

`ov025:021ebb90` 24 -> 14.

The same shape with no local at all: `and r0, fp, r0, lsl r8 ; mov r7, #0 ; str r0, [sp, #0x10]` and a
reload of that slot inside the loop is the condition `if (present & (1 << current))` written in the
loop body. A `flag = present & (1 << current);` before the `for` stores before the counter init, and
no declaration or statement order moves it (`ov023:021eeaac`, the last 19 bytes).

### A CONDITIONAL STORE AS `?:` BECOMES A COMPILER-TEMP SPILL — the lowest slot group
When a flag's spill slot is too HIGH and moving its declaration anywhere only makes things worse,
fold the second condition into a `?:` on the store. The ternary's value becomes a compiler temp,
and compiler-temp spills are laid out BELOW the declared scalars:

    if (e->mode == 1 && obj->f311 != 0) waitUnique = 0;            // slot 0x14, 49 bytes
    if (e->mode == 1) waitUnique = obj->f311 != 0 ? 0 : waitUnique;  // slot 0xc,  35 bytes

Same instructions (store 1, then `movne`/`strne` 0), different slot. `ov025:021ebb90`.

### BREAK A CSE WITHOUT SWAPPING REGISTERS — build the named pointer through a `static inline`
When the ROM RECOMPUTES `&obj->arr[i]` for a call but ours reuses a named `e = &obj->arr[i]`, every
direct CSE-breaker (`(T*)obj + i`, `&obj->arr[(unsigned)i]`, declaring `e` later) produces the
recompute AND swaps `i`/`e` across the whole function. Compute `e` through an inline helper instead
and leave the call's argument exactly as it was:

    static inline T* EntryAt(Container* o, int n) { return (T*)o + n; }
    T* e = EntryAt(obj, i);            // the call still passes &obj->arr[i]

The inline boundary hides the equality from the CSE pass; the registers stay put. `ov025:021ebb90`
35 -> 31. The helper MUST reach the element by different arithmetic — pointer plus index from the
container base. `return &o->arr[n];` inside the helper is re-CSE'd against the call and stays 35. Under `-inline noauto` only a `static inline` whose body is a single `return expr` is
inlined — a helper that stores through a parameter emits a `bl`.

## TYPE WIDTH IS A REGALLOC LEVER — `int` vs `unsigned char` on the same value
A byte-typed local sourced from a `ldrb` changes which callee-saved pair mwcc picks. When the diff
is register NUMBERS around a byte value, retype it before touching anything else:

- `int id = *ids;` instead of `unsigned char id` flipped an r8/sb callee-saved pair.
- a parameter typed `int` (not `unsigned char`) -- with the callee declared `extern "C"` under its
  mangled name so the narrow-width prototype stops applying -- fixed the argument setup.
- SIGNEDNESS PICKS THE MNEMONIC. A struct field typed `int` rather than `unsigned int` makes
  `f >> 2` emit `asr` not `lsr`, and `f >= 0x38` emit `blt`/`bge` not `blo`/`bhs`. When the only
  wrong mnemonics are shift or compare flavours, retype the FIELD, not the local (`020c0a40`).
- Keep a packed value in an `int` local so only the genuinely narrow call site pays for the
  truncation; typing the local `unsigned short` emits `lsl`/`lsr #0x10` at every use (`020307d0`).
- three separate `and rX,sl,#0xff` for ONE integer argument are per-call implicit conversions to an
  `unsigned char` PARAMETER, not source casts. Change the callee's parameter type; do not write
  casts at the call sites.
- the reverse also happens: a redundant `(unsigned char)(1 << (i % 8)) & 0xff` -- cast AND mask --
  is what pins the byte-set tail's `ldrb` before the mask AND.
- when one value feeds two call sites with different widths, SPLIT it: give the call-result site its
  own named `unsigned char` local so the truncation fuses there and not at the other site.
- **byte-param coercion beats a source cast** (`020703c8`; `short` params too, `02157b74`): three separate `and rX,sl,#0xff` for ONE
  integer argument are per-call implicit conversions to an `unsigned char` PARAMETER. Written as
  `(unsigned char)x` casts instead, mwcc CSEs them into one callee-saved register and overflows into
  fp. Declare each callee with `unsigned char index` and pass the int bare.
- `unsigned char` locals get stack slots in REVERSE declaration order (last declared = lowest sp
  offset).
- **a local's width decides where its LOAD is scheduled** (`021665b0`; params and locals typed `short`/`unsigned char` took `0204cd60` from LOOP-SHAPE 3272 to MATCH): locals loaded from `short`
  fields must be typed `short` too; as `int` their `ldrsh` stays grouped instead of interleaving
  with neighbouring zero-inits. Locals reused across switch cases go at FUNCTION scope so the cases
  share one spill slot / register (UNDERGEN 12 there).
- **spilled locals whose reloads are HOISTED above neighbouring stores** (`021eb5d0`; `unsigned char` length vs 7u, `021539d8`): type them
  `short` like the value they hold; as `int` mwcc schedules their spill loads early. Making them
  address-taken or struct members fixes the order but moves the slot: wrong basin. Same function:
  `const short id = ...` fixed a function-wide r7/r8 swap; halfword copy loops were struct
  assignments; 8-byte pairs were `unsigned long long` passed by value.
- **byte-typed FLAG locals block constant propagation** (`021e6a90`): a spilled `int ok = 1` /
  `forced = 1` / `mode = 0` gets folded into its uses; typed `unsigned char` it stays a real value,
  so an inline reuses it (`ldrle` reload), its store sinks below the neighbouring `mov`, and a reset
  that writes `0.0f` emits the `mov r0,r0` copy. Retype flag locals when those three symptoms show.
  Same for a predicated loop flag (`021dcf14`): as `int` its `streq` lands before the adjacent
  conditional store; `unsigned char` puts it after. A global whose store pins later loads there
  was the inline-accessor static (see "A CONSTANT HOISTED ABOVE A GLOBAL'S POOL LOAD").
- a truncation the ROM does UNCONDITIONALLY (`and #0xff`, not predicated `andeq`) is a narrow local bound before the `if` (`unsigned char b = v;`) (`02159d0c`).
- **byte-typed ACCUMULATORS** (`0208ec78`): a counter you increment/decrement takes the target's
  `and rX,#0xff` truncation after each step only if the local is `unsigned char`. An `int` counter
  emits none, and the gap shows up as a whole-function instruction-count shortfall, not as one bad
  register. An `int` counter with an explicit `(i & 0xff) < N` bound emits the same mask but colours
  the scratch registers around it differently (`02072398`): when the mask matches and only those
  register numbers differ, retype the counter `unsigned char` before any colouring lever.
- **signedness picks the mnemonic** (`02005ac4`): `unsigned` operands give `lsr`/`bhi`, signed give
  `asr`/`bgt`. Read which pair the target uses and type the local to match before touching anything
  else.
- **a `short` local keeps its spill reload BELOW earlier stores** (`021ebb90`): mwcc spills a `short`
  scalar with full-word `ldr`/`str`, and hoists an `int` spill's reload above the previous store but
  not a `short`'s. When the only diff is a spilled scalar reloaded one instruction early (above the
  preceding store), declare it `short`. If it is assigned from a call, also declare that callee as
  returning `short`, or the new truncation costs more than it saves; the return type is not part of
  the symbol, so the link is unchanged. Inert for this: `volatile` (reloads everywhere), address-taken
  (loads once), a narrow ternary assigned into an `int`. colorsweep `r51_short_spill`.
- **a local that copies a field takes the FIELD's type** (`0219e384`): `unsigned char kind`,
  `unsigned short value`, `unsigned char tag = detail[1]`, and the callee assigned into `value`
  declared returning `unsigned short`, put a spilled store ahead of the next load at two sites.
  As `int`s, mwcc schedules the load first.
- **a narrow local BLOCKS CONSTANT PROPAGATION** (`ov026:021d8ba0`): where the ROM RELOADS a value
  it could have kept in a register — `ldr r0,[sp,#0x60]` before each of several stores — declare the
  source `unsigned char` instead of `int`. mwcc propagates a constant through `int` locals and emits
  one `mov r0,#0` plus plain stores, but it will not propagate across the implicit conversion, so
  each read becomes a real reload. The narrow local still takes a full word slot, so nothing else in
  the frame moves. That one retype closed a 12-byte whole-function shortfall. Inert at the same site:
  chained versus separate assignments, a pointer round-trip, an aggregate holding the flags; and
  `volatile` is worse than inert — it breaks the loop shape.

## A RESIDUE THAT WILL NOT MOVE IS POINTING AT CODE YOU INVENTED
When a site resists every form you can think of, stop generating forms and read the whole file for
constructs the original developers could not have written. A file-wide `#pragma` is the obvious one,
but so are: a template or macro system for field access (`At<O,T>`, a `PAGE`/`FLD` pair, several
spellings of the same access chosen per site for their codegen), a variable reused to hold two
unrelated things, an expression that stores inside another store's operand, a cast of one object to
several different struct types. Each of those changes what mwcc knows about ALIASING and about which
values are common subexpressions, which is exactly what decides scheduling and register choice — so
one of them is usually why the last site will not move. Replace them with the natural form (ONE typed
struct with named members, accessed as `self->field`) and re-measure, accepting a worse number to get
into the right basin (`ov026:021d8ba0`).

## A CONSTANT HOISTED ABOVE A GLOBAL'S POOL LOAD — THE GLOBAL'S STORAGE CLASS DECIDES IT
Symptom: a store into a global is followed by a store through a pointer, and the ROM emits the
second store's constant FIRST (`mov r1,#3 ; ldr r0,[pc,#N] ; mvn r2,#0 ; str r2,[r0,#0x18] ;
str r1,[sl,#0xeac]`) where ours emits the pool load first. Statement order, declaration order, flags,
compiler builds and every spelling of the two statements are inert.

Cause: mwcc puts every non-const global in a worst-case alias set, and two stores that may alias get
a dependency the scheduler must honour. A static declared inside a function is left out of that set.
The member offset is also counted twice in the lookup, so the object must be at least twice the
member offset plus the member's width, or the lookup falls back to worst-case anyway.

Fix: make the object a function-scope static at its TRUE size — the gap to the next symbol in
`symbols.txt`, never the offsets your function happens to touch. When any other function references
the object, put the static in an inline accessor so it has a linkable name:
`inline T& GetT() { static T s = {...}; return s; }`. A static in the function body becomes a local
`s$N` symbol that no other object can link to. Both halves are required: a function static at a
guessed size, or the true size at file scope, leaves the residue byte-identical (`ov026:021d8ba0`).
Keep every other global `extern`.

## A FRAME THAT IS THE WRONG SIZE IS NEVER A COLOURING PROBLEM
If `sub sp, sp, #N` disagrees with the ROM, every sp-relative byte in the function is wrong and no
register rewrite and no colorsweep run can touch it. Fix the frame FIRST, and measure it:

    python pad/framemap.py <module> <addr> <your.cpp>

It prints both frame sizes, every stack slot each side touches, and the first rank where the two
lists diverge -- everything below that rank agrees, so the object just under it is the one whose
size is wrong.

A `#pragma opt_common_subs off` or `#pragma opt_dead_assignments off` in your file is a SIZE bug
waiting to be counted, not a lever: it keeps a store the ROM folded away and blocks a reload the ROM
performs. Delete both before reading any size verdict. On `main:02048e2c` that alone took OVERGEN 4
to size-exact -- the dead half of `idx = 0; idx = arr[1] - idx;` disappeared with the pragma, and
the store the ROM repeats came back once a `volatile`-qualified read of the owning pointer stopped
propagation from reusing the register (`((Obj volatile*)obj)->ptr->arr[w] = cur;`). Which local
lands in which callee-saved register follows the order the values are FIRST WRITTEN, not the order
they are declared: `int r; int w = 1; r = w;` emits `mov r4, #1; mov r3, r4` where `int w = 1; int
r = 1;` emits the constant twice in the other two registers. Splitting a declaration from its
assignment is therefore the positioning tool, and the two halves move it independently: a local
declared WITH its initializer takes its place from that write, while a bare `T x;` takes its place
from where the DECLARATION sits, wherever the assignment ends up. So hoisting a bare declaration of
the last-computed local above all the others rotates a three-register cycle without moving a single
call (`0215bc9c`, 20 bytes), and hoisting the writes that must own r5/r6/r7 above the first call
fixes the ladder before it (`021767ec`, SHAPE 102 -> REGPERM 9).

`push {r3, r4-fp, lr}; sub sp, #0x38` in the ROM against your `push {r4-fp, lr}; sub sp, #0x3c` is
the SAME 96-byte frame: mwcc reserves an odd word by pushing a scratch register rather than widening
the `sub`. Read it as "my locals are one word too large" -- take a word out of the frame and the
prologue flips to the ROM's form by itself. Every sp-relative offset moves with it, so settle this
before reading any other difference in the diff.

Two levers, in this order:

1. **Merge duplicate block-scoped scratch objects.** mwcc gives every block-scoped aggregate its own
   slot and never overlaps two blocks, so `{ struct Node n; } ... { struct Node n2; }` costs the
   frame twice. One function-scope object reused by both blocks costs it once. On `ov017:0219e384`
   this alone took the frame from 0x298 to 0x228 (two `NodeScratch`, one duplicate `Msg`, two
   duplicate `Vec3`).
2. **Then correct the SIZES of the structs you guessed.** Declaration order and scope do not move the
   frame SIZE: mwcc's total is the same however you arrange the objects (re-measured on `0219e384` --
   moving the biggest local to function scope and declaring it first changed nothing). Only the sizes
   move the frame. Work out what each object must be from the ROM's own slot list: the gap between
   two consecutive `add rX, sp, #N` sites IS that object's size.

   They DO move each aggregate's OFFSET inside the frame, which is what a wrong `add rX, sp, #N`
   with a right frame size is telling you. The local aggregates form ONE band, FLOOR-anchored at its
   lowest address, filled in REVERSE lexical declaration order -- the first declaration takes the
   highest offset and each later one stacks downward -- and every block-scope aggregate is placed
   BELOW every function-scope one, so hoisting one to function scope lifts all the block-scope ones.
   Predict each offset from that rule before you compile; it was exact 4 times out of 4 on
   `ov026:021d8ba0`. The corollary is a trade-off to watch for: an object you move for its own
   offset's sake also shifts every aggregate above it, and a function-scope object you delete stops
   being the spacer that held the band where the ROM has it.
- `unsigned short saved = f->x; f->x |= 0x4800;` instead of an int temp + cast stops the u16
  truncation sinking to the store (`02037934`).

## CONSECUTIVE BITFIELD WRITES MERGE INTO ONE STORE — count the stores, not the writes
For a run of N consecutive writes to the same bitfield STORAGE UNIT, mwcc emits N read-modify-write
sequences and exactly **ONE** store. The merge is keyed on the resolved storage-unit address, not on
how the lvalue is spelled: two differently-named bitfield structs inside a union, two union members
at the same offset, and a different declared base type (`unsigned long` vs `unsigned int`) all merge
identically (`main:0209a218`).

**Bitfield vs raw-word spelling moves scheduling function-wide** (`0215436c`, 26 -> 0). Model a flags
word as a union and write each bit the way the ROM does: 1-bit bitfield writes for single-bit sets,
a raw-word `|= 0x400` where the ROM ORs a constant. No statement order reproduces the tie-breaks.

So a ROM with TWO stores to one slot a few instructions apart is telling you its writes are in two
DIFFERENT runs, and the C must contain whatever ends the first one. **No pragma reaches this** —
`opt_dead_assignments`, `opt_lifetimes`, `peephole`, `opt_common_subs`, `opt_dead_code` and
`optimize_for_size` were all measured inert with the pragma plumbing verified working, so the merge
is front-end/IR, not an optimizer pass.

A may-alias cast (`*(unsigned int*)((char*)&rec + 4) &= …`) DOES split the store, but it marks the
object address-taken: it costs an `add rN, sp, #0` and perturbs the surrounding schedule. If the ROM
has neither, the ROM's source contains no cast and no pointer write — look for a write to a
different storage unit, a call, or a branch between the two instead.

Corollary for the residue: widening or splitting a field changes the RUN LENGTH, so `f4e:7` split
into `f4e:3 + f4f:4` buys an extra instruction slot. That is a diagnostic, not usually the answer —
it produces two narrow `bic`s where a single field produces one wide one.

## NAMED LOCAL vs CSE TEMP — this is what decides SPILLING
mwcc gives a memory home to a NAMED local and keeps CSE temporaries in registers. When your spill is
in the wrong place, the lever is whether the value has a name at all:

- REMOVING a name is as much a lever as adding one. `0205bd78` — deleting the count/ptr locals and
  reading `s->fieldNN` at every site moved the spill onto the loop bound the target spills
  (209 -> 192 -> 27 bytes). `02012538` — deleting a named bool and writing the two stores out under
  `if`/`else` let mwcc keep the sequence in `r0` and predicate it.
- `02098a84` — the converse across a LONG range: a value the ROM holds in a callee-saved register
  across a whole block is one the source reuses at the far end. Bind it once (`resetSlot =
  &arr[last]`) and use that variable at both ends; two textually identical `&arr[last]` expressions
  resolve independently and never produce the long-lived register.
- the reverse, same function: materialise both bounding-box sums into locals
  (`int right = a + c;`) BEFORE the `if`, which stops mwcc sinking the ldrsh loads into the
  short-circuit chain and emitting `addge` instead of an unconditional `add`.
- `02089630` — hoist a field into a named `unsigned int` local before a call to PARK it in r7.
- `020e1438` — write the store block as `out[count].field` (indexed) instead of hoisting an entry
  pointer, to force the mul result into r6 and the address into r2.
- `0208ec78` — give a bitfield extraction its OWN named local (`mask = 1u<<j; idbits = (w<<7)>>24;`)
  instead of inlining one side under `&`/`|`. That forces mwcc to materialise `idbits` fully into a
  register and fuse the mask's shift into `tst`/`orr`, which is the fusion side the target chose.
- `02084a64` — initialise a shared result local AT ITS DECLARATION (`unsigned int result = 0;`)
  rather than leaving it uninitialised. It then holds one stable register across a whole switch, and
  every case stores into it via `mov` — which is what produces a shared branch-to-tail epilogue
  instead of an inline `pop` per case.

Ask "does the target keep this in a register or on the stack?" and add or remove the NAME to match.

## BREAKING CSE — `volatile` is the tool, a plain cast is not
**`colorsweep` already tries this class for you** (`r36` on extern globals, `r37` on a pointer's
fields, including parameters) and it has been run on your starting artifact before this doc was
built. Reach for these by hand only when the shape below is one the rules cannot express.
`volatile` also PINS LOAD ORDER, not just reloads: plain locals let mwcc sink each load to its
first use and reorder them into use order, where volatile keeps declaration order (`0215b138`,
two `ldrsh` field reads, 4 bytes).
**A CACHED LOCAL IS A CLAIM THAT NOTHING IN BETWEEN WROTE THAT MEMORY, and the ROM rarely makes it.**
Re-read instead of caching whenever calls sit between the two uses: a field after three mutating
calls (`02184bbc`, the target's second `add`/`ldrsb` pair), a predicate the function has itself been
mutating — call it AGAIN rather than testing a held flag (`021ea85c`, 500B, matched on the first
compile) — and a plain redundant `ldr`, written by naming the field again at the use site
(`020307d0`).
`02156054` — a cast on an INDEX does break CSE. With `unsigned char j` and a loop condition that
reads `tbl[j]`, the ROM loads `tbl[j]` again in the body. Write the body's read as `tbl[(int)j]`: a
different subscript expression, so mwcc reloads. Plain `tbl[j]` reuses the condition's register,
and the function comes out 4 bytes short.
`02188d9c` — `const` is the OPPOSITE lever and it moves whole blocks, not one load: declaring an
extern lookup table `extern const short tbl[]` means `int` stores through an unrelated pointer can
no longer alias it, so eight `ldrsh` hoist above the store block and their index adds materialise up
front. When the target loads EARLY and you load late, ask what mwcc thinks your stores might alias.
`02188ba0` — a plain source split forces a reload without `volatile`: breaking `a && b` into a named
flag set by the first test and read by a SECOND `if` makes mwcc re-load the pointer field on the
equal path. Same address: a constant already live in a register changes how a NEIGHBOURING constant
is materialised — with `int missing = 1;` in scope above the bind, `-1` came out as `sub r1, r3, #2`
instead of `mvn r3, #0`. If a materialisation is one instruction off, look at what is live, not at
the expression.

When the target RE-COMPUTES register arithmetic you keep in a callee-saved register (`lsl r0, r6, #2`
again after a call, while ours holds `j*4` in `sl` from the top of the loop): change the TYPE of the
second expression, not its spelling. `(unsigned int)j * 4 + 2` is a different CSE key from `j * 4`
and costs no code; `j << 2`, `4 * j`, a second inline call and `(unsigned char)j` are either the
same key or add an `and`. The freed register then goes to the loop-invariant the ROM hoists there
(`ov017:0219bfb4`, 182 -> 0).

When the target RE-LOADS a value you are holding in a register:
- `*(volatile int*)obj` forces the reload. A plain `*(int*)obj` cast barrier is **inert**
  (`022410d8`, measured both).
- A register restored from a saved field, `ldr r0, [base] ; ldr r3, =mask ; ldr ip, [self, #off] ;
  and r0, r0, r3 ; orr r0, r0, ip`, is an SDK setter whose mode parameter is an ENUM:
  `static inline void GX_SetOBJVRamMode(GXOBJVRamMode mode) { DISPCNT = (unsigned int)((DISPCNT & ~0x300010) | mode); }`
  called as `GX_SetOBJVRamMode((GXOBJVRamMode)self->saved)`. Written inline, every operand order and
  local either emits `orr r0, ip, r0` or colours the register bases one lower; an `unsigned int`
  parameter is inert too (`ov003:0217e6b0`, the last 5 rows).
- A register field CLEARED with a hoisted `mov rN, #0` feeding an `orr` (`ldr ; bic ; orr rN` where
  ours emits `bic` alone and schedules the neighbouring RMW differently) is an SDK setter called
  with 0, not `REG &= ~mask`: `static inline void G2_SetBG3Priority(int p) { BG3CNT =
  (unsigned short)((BG3CNT & ~3) | p); }` then `G2_SetBG3Priority(0)`; same for
  `POWCNT1` bit 15 via `(sel << 15)` (`ov017:021c0850`).
- A pool-literal load scheduled AFTER a neighbouring `ldrh`/`sub` pair when the ROM has it first is
  a wrong prototype on a nearby SDK call: `GX_DisableBankFor*` / `Disable*VRAMBanks` return
  `unsigned int` even when discarded; declared `void`, the scheduler reorders (`ov017:0218b688`).
- A byte store through a pointer loaded from a struct slot that takes the swapped scratch pair is a
  typed accessor plus an inline member setter: `GetBattleHud(res)->SetVisible(1)`, not
  `*((u8*)res->ptr + 0xa) = 1` (`ov017:021acdf4`).
- A register copied to the stack TWICE and then reloaded (`str r1, [sp, #4] ; ldr r3, [sp, #4] ;
  str r3, [sp, #8]`) before bitfield reads is an inline returning the register struct by value:
  `static inline GXDispCnt GX_GetDispCnt(void) { return *(volatile GXDispCnt*)&DISPCNT; }`. Without
  `volatile` there is one stack copy and the frame is 4 bytes short (`ov003:0217e6b0`).
- An I/O register (`0x04000000`…) read twice in one update is the SDK's volatile register type
  behind a get/set inline pair. `GX_SetVisiblePlane(GX_GetVisiblePlane() | 0x10)` needs both as
  `static inline` functions over `volatile unsigned int`; one inline expression orders the `orr`
  before the `bic` (`02065418`). A plain `GX_SetVisiblePlane(obj->planes)` read once colours its
  scratch registers only as `(obj->planes << 8) | (reg & ~0x1f00)` (`021c17fc`, `021a9768`).
- `extern void (* volatile arr[])();` stops a handler load being sunk past a count store
  (`020015e8`).
- To defeat an UNWANTED CSE, change the expression's shape so mwcc does not recognise it: where
  parallel tables share an index and stride, `*(const char* const*)((char*)tbl + (int)i * 12)` is
  not the same subexpression as `tbl[i].value`, so the ROM's per-table recompute comes back
  (`02170a7c`, UNDERGEN 8 to shape-clean). Mixing access forms splits a pointer CSE the same way —
  cached local for the read side, global-deref for the store side (`0223d0b4`).
- The MIRROR CASE: when the target reuses the register from a nearby COMPARE, write the arithmetic
  on the literal, not on the field. In a `field == -16` arm, `-16 + 15` reuses the compare's `mvn`
  register; `field6a + 15` re-loads the field and picks a different one (`020db3f4`).
- a volatile read at the top of a loop body forces the per-iteration re-load that a guarded do-while
  otherwise CSEs away (`020015e8`).
- read the field TWICE rather than reusing an expression: `n = l->count; l->count = l->count + 1;`
  not `n + 1` — that supplies the extra mov/ldr the target has (`0209f030`).

## CONTROL-FLOW SHAPE — the lever for predication, tail-sharing, and branch order
mwcc predicates small arms and cross-jumps shared tails. The SHAPE of the source decides both, and
this family closed more large functions than any expression change:

**A CONSTANT THE ROM ISSUES BEFORE AN ALU OP WAS NOT IN THAT BLOCK** (`021e3178`). The first-pass
scheduler breaks ties by: critical-path slack, successors made ready, path height, then operand class
(an instruction that reads a register beats `mov rX,#imm` / a pool load), then IR order. So in one
first-pass block the constant always sinks; declaration order and loop spelling are inert. When the
ROM has `mov r3,#0` before `add r2,r0,#0x14`, the `add` was a loop-invariant address written INSIDE
the loop body and hoisted to the preheader after pass 1: move the address from a local bound before
the loop to its use in the loop, and stop the offset folding into the load with an int cast
(`((unsigned char*)((int)node + 0x14))[j]`). `pad/renum/schedwhy.py <src> <mod> <addr> <pick>` prints
which tie-break decided a schedforce pick. When 14 or more values are live in a block, pass 1
switches to a register-PRESSURE score: start 16, each register operand used for the LAST time
subtracts 1 + max(1, live-14), each def still read later adds max(1, live-14); lowest wins, ties to
earlier IR; latency and height ignored. r0-r3 between two calls in one block stay counted. Call
arguments are always set up in argument order, so a later argument's load issuing before an earlier
simple argument means the block was under 14 live, the calls sat in separate blocks, or that load
was its base's last use. Declaration order, temps and argument spelling cannot change it. A block
also splits at a named label, a C++ exception-region change, or after ~100 pcodes (`02157d40`,
`02165490`). Tools: `pad/renum/pressforce.py`, `schedrule.py`, `splitforce.py`, `irdump.py`. In pass 2 two constant moves tie and IR order decides:
when the ROM sets `r5final = 0` before `kind = 0xf`, the variable whose value is used FIRST in IR is
created first; there the else-arm loops were counting with `r5final` (`0216033c`).

**A LONG STRAIGHT-LINE BLOCK IS SPLIT BY INSTRUCTION COUNT** (`0218b710`): mwcc ends a basic block
after the statement where the generated pcode count passes 100, and CSE/scheduling do not cross
that split. A constant CSE'd where the ROM reloads it from the pool (`add r1,r5,#3` for 0x7536 next
to 0x7533), or the reverse, means the split falls one statement off: change how many instructions
the statements BEFORE it generate (`int` returns, `char buf[0x80] = {0};` instead of a `__clear`
call). Pool words are emitted in first-use order: swap two stores to swap two pool words (`021e8d20`).

**A STATE CHAIN THAT IS SHORT** (`0215cc70`, UNDERGEN 64 -> 0): a materialised flag test
(`tst; movne #1; moveq #0; cmp`) is a one-line inline returning `int`, not `bool` (r20); a call
result tested `mvn; cmp; beq far; cmp #1` is `if (r != -1) { if (r == 1) ... } else ...` (r24); a
field the ROM RE-LOADS after a conditional region is read there through a one-line `static inline`
reaching it by DIFFERENT arithmetic, one spelling per reload (r29). Helpers with control flow or
several statements are not inlined: write those bodies in place.

**THE ORDER OF TWO PREDICATED ARMS IS THE SOURCE ORDER OF THE IF/ELSE** (`02174a80`): `strhle` before
`asrgt` means the `<= 0` arm was written first: `if (x <= 0) { x = -1; } else { count++; }`.

**AN ARM THAT REFUSES TO PREDICATE HAS A CALL YOU LEFT OUTSIDE IT** (`0216acf0`). Read where the
ROM's `blt` lands: if it also skips the next call, that call belongs INSIDE the `if`
(`if (idx >= count) { idx = count - 1; Call(list, idx); }`). An early-reset arm that jumps past the
main body was a `goto` to a label after it, not `if`/`else`.

**AN INTERLEAVED MATERIALISATION IS THE SCHEDULER, NOT YOUR STATEMENT ORDER.** When the ROM threads
one statement's instructions THROUGH another's, no source order reproduces it — mwcc sinks each
definition to its use and then schedules. `main:0202c04c` (22B) and `main:020a1180` (34B) are both
this; every reordering gates the same or worse. Spend the session elsewhere.

**WHERE YOU ROOT A STRUCT DECIDES WHICH TEMP IS ALLOCATED FIRST.** Two spellings of the same field
can emit byte-identical instructions and still differ in registers. When a big constant offset is
not an encodable ARM immediate, mwcc keeps the partial sum as a temp and folds the rest into the
load offset — and the temp's allocation order follows how DEEP you rooted the object. Re-root one
level down (`(List*)(base + 0x9c + 0x400)` with the field at `+0x1c`, not `(Owner*)(base + 0x9c)`
with it at `+0x41c`) to make the POINTER the first-allocated temp and the loaded byte second.
`ov020:0218d32c` (792B, REGPERM 16). Naming the pointer, naming the call argument, flat `p[0x41c]`
indexing, `-O3`/`-O4`/`-opt speed` and an 80-compile colorsweep are all inert on the shallow root.
Two counters that must sit in the LOWEST stack slots after the arrays are one `int cnt[2];`
(`cnt[0]`, `cnt[1]`), not two scalars, which slot in declaration order (`021ed634`).
`add r0, sp, #0xc8; ldrsb r0, [r0]` (address formed, then loaded) is a stack buffer tested through a
`char*` local: `char* n1 = bufc8; if (n1[0] != 0)`. `bufc8[0]` folds to `ldrsb r0, [sp, #0xc8]`
(`02185c90`).
Two bytes copied as a unit from a table are a `struct { unsigned char v[2]; }` assignment from an
`extern const` array of that struct: mwcc copies it byte-wise (`ldrb`/`strb` pairs), which a
function-wide SCHED residue at many sites can come from (`021560e4`, SCHED 65 -> 0).
A struct temporary's STACK SLOT follows its inline nesting depth: a copy made inside an inline that
is itself inlined lands in a different slot band than the same copy written in place. When only
Vec3-style temp slots are wrong (OPERAND on `add rX,sp,#N`), move each copy one inline level in or
out; a named const reference to a call result is laid out as a local, not a temp (`0216ba70`).
When an attempt is NOT matching, suspect your own scaffolding first: run `python $KIT/plausible.py <file>`
and remove what it flags (volatile locals, unused address-taking pointers, `x = x;`, chains of invented
one-line inlines). Those reach a lower number in the wrong basin; the original never had them.
Never `const int CAP = 999;` as a block-local: mwcc folds every use but still emits `CAP$N` into
`.rodata`, and integration refuses the unreferenced object (DATA-UNPLACED). Use `enum { CAP = 999 };`
or the literal (`0216033c`).
A zero-fill of a local buffer is `__clear(buf, n)`, not `memset(buf, 0, n)` (`02021f88`, which also
used the chained adds below for `zone + 0x2000` and separate out-param locals per state for the frame).
A store offset the ROM splits in a specific order (`add #0x264` then `add #0x2400` for `+0x2664`)
comes from two chained one-line static inline pointer adds, `Off2400(Off264(p))[0xb6]` (`021643d4`).
The reverse also happens (`021729ac`): a FLAT non-encodable offset (`void* list = src->list`, list at
`+0x684`) makes the partial sum a compiler temp coloured below a hoisted address, where a named
pointer to the `+0x284` sub-struct took the other register. Try both roots.
`0215b520`: name BOTH the rooted base (`ents` at `+0xa70`, array at `+0x1000`) and the CSEd offset
(`off = (k + 4) * 0x88`), define them where the ROM first needs them (after the first store, not at
the loop top), and use them only at the call sites the ROM computes that way. A `const` byte table
lets its `ldrb` hoist above a volatile store (also `02173954`, colorsweep r60). A pointer table the
same: `extern T* const tbl[9];` stops its load being ordered before a global store (`020d22f4`).
Inside a loop the same re-rooting is spelled as raw pointer arithmetic on the element:
`((int*)o + i)[0xac/4]` rather than `o->idx[i]` (`0218f088`).

**NAMING AN INTERMEDIATE DECIDES WHERE IT MATERIALISES.** One rule, four proven instances. An
expression written inline is sunk to its use and folded; bound to a local it is computed where the
binding sits, in its own instruction. Reach for this whenever the residue is one instruction in the
wrong place, in the wrong register, or missing entirely.

| shape | inline form | bound form | address |
|---|---|---|---|
| loop-invariant address hoisted ahead of neighbouring loads | `p = (char*)obj + 0x10;` | `p = (char*)obj; p += 0x10;` | `0202b900` SCHED 14 -> 0 |
| a call inside a compare always takes Rm, for BOTH operand orders | `if (x == Call())` | `int a = Call(); if (a == x)` — declared BELOW the loaded local | `02053634` 2B -> 0, `0209bed8` |
| a condition the ROM tests twice | folded into the enclosing `\|\|` | `int ok = (... == 3); if (ok == 0) return 0;` | `02094fe4` 12B |
| a constant the ROM materialises rather than folds | `x >= 0` (folds to a `cmp` immediate) | `x > -1` (forces `mvn r1,#0` + `cmp`) | `0209c840`; run it BOTH ways — `x <= -1` -> `x < 0` removes an `mvn` ours emits and the ROM does not (`02081f20`, 23B -> 4B) |
| a `+K` folded into a cast over a struct FIELD | `p = (Stage*)(h.ptrC + 0x1000);` | `p = h.ptrC; p += 0x1000;` — keep the BASE type, cast after | `021db524` 13B -> 0. The add then emits in place, its dead register is reused by the next `mov #1`, and two pool constants fall back into the ROM's slots. `r39_split_pointer_add` does NOT reach this: its pattern needs a bare identifier base and the `+` OUTSIDE the cast |

Bounding rules for all four: the bind must sit BELOW the value it is compared against (above gives
SHAPE); the local's type is free; `(char*)(obj + 4)` and `&obj[4]` are the same instruction as the
inline form and inert; inlining an already-bound expression at its use site DELETES the instruction
(UNDERGEN). Sibling sites in one function may legitimately want opposite forms — match each site to
its own instruction.

**AN EXPLICIT NO-OP `case` COSTS A DETACHED COPY.** `case 0: return;` written out gives mwcc a real
arm to branch to; deleting it and letting 0 fall through the jump table unhandled — exactly as the
out-of-range values already do — lets the shared one-instruction epilogue inline into the table slot
instead. Pair it with merging sequential `if (x) return;` checks into one `if/else` chain so the
fallthrough converges on the call the ROM shares. `main:02076df4` (488B) closed 12 bytes on the two
together. A switch arm that does only what "no arm" does should not be written.

**Predication vs branch, measured (`pad/probe_ifconv*.cpp`, `pad/probe_pragma_scope.cpp`):**

- **A pragma inside a function body does nothing** — mwcc binds optimization pragmas where the
  FUNCTION is defined, so bracketing one around a single `if` compiles byte-identical. "Tried it
  per-case, tightly bracketed" means the pragma never ran. A probe pragma goes at function scope.
- **`optimize_for_size off` at function scope does defeat if-conversion:**
  `tst / movne / strne / movne / strne` becomes `tst / bxeq lr` + the plain arm.
- **The boundary is a size: 4- and 5-instruction arms predicate, 6 branches** with no pragma, for an
  arm that FALLS THROUGH; an arm ending in `b` counts the `b`, so it branches at 5 body instructions.
  So "1 instruction short AND predicated where the ROM branches" is ONE bug — find the missing
  instruction and the branch is free.
- **An arm the ROM branches over that ours predicates.** Each arm is if-converted on its own: every
  instruction must be predicable and the arm must be <= 5 instructions (`b` included) at `-O2`. Game
  code is all `-O2`, so when the ROM leaves a short arm as a branch, the ORIGINAL arm held something
  non-predicable or was longer: a call, a volatile access, a store through a different base, an extra
  instruction. Find it. `$KIT/pad/renum/ifcvtrace.py <src> "<flags>"` lists every arm, its size and
  why it was or was not converted. Only in third-party library code (NitroSDK, DWC/NHTTP, the
  CodeWarrior runtime, MSL) can it instead mean the library was built `,p` (limit 3): there emit
  `BLOCKED <addr> SPEEDTU` (`0224185c`, `0200df80`).
- **A call in the arm always forces a branch**, including a hidden one (`x / 7` -> `bl __div`); so
  does a loop.
- **Inert, stop re-testing:** `goto` over the arm, `volatile` on the store target, and binding the
  guard to a local (`int g = x & 0x20; if (g != 0)` gives flag-setting `ands`, still predicated —
  only a use of `g` AFTER the `if` splits it).

- **if/else-if chain, not separate ifs** (`0205a528`, four magic-string checks): the first arm's `&&`
  false-path falls into the second compare; arms 2-3 need `else if` so their inner
  `if (fN()) return 1;` false-path BRANCHES past the rest to a shared `return 0`. Measured: separate
  ifs 34 bytes, nested-if-with-explicit-return-0 10 bytes, switch-on-call-result 102 bytes.
- **nest to force a shared tail** (`02077500`): putting three `return 1` paths inside
  `if (f() != 0) { ... } return 1;` makes them branch to the shared tail instead of emitting
  `movCC r0,#1`.
- **write it into BOTH arms** — duplication is a placement tool, with two distinct outcomes.
  `dst->f0 = dst->f4` in each arm makes cross-jumping merge only that store, restoring the target's
  `strhne` + `bne` (`02089630`); a pointer bind and its `src += 0x28` advance at the top of each arm
  get CSE-hoisted into the dominator block BELOW the tag load, so the load takes r0 and the advance
  materialises as a real `add r4, r4, #0x28` instead of folding into its merge-block uses
  (`0201d638`, 2452B).
- **one `||` condition, not two ifs** (`02003ddc`): `handler == (fn)1 || (handler == 0 && slot == 1)`
  gives the shared `mov r0,#0; pop` then-block plus the cmp/cmpeq fold.
- **switch instead of if/else** (`0223c308`) to stop mwcc hoisting a shared constant above the `bne`.
- **order the diamond** (`020ba264`): make the block the target falls through to the if-body and the
  other the else (56 -> 22 bytes).
- **READ THE POLARITY OF A PREDICATED COMPARE CHAIN — it names the operator.** `cmp a,b` +
  `cmpne`/`cmpne`/`cmpne` + `moveq r0,#1` is `a==b || c==d || e==f || g==h`: the first EQUAL sets Z
  and skips the rest. The same chain with `cmpeq` is the `&&` of the same equalities. They are not
  two shapes of one condition, they are different conditions, so a `cmpne | cmpeq` column in the
  diff means the SOURCE has the wrong operator, not the wrong colouring (`020cddec`).
- **block the range fold** (`0205337c`): an `||`-chain of equality tests written as
  `!(a != x && a != y && a != z)` stops mwcc lowering it to `(unsigned)(a-x) <= 2` and forces real
  cmp/cmpne/cmpne/bne with a non-predicated then-block.
- **a chain that opens with `cmpne` right after a `beq` on the same value** repeats the test the
  branch above already decided: `if (s == 0) {...} else if (s != 0 && s != 1 && s != 2)`. Without
  the redundant `s != 0` the chain opens with a plain `cmp` (`021dbf04`).
- **backward `goto` into a label** reproduces mwcc's tail-merge of a duplicated reset block
  (`02002b90`).
- **EARLY RETURN vs SINGLE EXIT decides WHERE a returned constant is materialised — both directions
  are levers.** Single exit keeps it live past the test so it must be materialised first
  (`020a1bb4` + its twin `020a1ccc`); early return out of the success branch instead makes the FAIL
  path materialise its `NULL` before the cleanup calls rather than sinking it to the final return
  (`0207568c`, 296B, 53 bytes — but only with `#pragma opt_propagation off` at function scope, which
  is evidence the real form has not been found: a pointer round-trip usually reproduces it without
  one). The single-exit direction, measured: an early return makes K dead on the fall-through path,
  so mwcc folds it into a predicated instruction instead:

        cmp r7, #0x23 / mov r0, #0   / pophs {...}      target
        cmp r6, #0x23 / movhs r0, #0 / pophs {...}      from `if (id >= 0x23) return 0;`

  Write it single-exit — `int ok = 0; if (id < 0x23) { ...body...; ok = 1; } return ok;` — and K is
  live past the test, so it has to be materialised first. You do NOT lose the early exit: mwcc still
  emits the conditional pop. `colorsweep` applies this automatically (rule r23), so reach for it by
  hand only when the returns are not plain integer literals.
- **A `switch` dispatches through a JUMP TABLE only if the last arm is a real `case N:`.** Writing
  it as `default:` makes mwcc emit a compare chain instead. `02005ac4` needed an explicit `case 3:`
  to get the target's table dispatch.

## LOOP FORM — mwcc does NOT rotate loops
A `while` emits a `b` to a bottom test; it will never become the target's top-tested do-while. Pick
the form directly:
- guarded do-while `if (n != 0) { do { ... } while (n != 0); }` for a top-tested loop (`02002b90`,
  `022378e4` — both loops).
- `while (y < h) { ...; if (count >= max) break; }` plus an explicit `max == 0` early return
  (`020e1438`).
- A SIZE gap of a few bytes spread over several loops is usually this and nothing else: rewriting
  three zero-fill loops as guarded do-while moved `0x1f0 -> 0x1fc` in one edit (`020c0a40`). Fix
  loop form before you touch registers.
- Materialise a loop BOUND as its own named local declared ABOVE the `for` init clause. Folded into
  the condition, mwcc computes it last; hoisted, it is computed before the init `mov`s, which is the
  ROM's order (`020e2110`).
- `while (node)` with a break plus a redundant post-loop null test (`021982ac`).
- Test at the TOP and a `b` back to it at the bottom, with no guard: `for (;;) { if (!a || b) break; ... }`
  (`0218b5fc`, 137 bytes to MATCH in one edit; the wrong loop form also recoloured the whole function).

## EXTERN "C" AND MANGLING — the WRONG-SYMBOL trap
Whether a declaration mangles decides both the emitted symbol and the argument widths. Get this
wrong and a byte-perfect function still fails the gate:
- if the config binds a plain `func_0XXXXXXX` name, the definition MUST be
  `extern "C" ARM void func_0XXXXXXX(...)` — a semantic C++ name mangles and fails WRONG-SYMBOL
  (`02020fc4`, found only after the bytes already matched). The bound name can also carry a
  DIFFERENT overlay number than the module you are working in — `021d8a40` is bound as
  `func_ov023_021d8a40`, not ov026. Read the name out of the config; never construct it.
- to call a C++ callee with specific argument widths, declare it `extern "C"` under its MANGLED name
  (`_Z25EnqueueEventTag8_021cc4b8hhi`) with the widths you need — an int param home is then emitted
  in declaration order with no `and #0xff` truncation at the call (`021cc388`).
- a symbol may mangle as void-param even when you pass arguments (`_Z21BlankFunction020a28dcv` with
  4 args, `021982ac`).
- **but pool-word FUNCTION ADDRESSES resolve to mangled symbols** (`_Z22TransferMainObjPaletteiij`)
  — declare those as plain C++ declarations, NOT `extern "C"` (`0222d1b8`).
- Mixed in one file is normal and is what let `0202b0f4` match FIRST TRY: mangled `_Z` callees as
  plain C++ declarations, `func_` callees as `extern "C"`, in the same source.

## BIG OFFSETS AND ADDRESSING
- **fields past 4095** (`obj + 0x1000` region) MUST be plain nested-struct member chains
  (`obj->sub.f28`) — never a local `Sub*` and never an inline accessor. The 4095 immediate limit
  already forces an `add rX,base,#0x1000` split, so mwcc rematerialises the base per block; a named
  local or `GetSub()` gets CSEd into a parked callee-saved register and adds a register to the push
  (`0202aec0`).
- use the project's existing one-pointer idiom rather than a base + re-offset local: canon splits the
  same constant either way but colours the two registers in the opposite order (`021b4f48`).
- read halfword field offsets straight off the canon splits: `add #0x100` + `ldrh [r0,#0x88]` means a
  u16 at 0x188 (`020d59bc`).
- a byte read and later cleared through ONE `add rX,gs,#0x7000` (`ldrb [rX,#0xf72]` …
  `strb [rX,#0xf72]`) is a member-array access, `*(unsigned char*)&gs->unk_6fc0[0x7f72 - 0x6fc0]`.
  `((unsigned char*)gs)[0x7f72]` splits the store as `#0x72`+`#0x7f00` into a second register;
  the plain `char` member gives `ldrsb` (`main:02000c9c`).

## DUPLICATE POOL WORD — the alias also works for DATA and BSS
One symbol referenced twice ALWAYS dedupes to a single pool word. Declare a second extern named
`<symbol>_dup` (or `_arg`) and use each symbol at one site. Do NOT edit `symbols.txt`: integration
adds the alias line itself, and wgate accepts the `_dup`/`_arg` name without it. The line it writes:
(`02155e28`):
- **data**: `data_ov031_02249b54_arg kind:data(any) addr:0x02249b54` — plain `kind:data(any)`, and
  **no `size=` field**, because data entries in that file carry no size. 149 bytes -> MATCH in one
  step (`02216b90`).
- **bss**: `data_ov031_02290c94_dup kind:bss addr:0x02290c94` (`0222d1b8`); dsd 0.10 rejects a `size:` here.
Keep the field reads on the original symbol and pass the ALIAS to the call that needs the address.

## A SMALL INTEGER LOADED FROM THE POOL IS AN OVERLAY ID — write `OVERLAY_ID(n)`
`ldr rN,=0x18` where a C constant would give `mov rN,#0x18` is the address of the LCF symbol
`OVERLAY_24_ID`. No constant spelling reproduces it. `#include "System/OverlayId.h"` and pass
`OVERLAY_ID(24)`; `relocs.txt` already carries the `kind:overlay_id` entry (`ovidrelocs.py`).
Consumers: `func_020a1940`, `func_020a1bb4`, `IsIndexMappedToSelf020a18f4`,
`BackgroundLoader::QueueLoadOverlay`, the third argument of `SetFields30And34_021b2bd0` (`02160da0`).

**The same ID re-loaded from the pool at the unload** (`ldr r0,=OV` before BOTH `func_020a1940`
and `func_020a1bb4`, no callee-saved copy) means the ID is POINTER-typed. `OVERLAY_ID(n)` casts to
`unsigned int`, and mwcc CSEs an integer into a callee-saved register; it never CSEs a pointer
constant, it rematerialises it. Declare the callee `func_020a1940(unsigned int* id)` in that file
and pass `&OVERLAY_19_ID`. Eight load/unload pairs, 36 bytes (`main:02000c9c`).

## RELOCS — an array-indexed base needs its OWN extern symbol
`&data_02107870[i]` fails the reloc check: mwcc addend relocs resolve back to the base symbol. Each
argument needed its own extern (`data_021078a8`, `data_021078e0`) while the array-indexed STORE base
kept its `+0x38` / `+0x70` immediates (`0204a4ac`).

**A VTABLE POINTER IS AN ADDEND RELOC — give it its own symbol.** The vptr store loads
`_ZTV<Class>+8` from the pool, and as an addend it fails the same check. Add an alias in
`symbols.txt` AT that address and use it: `data_ov033_022a2a14_vptr` turned `RELOC-WRONG 1` into
MATCH (`022a2998`).

## A BLOCK REPEATED VERBATIM IN THE ROM IS A MACRO — `-inline noauto` WILL NOT INLINE IT
Under this build mwcc inlines an `inline` function only when its body is about five statements or
fewer, and never a template whose return value is a named local. Anything larger is emitted out of
line with a `bl`, and the function comes out UNDERGEN by the block size times its copies. A
repeated eight-call sequence (load overlay, allocate, ctor, stub, run, dtor, free, unload) is a
`#define … do { … } while (0)` macro. A block that appears once at the loop head and again at the
loop tail, with the tail branching back to the head, is written twice in the loop body; mwcc does
not duplicate it (`main:02000c9c`).
A step macro whose multi-term sum adds in the wrong order: bind the leaf operand and the sum to
block-scoped locals inside the macro (`{ unsigned long xv = (w); unsigned long sum = a + f(b, c, d) +
xv + *t++; a = b + ROTATE_LEFT(sum, s); }`), and give each unrolled round its own `do` counter,
declared after the word pointer (`020c04e8`).

## A DESTRUCTOR WRITTEN AS A C++ DESTRUCTOR EMITS THREE OF THEM
`Class::~Class()` makes mwcc emit the D0 (deleting), D1 (complete) and D2 (base) variants, and under
`-nodead` all three are kept — the ROM has one, so the slot OVERGENs and the whole module shifts.
Write the one the ROM has, under its mangled name, and none of the others exist:

    extern "C" ARM Ov33BackgroundLoader* _ZN20Ov33BackgroundLoaderD1Ev(Ov33BackgroundLoader* self);

It returns `self`, like the real D1. `022a2998` went `OVERGEN 0x68 -> 0x20` on that line alone. The
same reasoning applies to a constructor's `[base]` duplicate.

An implicit `operator=` is the same trap (`ov026:021d8ba0`, `021dddcc`). A struct with NAMED members, declared
bare and then assigned — `Vec3i v; ... v = data;` — makes mwcc emit an out-of-line
`_ZN5Vec3iaSERKS_` and call it; `-nodead` keeps that symbol, so the file can never match however
good its byte count looks. Give the struct ONE ARRAY member (`struct Vec3i { int v[3]; };`) and the
copy inlines to the ROM's `ldr [pc] / ldm / stm`.

## DIVISION AND SMALL HELPERS
- `% 3u` (unsigned) binds `_u32_div_f`; a signed modulo binds `_s32_div_f` (`0208a5d8`). An
  `unsigned char` promoted to int gives the SIGNED helper (`02020fc4`).
- a `static inline` helper is emitted out-of-line as a `bl`. Expand it into a macro ternary —
  `(v) < 0 ? (v)%8 + 8 : (v)%8` (`02027438`).
- spell a shifted mask as `((e) << 16) & 0x1ff0000`, not `((e) & 0x1ff) << 16`, which mwcc lowers to
  `lsl#23/lsr#7` instead of the pooled AND (`02027438`).
- an inline `lsr / rsb / ror` bit-trick where you emit a `bl _s32_div_f` means your DIVISOR GUESS is
  wrong, not your expression: mwcc only open-codes a modulo by a POWER OF 2, so a ROM doing the
  trick is doing `% 4`, never `% 3` (`0215e250`, OVERGEN 8 to size-exact).
- a skip inside a `for` belongs in the loop's own increment clause, not an `if { i++; continue; }`
  body -- the clause form branches straight to the shared tail the ROM shares (`0215e250`).

## BITFIELDS ARE REAL MEMBERS, NOT MASKS
`(x & 0xf) == 0` emits `tst` + `bne`. The target's `ldrb / lsl#0x1c / lsrs#0x1c / bne` comes from an
actual `unsigned char mode:4` member (`02020838`). A 7:1 pair at 0x2c reads as `lsl#25/lsr#25` and
`lsl#24/lsr#31` (`021982ac`). Model the bitfield; do not mask.

This applies to a value a CALLEE hands back as much as to one you load: declaring the packed result
fields as real bitfields, copied from whichever matched sibling already declares that struct, is
what took `021dc694` on its first compile -- hand-rolled shift/mask on the same fields does not.

### A BITFIELD INSERT PUTS ITS RESULT IN THE VALUE'S REGISTER — accumulate with `|=` instead
`rec.f = Val()` emits `bic rW, rW, #mask` / `orr rV, rW, rV, lsr #k` / `str rV, [sp, #N]`: the OR
lands in the INSERTED VALUE's register, and because the store then needs it, the next statement's
setup is pushed past the store. The ROM does the opposite — it accumulates into the WORD's register
and computes the next call's argument before storing. Bind the value first, then accumulate:

    { unsigned int v = (unsigned int)Val() << 24; rec.u.raw &= ~0xff000; rec.u.raw |= v >> 12; }

    ROM / ours after:  bic r1, r1, #0xff000 ; orr r1, r1, r0, lsr #12 ; add r0, r4, #0x30 ; str r1
    ours before:       bic r1, r1, #0xff000 ; orr r0, r1, r0, lsr #12 ; str r0 ; add r0, r4, #0x30

The temp is REQUIRED: with the call inside the `|=` the word has to be reloaded after it
(`ldr` + 4 bytes). A single `W = (W & mask) | v` is a two-operand OR with no accumulator and keeps
the wrong destination. The shape is `(val << (32 - width)) >> (32 - width - offset)`, which is what
the bitfield assignment already emits. `0209a104` 33 -> 27; the corpus source that shows the
accumulator form is `GenerateDeviceRandomBytes_020120f0` (`packed = a<<24 | b<<16 | c<<8 | d`).

### TWO STORES TO ONE BITFIELD WORD — clear through a `volatile` union member
Consecutive writes to one bitfield storage unit collapse to ONE store: mwcc keeps the word in a
register and dead-store-eliminates every write but the last. This is general dead-store elimination,
not a bitfield quirk — two full-word writes to the same local collapse the same way. When the target
stores the word TWICE, overlay the bitfield struct with a volatile full-word member and write the
last one through it:

    union U { struct Bits b; unsigned int raw; volatile unsigned int vraw; };

    rec.u.b.d = Val();                            // run flush   -> str rX, [sp, #N]
    rec.u.vraw = rec.u.raw & 0x01ffffff;          // the clear   -> str rY, [sp, #N]

The volatile store cannot be eliminated, so the first store survives, and because it is a MEMBER
write (not a pointer deref) both stores are addressed off sp with no base register. The volatile
must be on the LATER store: a volatile EARLIER store followed by a plain one is still deleted, even
`rec.u.vraw = rec.u.raw;` (measured on `0209a218`, output identical to having no volatile at all). A cast
(`*(unsigned int*)((char*)&rec + 4) &= mask`) also keeps both stores but pays a materialised
`add rX, sp, #0` and addresses the second off it — that is the wrong shape.

`0209a104`, `0209a218`. Inert, measured: volatile on the CLEARED bitfield (adds a reload), volatile
on the FIRST write, `optimization_level 1` (restores the store but reschedules the whole function),
every dead-store pragma, a union self-assign, an aggregate round-trip (costs the temp a stack slot).
The volatile store is a scheduling barrier, so it lands immediately after the mask — if the target
computes something from the cleared word before storing it, that ordering is a separate residue.

A dead `strb` triple (raw, `and`, `mov r0,r0`) that survives in the ROM is a 1-byte struct returned
by a `static inline` wrapper and assigned to a FUNCTION-scope struct whose address is taken
(`0201a600`). The unused pointer local that takes the address is suspect; look for a real use first.

### A BITFIELD READ INSIDE AN `add` LOSES ITS LAST SHIFT — cast it to `short`
`add rd, rn, rm, shift` folds ONE shift, and a bitfield read ends in one. Written bare, the extract
is folded into the add and the OTHER operand takes the register: `add r0, r0, r1, lsr #31` where the
target has `lsr r1, r1, #0x1f` standing on its own line and folds `i * 2` instead. A `(short)` cast
on the read blocks the fold without emitting anything -- mwcc knows the field cannot overflow a
short -- so the extract materialises and the add folds the other operand:

    (short)fld->flag + i * 2 + 0x7530      // lsl#6 / lsr#31 / add r1, r1, r6, lsl #1   MATCH
    fld->flag + i * 2 + 0x7530             // lsl#6 / add r0, r0, r1, lsr #31           21 bytes out

`ov012:0218a9ec`. `(int)`, `|0`, a named local, `(x << 6) >> 31` and `opt_propagation off` all still
fold; `(unsigned char)` and `& 1` change the extract itself and cost more.
