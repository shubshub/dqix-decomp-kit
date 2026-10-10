#!/usr/bin/env python
"""ZERO-TOKEN matcher: brute-force the two mechanical levers that recipe #9 proved, over an existing
near-match .cpp. No model involved — just source rewrites + the real gate.

WHY THIS CAN WORK. An LLM is needed to UNDERSTAND a function (control flow, types, which callee is
which). It is NOT needed to decide which local gets declared first — and that is what actually blocks
the largest family of near-misses. Both levers are codegen-neutral (only register numbers move), and the first is the one that pays,
because the ROM is built with opt_propagation ON:
  a) DEFINITION order  — split `T x = expr;` into `T x;` … `x = expr;`
  b) DECLARATION order — reorder the local declarations
Proven on func_ov001_0215c994: every mnemonic already matched and only two registers were swapped;
hoisting one declaration byte-matched it. That search is a few dozen compiles — seconds of CPU.

Usage: python permute.py <module> <addr> <src.cpp> [max_variants]
Prints MATCH + writes the winning file, or the best BYTEDIFF it reached.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import re, sys, os, subprocess, itertools, shutil

SP = _kp.SP
KIT = _kp.KIT
MOD, ADDR, SRC = sys.argv[1], sys.argv[2], sys.argv[3]
MAXV = int(sys.argv[4]) if len(sys.argv) > 4 else 400

base = open(SRC, encoding='utf-8', errors='ignore').read()

# --- locate the function body (last top-level `{` … matching `}`) ---
m = re.search(r'^[A-Za-z_].*\b' + re.escape(ADDR) + r'\s*\([^;]*\)\s*\{', base, re.M)
if not m:
    m = re.search(r'^(?:extern\s+"C"\s+)?(?:ARM|THUMB)\b[^;{]*\{', base, re.M)
if not m:
    sys.exit("could not locate the function body")
bstart = m.end()
depth, i = 1, bstart
while i < len(base) and depth:
    if base[i] == '{': depth += 1
    elif base[i] == '}': depth -= 1
    i += 1
bend = i - 1
head, body, tail = base[:bstart], base[bstart:bend], base[bend:]

# --- find simple local declarations at the TOP of the body (the only ones safe to permute) ---
# `T name = init;`  or  `T name;`  — one per line, no commas, no calls in the type.
DECL = re.compile(r'^(\s*)([A-Za-z_][\w:\*\s&<>]*?[\*\s&])([A-Za-z_]\w*)\s*(=\s*[^;]+)?;\s*$')
lines = body.split('\n')

# Scan the ENTIRE body, not just a leading block. The first version stopped at the first non-declaration
# statement, which in real code is usually line 2 — it found <=1 permutable decl in 5 of 8 near-misses
# and could search nothing. Declarations here are HOISTED to the top of the body and (optionally) their
# initialiser is left behind as a plain assignment. Hoisting a declaration is safe as long as the name
# is not used before its original line; splitting `T x = e;` into `T x;` + `x = e;` is always safe.
# Together these are exactly the two levers recipe #9 proved: DECLARATION order and DEFINITION order.
decls, depth = [], 0
for n, l in enumerate(lines):
    s = l.strip()
    depth += l.count('{') - l.count('}')
    if not s or s.startswith('//') or s.startswith('#'):
        continue
    if depth != 0:                       # only hoist from the function's top scope
        continue
    d = DECL.match(l)
    if not d:
        continue
    typ, name, init = d.group(2).rstrip() + ' ', d.group(3), d.group(4)
    if name in ('return',) or 'return' in typ or typ.strip() in ('else', 'case'):
        continue
    if re.match(r'^\s*(if|for|while|switch|do|return|else)\b', l):
        continue
    # Unsafe to hoist if the name is USED before its declaration. Strip comments first — this file's
    # own explanatory comment names `label` and `id`, which excluded both and lost the known-good
    # answer (the regression that caught this).
    _prior = '\n'.join(re.sub(r'//.*$', '', x) for x in lines[:n])
    if re.search(r'\b' + re.escape(name) + r'\b', _prior):
        continue
    decls.append((n, d.group(1), typ, name, init))

if len(decls) < 2:
    sys.exit(f"only {len(decls)} permutable declarations found — nothing to search")


def render(order, split_set):
    """Hoist `order` as declarations at the top of the body; names in split_set keep their initialiser
    behind as an assignment at the original site, so DEFINITION order differs from DECLARATION order."""
    drop = {d[0] for d in decls}
    hoist, rest = [], []
    for (n, ind, typ, name, init) in order:
        if init and name in split_set:
            hoist.append(f"    {typ}{name};")
        else:
            hoist.append(f"    {typ}{name}{(' ' + init.strip()) if init else ''};")
    keep = {d[0]: d for d in ((x[0], x) for x in decls)}
    for n, l in enumerate(lines):
        if n in drop:
            d = next(x for x in decls if x[0] == n)
            if d[4] and d[3] in split_set:
                rest.append(f"{d[1]}{d[3]} {d[4].strip()};")   # assignment stays where it was
            continue
        rest.append(l)
    return hoist + rest


def gate(text):
    p = f"{SP}/lab/perm_{os.getpid()}.cpp"
    open(p, 'w', encoding='utf-8').write(head + '\n'.join(text) + tail)
    r = subprocess.run([sys.executable, f"{KIT}/wgate.py", MOD, ADDR, p],
                       capture_output=True, text=True)
    o = (r.stdout + r.stderr).strip()
    if o.startswith("MATCH"):
        return 0, p
    mm = re.search(r'BYTEDIFF: (\d+) bytes', o)
    if mm:
        return int(mm.group(1)), p
    ms = re.search(r'total=0x([0-9a-f]+) slot=0x([0-9a-f]+)', o)
    if ms:
        return 1000 + abs(int(ms.group(1), 16) - int(ms.group(2), 16)), p
    return 9999, p


names = [d[3] for d in decls]
splittable = [d[3] for d in decls if d[4]]
best, bestfile, tried = gate(lines), None, 1
print(f"  baseline: {best[0]}   ({len(decls)} decls, {len(splittable)} splittable)")
best = best[0]

ident = tuple(decls)


def _splits():
    for k in range(1, len(splittable) + 1):
        for combo in itertools.combinations(splittable, k):
            yield ident, frozenset(combo)


def _orders():
    for perm in itertools.permutations(decls):
        if perm == ident:
            continue
        yield perm, frozenset()
        for name in splittable:
            yield perm, frozenset((name,))


def variants():
    """Definition order (where each initialiser lands) FIRST, declaration order interleaved.

    The old enumeration was `for perm: for combo:`, which spends the whole budget on the tail of
    the declaration list -- itertools.permutations varies its LAST element first, so 2001 compiles
    on 0201edf0 never moved the first declaration. It also spends that budget on the lever that is
    inert here: the ROM is built with opt_propagation ON, which sinks each definition to its use
    and stops declaration order from being the register ladder, while SPLITTING `T x = e;` into
    `T x;` + `x = e;` still moves the definition and therefore the colouring.
    """
    a, b = _splits(), _orders()
    live = True
    while live:
        live = False
        for it in (a, a, b):
            nxt = next(it, None)
            if nxt is not None:
                live = True
                yield nxt


for perm, split_set in itertools.islice(variants(), MAXV):
    tried += 1
    score, path = gate(render(perm, split_set))
    if score < best:
        best = score
        order_txt = ' '.join(n for (_, _, _, n, _) in perm)
        split_txt = ','.join(sorted(split_set)) or '-'
        print(f"  improved to {score:4d}  decl-order[{order_txt}] split[{split_txt}]")
        bestfile = shutil.copy(path, f"{SP}/lab/best_{ADDR}.cpp")
        if score == 0:
            print(f"MATCH after {tried} compiles -> {bestfile}")
            sys.exit(0)
print(f"  no match in {tried} compiles; best BYTEDIFF {best}" + (f" -> {bestfile}" if bestfile else ""))
