## THUMB: `adds rD, rS, #0` IS THE REGISTER COPY — do not chase it as an idiom
mwcc emits `adds rD, rS, #0` wherever it copies one low register to another in Thumb (`adds r6, r2,
#0`, `adds r4, r1, #0`, `adds r7, r0, #0` in one prologue).

**Consequence: never write a zero accumulator for it.** A plain `int n = f(x);` already compiles to
`bl f; adds r7, r0, #0`. Writing `int n = 0; n = n + f(x);` in a Thumb function adds a spurious
`movs r7, #0` and costs 4 bytes -- measured on func_ov031_0221db48, which sat at 11 bytes until the
accumulator was removed.

## THE COMPILER FOLDS AN ALIAS IT CAN PROVE EQUAL — change WHEN, not WHAT
Target keeps two pointers live (`adds r1, r4, #0` then reads `[r1, #2]` while `r4` advances). Writing
`cur = buf;` and reading `cur + 2` does NOT reproduce it: at the read `cur == buf`, so mwcc coalesces
them into one register. Neither `opt_common_subs off` nor `opt_propagation off` stops it.

**What works: advance the original BEFORE reading through the copy.**

```c
found:
    buf += 4;                                       /* was AFTER the read */
    unsigned short len = *(unsigned short*)(cur + 2);
```

Now the two pointers genuinely differ at the read and the copy is emitted. 53 -> 43 bytes, and the
`ldrh r0, [r1, #2]` lines up exactly. Same law as recipe 15: the lever is the ORDER of the uses, not
the shape of the declaration.

## STACK SLOT ORDER IS SCOPE, NOT DECLARATION ORDER
Three spilled locals landed in the right slots only when one of them was declared INSIDE the loop
that uses it. Declaration order does nothing here: `permsweep` tried all 720 orders x 6 pragma sets
(4320 variants) and never beat 11 bytes; moving `dst0` into the do-while took it to 4 in one step.
When the residue is `str rX, [sp, #A]` vs `[sp, #B]` with the same values in the same order, move a
declaration into the tightest scope that still covers its uses.

## A CAST CHAIN AND AN ARRAY COLOUR DIFFERENTLY
`*(int*)((char*)&sym + 0x30) |= flags` and `sym_as_array[12] |= flags` emit the same instructions with
r0/r1 SWAPPED. The array form matched. Seven rephrasings of the cast version (named base, named
value, `|=` vs `= a | b` vs `= b | a`, index form) were byte-identical to each other, as were all 24
mwccarm builds -- so when only a register pair is wrong on a global access, change the DECLARED TYPE
of the global, not the expression.

    extern int data_ov031_0224e6e0[];      /* not `extern int data_ov031_0224e6e0;` */
    data_ov031_0224e6e0[12] |= flags;      /* not *(int*)((char*)&sym + 0x30) |= flags */
