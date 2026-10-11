#!/usr/bin/env python3
"""REGRESSION tests for the helper scripts, pinning behaviour that was fixed by hand.

    python regress.py

pipetest.py tests the GATE against the ROM. This tests the scripts AROUND it -- the ranking, the
prompt, the verdict parsing, the placement filter, the colour sweep -- because those were edited
repeatedly on 08-20 with nothing but selfcheck's static invariants as a guard, and selfcheck cannot
see behaviour. Every case below encodes a fault that actually happened and cost something, so a
failure here means a real regression, not a style drift.

Each test states the fault it detects. If you change a script and a test fails, the question is
which of the two is wrong -- do not delete the test to make the suite green.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time

SP = _kp.SP
KIT = _kp.KIT
sys.path.insert(0, KIT)

FAILED = []
PASSED = [0]


def check(name, fault):
    def deco(fn):
        try:
            bad = fn()
        except Exception as e:                       # a test that explodes is a failure
            bad = "raised %s: %s" % (type(e).__name__, e)
        if bad:
            FAILED.append((name, fault, bad))
            print("FAIL  %s: %s" % (name, bad))
            print("      the fault it detects: %s" % fault)
        else:
            PASSED[0] += 1
            print("ok    %s" % name)
        return fn
    return deco


def load(mod):
    spec = importlib.util.spec_from_file_location("_r_" + mod, f"{KIT}/{mod}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@check("decomment keeps the KEEP-NAME marker integrate reads",
       "a staged file stripped by pad/decomment.py lost `// KEEP-NAME`, so integrate no longer "
       "delinked it under its mangled symbol")
def _decomment_keeps_markers():
    src = ("// KEEP-NAME: the ROM symbol here is the mangled C++ name, not a func_ tag.\n"
           "// USA: 0x02000000\n// note\nint f() { return 0; } /* x */\n")
    out = load("pad/decomment").strip(src)
    if "// KEEP-NAME" not in out or "// USA:" not in out or "note" in out or "/*" in out:
        return "stripped to %r" % out
    return None


# ---------------------------------------------------------------- resumable.py

@check("distances() ignores hex tails and size deltas",
       "reading '9/0xb4 bytes diff' as 4 bytes told a worker a barely-started function was nearly "
       "done; reading 'SIZE+4' or '-4 vs slot' as a diff distance did the same")
def _distances():
    R = load("resumable")
    cases = [
        ("regalloc cascade, 142/340 bytes best", [142]),
        ("extremely close (9/0xb4 bytes diff, mov#0xc), stuck after 7 tries", [9]),
        # A bare hex size with no numerator is the case that actually exercises the lookbehind:
        # in the line above, the `N/0xM bytes` alternative consumes the span before the tail of
        # the hex literal can be misread, so that case passes even with the guard removed.
        ("residual 0xb4 bytes diff", []),
        ("best 0x2c bytes", []),
        ("1 byte at 0x68 (asr vs lsr)", [1]),
        ("accum not cached; 140b/8runs, mine -4 vs slot", [140]),
        ("SIZE 0xc8 vs 0xd0 (8 bytes short), missing r3/r5", []),
        ("SIZE+4/332, if-conversion, 220 diff bytes", [220]),
    ]
    for text, want in cases:
        got = R.distances(text)
        if got != want:
            return "distances(%r) = %r, want %r" % (text, got, want)
    return None


@check("a spent-lever verdict deprioritises but never removes an address",
       "excluding 'no C form' / 'colorsweep inert' addresses wrote off matchable work; 30 functions "
       "were skiplisted that way before, and the standing rule is that only genuine needs-asm defers")
def _no_exclusion():
    R = load("resumable")
    out, held = R.rows()
    addrs = {r[4] for r in out}
    for _why, _mod, a, _reason in held:
        if a not in addrs:
            return "address %s is held but absent from the queue" % a
    if held and not any(r[0] == 1 for r in out):
        return "held addresses are not carrying the deprioritisation flag"
    return None


@check("gated/ is a dispatch pool",
       "wgate writes a source to gated/ ONLY on a MATCH; 801 proven sources accumulated there and "
       "112 stayed uncommitted because no dispatcher read the directory")
def _gated_pool():
    R = load("resumable")
    if not any(p.startswith("gated/") for p in R.POOLS):
        return "POOLS does not include gated/"
    if R.rank_of("/x/gated/main/0209ed0c.cpp") >= R.rank_of("/x/hold_main/0209ed0c.cpp"):
        return "a proven gated/ source does not outrank an ordinary hold_ prior"
    return None


# ---------------------------------------------------------------- mkresume.py

@check("the resume prompt hands over prior verdicts without authorising a skip",
       "the block used to end with 'SKIP immediately ... a cheap early SKIP is worth more than a "
       "full-cap grind', and three of four workers quoted it back instead of trying anything")
def _ruled_out():
    M = load("mkresume")
    if KIT not in sys.path:
        sys.path.insert(0, KIT)
    import resumable
    real = resumable.skips
    resumable.skips = lambda: {"021e6448": (None, "SKIP REGPERM 142 | declaration order inert")}
    try:
        block = M.ruled_out(SP, "021e6448")
    finally:
        resumable.skips = real
    if not block.strip():
        return "no ruled-out block produced for an address with recorded verdicts"
    low = block.lower()
    for banned in ("skip immediately", "cheap early skip", "not progress -- it is the prior"):
        if banned in low:
            return "block still contains skip-authorising text: %r" % banned
    if "matchable" not in low:
        return "block no longer asserts the function is matchable"
    return None


# ---------------------------------------------------------------- fix_verdicts.py

@check("a PASS sign-off counts as a match, a SKIP quoting MATCH does not",
       "resume_sweep classified on MATCH|matched only, so a worker finishing with 'PASS <addr>' was "
       "filed as a miss -- and DONE is what stops an address being served again")
def _verdicts():
    F = load("fix_verdicts")
    d = tempfile.mkdtemp()
    cases = [("PASS 0203af48", "MATCH"), ("MATCH", "MATCH"),
             ("SKIP 021f7b98 no MATCH found, 19 bytes", "miss"),
             ("BYTEDIFF 8 bytes", "miss")]
    for i, (result, want) in enumerate(cases):
        p = f"{SP}/wlog/resume_zz{i}_0000000{i}.json"
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"result": result}, fh)
        try:
            got = F.verdict_of("zz%d" % i, "0000000%d" % i)
            if got != want:
                return "result %r classified %s, want %s" % (result, got, want)
        finally:
            os.remove(p)
    return None


# ---------------------------------------------------------------- colorsweep.py

@check("colorsweep ranks a mnemonic mismatch worse than a byte",
       "byte-count scoring rejected the only move that led to a match: on 0209ed0c the CORRECT "
       "'i < total' scored 16 against the wrong 'total > i' at 15, so the sweep converged on the "
       "wrong side of the valley and reported 'inert'")
def _score_ranking():
    C = load("colorsweep")
    src = open(f"{KIT}/colorsweep.py", encoding="utf-8").read()
    if "mnem * mnem_w" not in src or "mnem_w = slot + 1" not in src or "over_w = mnem_w * (slot // 4 + 2)" not in src:
        return "score() no longer weights size above mnemonics above bytes"
    if not re.search(r"\\s\*\\\*\\s\*0x|\*\\s\*0x", src):
        return "score() no longer anchors the diff-row parse (wdiff's legend line has '*' and '|')"
    slot = 10204
    mnem_w = slot + 1
    over_w = mnem_w * (slot // 4 + 2)
    worst_right = (slot // 4) * mnem_w + slot       # every instruction a wrong mnemonic, every byte wrong
    best_over = 0 * mnem_w + 0 + over_w             # oversize but otherwise identical
    if not worst_right < best_over:
        return "an oversize candidate can still outrank a right-sized one (0217e6b0: --apply wrote a 265-byte oversize over a 12-byte base)"
    if not worst_right + over_w < 10 ** 9:
        return "the worst real score reaches FAILSCORE"
    if not (0 * mnem_w + slot) < (1 * mnem_w + 0):
        return "one wrong mnemonic can still score better than a large byte diff"
    C._W["mnem"], C._W["over"] = mnem_w, over_w
    if C._fmt_score(2 * mnem_w + 37 + over_w) != "37 bytes / 2 wrong mnemonic(s) +oversize":
        return "_fmt_score no longer decodes the composite it is given: %r" % C._fmt_score(
            2 * mnem_w + 37 + over_w)
    return None


@check("r11 hoists a block-scoped declaration to function scope, at every position",
       "mwcc ranks callee-saved candidates by function-scope declaration order and an inner-block "
       "declaration ranks after all of them; r8 only lifts to the head of its OWN block, so the "
       "form that closed 0209ed0c was unreachable. Hoisting BELOW the other declaration is inert, "
       "so position must be enumerated")
def _r11():
    C = load("colorsweep")
    text = ('extern "C" ARM void f(char* p)\n{\n    int i;\n\n'
            '    if (p) {\n        unsigned char total = *(unsigned char*)(p + 0x8e07);\n'
            '        i = total;\n    }\n}\n')
    got = C.r11_decl_to_function_scope(text)
    if not got:
        return "no candidate produced (a cast initialiser must not be rejected -- r8's guard was)"
    if len(got) < 2:
        return "only %d position(s) generated; both above and below `int i;` are needed" % len(got)
    above = [c for _l, c in got if c.index("unsigned char total;") < c.index("int i;")]
    if not above:
        return "never places the hoisted declaration ABOVE the existing one, the case that matched"
    for _l, cand in got:
        if "total = *(unsigned char*)(p + 0x8e07);" not in cand:
            return "the assignment was moved; r11 must move only the declaration"
    return None


@check("r13 duplicates a pool literal for INDEXED and MEMBER uses, with element-pointer arithmetic",
       "an array or struct global read at two sites shares one pool word and lands exactly 4 bytes "
       "short, but r13 only matched `&data_x` -- ov023:021fa2f4 had `data_x[3].fn` and "
       "`&data_x[idx]` and the sweep could not generate a single candidate. `&data_x[i]` also has "
       "to cast to the ELEMENT pointer: `__typeof__(&data_x)` is T(*)[], so indexing it steps by "
       "the whole array")
def _r13_indexed():
    C = load("colorsweep")
    text = ('extern int data_ov023_021fea0c[];\nextern struct P data_020e6d5c;\n'
            'void f(int i) {\n    data_ov023_021fea0c[3] = data_020e6d5c.a;\n'
            '    int* d = &data_ov023_021fea0c[i];\n}\n')
    got = dict(C.r13_dup_pool_literal(text))
    idx = [c for l, c in got.items() if l.startswith("dupliteralidx")]
    mem = [c for l, c in got.items() if l.startswith("dupliteralmem")]
    amp = [c for l, c in got.items() if l.startswith("dupliteral:")]
    if not idx:
        return "no candidate for the `data_x[3]` use -- an indexed read owns a pool word too"
    if not mem:
        return "no candidate for the `data_x.field` use"
    if not amp:
        return "no candidate for the `&data_x[i]` use"
    if not any("&((__typeof__(&data_ov023_021fea0c[0]))0x021FEA0C)[i]" in c for c in amp):
        return "`&data_x[i]` was rewritten to a pointer-to-ARRAY cast; indexing that steps by the " \
               "whole array, so the candidate is wrong even when it compiles"
    return None


@check("neighbours() interleaves the rules instead of concatenating them",
       "a file with many early-rule sites spent the whole budget before a later rule was scored "
       "once: 150 compiles on ov023:021fa2f4 never reached r13, whose own candidate takes the "
       "score from 1031 to 32. Every rule must get a turn before any rule gets a second")
def _interleaved():
    C = load("colorsweep")
    text = ('extern int data_ov023_021fea0c[];\n'
            'void f(int a, int b, int c, int d) {\n'
            '    int w = a + b;\n    int x = b + c;\n    int y = c + d;\n    int z = d + a;\n'
            '    int* p = &data_ov023_021fea0c[w];\n    int q = x + y + z;\n}\n')
    labels = [l for l, _ in C.neighbours(text)]
    if not any(l.startswith("dupliteral") for l in labels):
        return "r13 produced nothing on a text that clearly holds its shape"
    first = next(i for i, l in enumerate(labels) if l.startswith("dupliteral"))
    rules = len(C.RULES)
    if first >= 2 * rules:
        return ("the first r13 candidate is at position %d of %d; with one candidate per rule per "
                "round it must appear within the first two rounds (%d)" % (first, len(labels),
                                                                           2 * rules))
    return None


@check("r23 turns an early-return guard into a single-exit result variable",
       "the ROM materialises a return constant BEFORE the compare (`mov r0,#0` then `pophs`) while "
       "an early `return 0;` lets mwcc predicate it (`movhs r0,#0`). main:020a1bb4 and its twin "
       "020a1ccc each carried that one instruction as part of a 33/62-byte residue")
def _r23():
    import colorsweep
    body = ("// USA: func_1\nARM int f(unsigned int id) {\n"
            "    if (id >= 0x23) {\n        return 0;\n    }\n"
            "    g();\n    return 1;\n}")
    got = dict(colorsweep.r23_single_exit_result(body))
    if not got:
        return "r23 no longer fires on the early-return guard shape"
    only = list(got.values())[0]
    for want in ("int _r23 = 0;", "if (!(id >= 0x23)) {", "_r23 = 1;", "return _r23;"):
        if want not in only:
            return "r23 output is missing %r" % want
    ptr = ("// USA: func_1\nARM char* f(int i) {\n"
           "    if (i) {\n        return 0;\n    }\n    g();\n    return buf;\n}")
    if colorsweep.r23_single_exit_result(ptr):
        return "r23 fires when the final return is not an integer literal, but it types _r23 as int"
    if colorsweep.r23_single_exit_result(only):
        return "r23 fires on its own output, so a sweep can nest the rewrite"
    if colorsweep.r23_single_exit_result not in colorsweep.RULES:
        return "r23 is defined but not in RULES, so no sweep will ever try it"
    return None


@check("r22 folds an adjacent single-use local and refuses every unsafe shape",
       "r22 is the only rule that changes the expression TREE rather than statement order, which is "
       "why it can reach the scratch-register family nothing else can -- but folding a local whose "
       "value is read twice, or across an intervening statement, silently changes what the code "
       "computes, and a wrong 'improvement' that still compiles is the worst outcome the sweep has")
def _r22():
    import colorsweep
    body = ("extern \"C\" ARM int f(int* p) {\n"
            "    int a = p[0] + 3;\n"
            "    int b = a * 2;\n"
            "    return b;\n}")
    got = dict(colorsweep.r22_inline_single_use(body))
    if "inline1:a" not in got or "int b = (p[0] + 3) * 2;" not in got["inline1:a"]:
        return "r22 no longer folds an adjacent single-use local"
    twice = ("extern \"C\" ARM int f(int* p) {\n"
             "    int a = p[0] + 3;\n"
             "    int b = a * a;\n"
             "    return b;\n}")
    if any(l == "inline1:a" for l, _ in colorsweep.r22_inline_single_use(twice)):
        return "r22 folds a local that is read twice, which duplicates the computation"
    apart = ("extern \"C\" ARM int f(int* p) {\n"
             "    int a = p[0] + 3;\n"
             "    p[1] = 0;\n"
             "    return a;\n}")
    if any(l == "inline1:a" for l, _ in colorsweep.r22_inline_single_use(apart)):
        return "r22 folds across an intervening statement, so a side effect can be reordered"
    if colorsweep.RULES[-1] is not colorsweep.r17_decl_permute:
        return "r17_decl_permute is no longer last, so the widest rule now starves the others"
    if colorsweep.r22_inline_single_use not in colorsweep.RULES:
        return "r22 is defined but not in RULES, so no sweep will ever try it"
    return None


@check("the scheduler serves every unmatched function, not only the ones inside SIZE_BOUNDS",
       "claim.stratify() filtered to SIZE_BOUNDS, which starts at 257, and it was applied to the "
       "MEDIUM band (65-256) as well -- so every medium function fell outside every bound and the "
       "band came back EMPTY. `PULL_BAND=med` reported `pool drained` with 331 medium functions "
       "waiting in main, and mixed round-robin never served a medium function at all: 765 of them, "
       "749 never once attempted, unreachable. A scheduler that drops work reports success")
def _stratify_total():
    C = load("claim")
    src = open(os.path.join(KIT, "claim.py"), encoding="utf-8").read()
    if "not any(lo <= r[1] <= hi for lo, hi in SIZE_BOUNDS)" not in src:
        return "stratify() no longer keeps items that fall outside every SIZE_BOUNDS range"
    # and the live pool must not be empty for a band the config still has work in
    if not C.unmatched("main"):
        return "claim.unmatched('main') is empty -- the band filter is dropping work again"
    return None


@check("colorsweep's compile-failure sentinel cannot collide with a real score",
       "score() returns mnemonic_mismatches*10000 + bytes + oversize, and the sentinel was 10**6 -- "
       "so any base with 100+ differing mnemonics scored ABOVE it and the sweep refused to start, "
       "printing `base does not compile cleanly against the slot` about a source that compiles. "
       "100 wrong mnemonics is ordinary on a 250-instruction function's first pass, so this "
       "silently disabled colorsweep on the whole large band; three verdicts in one night quote "
       "that false message. main:02035178 scored 1581602 and was called a compile failure")
def _failscore():
    C = load("colorsweep")
    worst = 2600 * 10000 + 999 + 1000          # every instruction wrong, oversize, on the biggest
    if C.FAILSCORE <= worst:                   # function in the ROM
        return "FAILSCORE %d is reachable by a real score (worst plausible %d)" % (C.FAILSCORE, worst)
    src = open(os.path.join(KIT, "colorsweep.py"), encoding="utf-8").read()
    if "10 ** 6" in src:
        return "a bare 10**6 sentinel is still in colorsweep.py"
    if src.count("FAILSCORE") < 5:
        return "FAILSCORE is not used at every failure return and guard"
    return None


@check("r24 puts the bigger arm first, and only when that is what it is asked to do",
       "six verdicts report `the ROM branches over the arm, mwcc predicates it`, and on "
       "ov031:0221474c writing the 3-instruction arm as the `if` instead of the `else` produced the "
       "missing `beq` -- 4 bytes SHORT became size-exact and colorsweep closed the rest. The rewrite "
       "is pure negation, so the danger is not correctness but firing on `else if` (which would drop "
       "a branch of the chain) or looping on its own output")
def _r24():
    C = load("colorsweep")
    src = ("void f(void){\n    if (n == 0) {\n        ok = 0;\n    } else {\n"
           "        p->a = n;\n        p->b = i;\n        ok = 1;\n    }\n}\n")
    got = C.r24_arm_invert(src)
    if not got:
        return "r24 does not fire on the 0221474c shape it was written for"
    cand = got[0][1]
    if "if (!(n == 0)) {" not in cand:
        return "r24 fired but did not negate the condition"
    if cand.index("p->a = n;") > cand.index("ok = 0;"):
        return "r24 fired but left the small arm first, which is the shape that predicates"
    if C.r24_arm_invert(cand):
        return "r24 fires on its own output, so a sweep can loop between the two orders"
    for bad, why in (
            ("void f(void){\n    if (a) {\n        x = 1;\n    }\n}\n", "an if with no else"),
            ("void f(void){\n    if (a) {\n        x = 1;\n        y = 2;\n    } else {\n"
             "        z = 3;\n    }\n}\n", "an else that is already the smaller arm"),
            ("void f(void){\n    if (a) {\n        x = 1;\n    } else if (b) {\n        z = 3;\n"
             "        w = 4;\n    }\n}\n", "an else-if chain")):
        if C.r24_arm_invert(bad):
            return "r24 fires on %s" % why
    if C.r24_arm_invert not in C.RULES:
        return "r24 is defined but not in RULES, so no sweep will ever try it"
    return None


@check("r16 never moves a statement across a brace or a control header",
       "on main:0201647c r16 lifted `node = node->next;` out of its while body, scored 599 against "
       "604 and offered an INFINITE LOOP as the sweep's best candidate -- a worker had to revert it "
       "by hand twice. A rewrite rule that changes meaning is worse than an inert one: it burns the "
       "budget and can hand a broken source to whoever trusts the score")
def _r16_blocks():
    C = load("colorsweep")
    loop = ("void f(void){\n    int a = 1;\n    int b = 2;\n    Node *node = head;\n"
            "    while (node) {\n        a = node->v;\n        b = a;\n"
            "        node = node->next;\n    }\n    int c = 3;\n}\n")
    for label, cand in C.r16_stmt_move(loop):
        body = cand.split("while (node) {", 1)
        if len(body) != 2:
            return "r16 %s destroyed the loop header" % label
        if "node = node->next;" not in body[1].split("}", 1)[0]:
            return "r16 %s moved the loop advance out of the while body" % label
    unbraced = ("void f(void){\n    int a = 1;\n    if (a)\n        a = 2;\n"
                "    int b = 3;\n    int c = 4;\n}\n")
    for label, cand in C.r16_stmt_move(unbraced):
        if cand.split("if (a)", 1)[1].lstrip().split("\n", 1)[0].strip() != "a = 2;":
            return "r16 %s changed the body of a brace-less if" % label
    flat = "void f(void){\n    int a = 1;\n    int b = 2;\n    int c = 3;\n    int d = 4;\n}\n"
    if not C.r16_stmt_move(flat):
        return "r16 no longer moves anything in straight-line code, so the fix made it inert"
    return None


@check("r18-r21 each fire on the shape they were written for, and only on it",
       "the four rules added from 02061c04 all rewrite ordinary-looking C, so a regex that is one "
       "character too greedy fires on comments or captures an unbalanced fragment and every "
       "candidate it proposes fails to compile -- which reads as `the rewrite made it worse` and "
       "silently spends the whole sweep budget. pad/ruleprobe.cpp carries one instance of each")
def _new_rules():
    C = load("colorsweep")
    probe = os.path.join(KIT, "pad", "ruleprobe.cpp")
    if not os.path.exists(probe):
        return "pad/ruleprobe.cpp is missing -- the rules have no shape to be tested against"
    text = open(probe, encoding="utf-8").read()
    want = {"r18_narrow_bind": "int _nb", "r19_ternary_split": "if (k == 3)",
            "r20_bool_materialise": "int _bm", "r21_cast_width": "Sink(k, 2);"}
    for name, needle in want.items():
        got = getattr(C, name)(text)
        if not got:
            return "%s produced no candidate for its own probe shape" % name
        if not any(needle in cand for _l, cand in got):
            return "%s fired but never emitted %r" % (name, needle)
        for _l, cand in got:
            if cand.count("(") != cand.count(")"):
                return "%s emitted a candidate with unbalanced parentheses" % name
    # and the rules must leave a source with none of their shapes alone
    clean = 'extern "C" ARM int g(int a) { return a; }\n'
    for name in want:
        if getattr(C, name)(clean):
            return "%s fires on a source containing none of its shapes" % name
    return None


@check("r43 casts a bitfield read that feeds an add, r44 splits a compound cast store",
       "`add rd, rn, rm, shift` folds ONE shift and a bitfield read ends in one, so a bare "
       "`fld->flag + i * 2` folds the extract and gives the other operand the register; a (short) "
       "cast blocks the fold and emits nothing (0218a9ec). Two writes to one word collapse to one "
       "store under general DSE, and only a volatile store fed by a non-volatile read keeps both "
       "of them off sp with no base register (0209a104)")
def _r43_r44():
    C = load("colorsweep")
    add = ('ARM void f(Box* fld, int i)\n{\n'
           '    Sink((short)(fld->flag + i * 2 + 0x7530));\n}\n')
    got = [c for _t, c in C.r43_short_cast_in_add(add)]
    if not any("(short)fld->flag" in c for c in got):
        return "r43 never cast the bitfield read that feeds the add"
    store = '    *(unsigned int*)((char*)&rec + 4) &= 0x01ffffff;\n'
    want = ('*(volatile unsigned int*)((char*)&rec + 4) = '
            '*(unsigned int*)((char*)&rec + 4) & 0x01ffffff;')
    got = [c for _t, c in C.r44_volatile_split_store(store)]
    if not any(want in c for c in got):
        return "r44 never split the compound cast store into a read and a volatile store"
    if any("volatile volatile" in c for _t, c in C.r44_volatile_split_store(want + "\n")):
        return "r44 re-qualifies a store that is already volatile"
    if C.r44_volatile_split_store(add) or C.r43_short_cast_in_add(store):
        return "a rule fired on a source containing none of its shape"
    return None


@check("r45 turns a masked OR into an accumulate so the OR lands in the word's register",
       "`W = (W & mask) | v` is a two-operand OR with no accumulator, so mwcc puts the result in "
       "the INSERTED value's register and the store then pushes the next statement's setup past "
       "it. Binding the value and accumulating with `&=` then `|=` reproduces the ROM's "
       "`orr rW, rW, rV, lsr #k` and its argument order (0209a104, 33 -> 27)")
def _r45():
    C = load("colorsweep")
    text = ('ARM void f(Rec* r, int x)\n{\n'
            '    r->raw = (r->raw & ~0xff000u) | ((unsigned int)x << 12);\n}\n')
    got = [c for _t, c in C.r45_accumulate_or(text)]
    if not got:
        return "r45 produced no candidate for a masked OR"
    if not any("r->raw &= ~0xff000u;" in c and "r->raw |= _acc" in c for c in got):
        return "r45 fired but never emitted the &= / |= accumulate pair"
    mirrored = text.replace("(r->raw & ~0xff000u) | ((unsigned int)x << 12)",
                            "((unsigned int)x << 12) | (r->raw & ~0xff000u)")
    if not C.r45_accumulate_or(mirrored):
        return "r45 only matches the mask-first spelling, not value-first"
    if C.r45_accumulate_or('ARM void f(Rec* r) { r->raw = r->other | 3; }\n'):
        return "r45 fires on an OR whose left operand is not the destination masked"
    return None


@check("r46 moves a loop counter's declaration among the pointer locals",
       "Callee-saved pointers came out as a register permutation that no ordering of their own "
       "declarations reached; where the loop COUNTER is declared decides which pointer's register "
       "it coalesces with. 02021578 needed its counter block moved between two pointers, 020227dc "
       "needed its for-scoped counter hoisted to a bare declaration, which emits nothing")
def _r46():
    C = load("colorsweep")
    block = ('extern "C" ARM void f(char* s) {\n'
             '    void* a;\n    char* b;\n    int i0;\n    int i1;\n'
             '    a = g();\n    b = h();\n'
             '    for (i0 = 0; i0 < 4; i0++) { k(a); }\n'
             '    for (i1 = 0; i1 < 4; i1++) { k(b); }\n}\n')
    got = [c for _t, c in C.r46_counter_position(block)]
    if not any(c.index("int i0;") < c.index("char* b;") for c in got):
        return "r46 never moved the counter block above a pointer declaration"
    scoped = ('extern "C" ARM void f(char* s) {\n'
              '    void* a = g();\n    char* b = h();\n'
              '    for (int i = 0; i < 4; i++) { k(a, b); }\n}\n')
    got = [c for _t, c in C.r46_counter_position(scoped)]
    if not any("int i;" in c and "for (i = 0;" in c for c in got):
        return "r46 never hoisted a for-scoped counter to a bare declaration"
    if C.r46_counter_position('extern "C" ARM int g(int a) {\n    int x;\n    x = a;\n'
                              '    return x;\n}\n'):
        return "r46 fires on a function whose int local is not a loop counter"
    return None


@check("r47 folds the second && operand of a conditional store into a ?:",
       "A flag stored under `if (A && B)` spilled to the wrong slot and no declaration position "
       "fixed it; `if (A) V = B ? K : V;` makes the store a compiler-temp spill, which is laid out "
       "below the declared scalars (021ebb90, 49 -> 35)")
def _r47():
    C = load("colorsweep")
    text = ('extern "C" ARM void f(struct S* e, struct T* obj) {\n'
            '    int w = 1;\n'
            '    if (e->mode == 1 && obj->f311 != 0) w = 0;\n'
            '    g(w);\n}\n')
    got = [c for _t, c in C.r47_ternary_store(text)]
    if not any("if (e->mode == 1) w = obj->f311 != 0 ? 0 : w;" in c for c in got):
        return "r47 never produced `if (A) V = B ? K : V;`"
    nested = text.replace("e->mode == 1 && obj->f311 != 0", "(e->a && e->b) || obj->f311")
    if C.r47_ternary_store(nested):
        return "r47 split an && that is not at the top level of the condition"
    if C.r47_ternary_store('extern "C" ARM void f(int x) {\n    int y = 1;\n'
                           '    if (x) y = 0;\n    g(y);\n}\n'):
        return "r47 fired on a condition with no &&"
    return None


@check("r48 builds a named element pointer through a static inline pointer-arithmetic helper",
       "The ROM recomputes &obj->arr[i] for a call while ours reused the named e; every direct "
       "CSE-breaker swapped i and e. An inline helper returning (T*)o + n hides the equality and "
       "keeps the registers (021ebb90, 35 -> 31); a helper returning &o->arr[n] is re-CSE'd")
def _r48():
    C = load("colorsweep")
    text = ('struct E;\nstruct C;\n'
            '// USA: func_f\n'
            'extern "C" ARM void f(struct C* obj, int i) {\n'
            '    struct E* e = &obj->entries[i];\n'
            '    g(e);\n'
            '    h(&obj->entries[i]);\n}\n')
    got = [c for _t, c in C.r48_inline_index(text)]
    if not got:
        return "r48 produced no candidate for `T* e = &obj->arr[i];`"
    c = got[0]
    if "static inline struct E* _AtR48_" not in c or "return (struct E*)o + n;" not in c:
        return "r48 did not emit the void* pointer-arithmetic helper"
    if "h(&obj->entries[i]);" not in c:
        return "r48 rewrote the call's own &obj->arr[i], which is the recompute it must keep"
    if c.index("static inline") > c.index("// USA: func_f"):
        return "r48 put the helper between the USA tag and the function"
    if C.r48_inline_index('extern "C" ARM void f(int* p) {\n    int* q = p + 1;\n    g(q);\n}\n'):
        return "r48 fired on a declaration that is not &base->arr[i]"
    return None


@check("r49 moves the assignment before a for loop into the body as its first statement",
       "LICM hoists a loop-invariant load written as the body's first statement to just after the "
       "for-init; written before the for, it lands before the init (021ebb90, with r50: 24 -> 14)")
def _r49():
    C = load("colorsweep")
    text = ('extern "C" ARM void f(struct N* node, int n) {\n'
            '    codes = node->codes;\n'
            '    for (j = 0; j < n; j++) {\n'
            '        g(codes[j]);\n'
            '    }\n}\n')
    got = [c for _t, c in C.r49_sink_into_loop(text)]
    want = '    for (j = 0; j < n; j++) {\n        codes = node->codes;\n        g(codes[j]);'
    if not any(want in c for c in got):
        return "r49 never moved the assignment into the loop body"
    if C.r49_sink_into_loop(text.replace("    codes = node->codes;\n", "    int k = 1;\n")):
        return "r49 moved a declaration into the loop body"
    return None


@check("r50 retypes locals that only ever hold 0 or 1 to unsigned char, each and all together",
       "Byte flags move the spill slots and the reload order around them; the row loop of 021ebb90 "
       "only matched with found and blocked BOTH unsigned char (with r49: 24 -> 14)")
def _r50():
    C = load("colorsweep")
    text = ('extern "C" ARM void f(int n) {\n'
            '    int found = 0;\n'
            '    int blocked = 0;\n'
            '    int count = 0;\n'
            '    if (n) found = 1;\n'
            '    if (n > 2) blocked = 1;\n'
            '    count++;\n'
            '    g(found, blocked, count);\n}\n')
    got = [c for _t, c in C.r50_narrow_flag(text)]
    if not any("unsigned char found = 0;" in c and "unsigned char blocked = 0;" in c for c in got):
        return "r50 never retyped both flags together"
    if any("unsigned char count" in c for c in got):
        return "r50 retyped a counter that is incremented"
    if C.r50_narrow_flag(text.replace("found = 1;", "found = n;").replace("blocked = 1;", "blocked = n;")):
        return "r50 retyped a local assigned a value other than 0 or 1"
    return None


@check("r51 retypes a spilled int local to short, together with the int callees assigned into it",
       "A short spill is reloaded with a full-word ldr that stays below the previous store, where an "
       "int's reload is hoisted above it (021ebb90, 14 -> MATCH). short alone adds a truncation after "
       "each call, so the callee's declaration returns short too")
def _r51():
    C = load("colorsweep")
    text = ('int Clamp(int id, float a);\n'
            'int Other(int id);\n'
            '// USA: func_f\n'
            'extern "C" ARM void f(struct V* d, int id) {\n'
            '    int sf = 0x1000;\n'
            '    int k = Other(id);\n'
            '    if (id) sf = Clamp(id, 1.0f);\n'
            '    d->x = Mul(d->x, sf);\n'
            '    d->y = Mul(d->y, sf);\n'
            '    g(k);\n}\n')
    got = C.r51_short_spill(text)
    if not got:
        return "r51 produced no candidate"
    c = got[0][1]
    if "short sf = 0x1000;" not in c or "short Clamp(int id, float a);" not in c:
        return "r51's first candidate is not the most-used local retyped together with its callee"
    if "short Other(" in c:
        return "r51 retyped a callee that is not assigned into the retyped local"
    if not any("short sf = 0x1000;" in c2 and "int Clamp(" in c2 for _t, c2 in got):
        return "r51 did not also offer the local alone"
    if C.r51_short_spill('extern "C" ARM void f(int* p) {\n    unsigned int u = 0;\n    g(u);\n}\n'):
        return "r51 fired on a local that is not a plain int"
    return None


@check("r52 inlines a once-assigned address local at every use and offers the compound form",
       "A named pointer to base + K changed the order mwcc hoists loop invariants (02065990, 40 -> 5)")
def _r52():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM void f(unsigned char* self, int n) {\n'
            '    unsigned char* flags;\n'
            '    flags = self + 0x195b;\n'
            '    if (n) *flags = *flags | 1;\n'
            '    g(flags);\n}\n')
    got = C.r52_inline_address_local(text)
    if not got:
        return "r52 produced no candidate"
    if "*(self + 0x195b) |= 1;" not in got[0][1] or "flags" in got[0][1]:
        return "r52's first candidate is not the compound form with the local gone"
    if not any("*(self + 0x195b) = *(self + 0x195b) | 1;" in c for _t, c in got):
        return "r52 did not also offer the plain inlined form"
    if C.r52_inline_address_local(text.replace("g(flags);", "flags += 2;\n    g(flags);")):
        return "r52 inlined a local that is written twice"
    return None


@check("r53 turns a copy-modify-store into an in-place update and folds the copy's only shift",
       "02065990's last 5 bytes: `int old = F >> 4; F += step * 16;` -- the in-place update alone is inert")
def _r53():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM void f(struct S* s, int step) {\n'
            '    int old = s->pos;\n'
            '    int cur = old + step * 16;\n'
            '    s->pos = cur;\n'
            '    g((cur >> 4) - (old >> 4));\n}\n')
    got = C.r53_update_in_place(text)
    if not got:
        return "r53 produced no candidate"
    c = got[0][1]
    if (not got[0][0].startswith("inplace+fold") or "int old = s->pos >> 4;" not in c
            or "s->pos += step * 16;" not in c or "- old)" not in c or "cur" in c):
        return "r53's first candidate is not the folded in-place update"
    if not any(t.startswith("inplace:") for t, _c in got):
        return "r53 did not also offer the unfolded in-place update"
    if C.r53_update_in_place(text.replace("int old = s->pos;", "int old = Get(s)->pos;")):
        return "r53 fired on a copy whose source calls a function"
    return None


@check("r54 gives an indexed struct pointer a two-definition byte offset",
       "The second definition of the offset flips which scratch register the value and the address "
       "take (0219e384, 0x644); plain `+=`, locals, casts and operand order are inert")
def _r54():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM void f(char* ov, struct E* e) {\n'
            '    struct Slot* slot = (struct Slot*)(ov + 0x3a9c) + e->idx;\n'
            '    g(slot);\n}\n')
    got = C.r54_two_def_offset(text)
    if not got:
        return "r54 produced no candidate"
    c = got[0][1]
    if ("int off = e->idx;" not in c or "off *= sizeof(struct Slot);" not in c
            or "struct Slot* slot = (struct Slot*)(ov + 0x3a9c + off);" not in c):
        return "r54 did not render the two-definition offset"
    if "int off2 = e->idx;" not in C.r54_two_def_offset(text.replace("g(slot);", "g(slot, off);"))[0][1]:
        return "r54 reused a name already in the function"
    if C.r54_two_def_offset(text.replace("(struct Slot*)(ov", "(struct Other*)(ov")):
        return "r54 fired when the cast type differs from the declared type"
    return None


@check("r56 drops the trailing argument from an extern \"C\" prototype and every call to it",
       "A stale pointer left in r1 at a call means the source passed one argument fewer (ov003:02178910)")
def _r56():
    C = load("colorsweep")
    text = ('extern "C" void g(int a, int b);\n'
            '// USA: func_f\n'
            'extern "C" ARM void f(int* p) {\n'
            '    g(p[0], h(p[1], 2));\n'
            '    g(1, 2);\n}\n')
    got = dict(C.r56_drop_last_arg(text))
    new = got.get("droparg:g")
    if not new:
        return "r56 produced no candidate"
    if 'extern "C" void g(int a);' not in new or "g(p[0]);" not in new or "g(1);" not in new:
        return "r56 did not drop the last parameter and argument everywhere"
    if C.r56_drop_last_arg('extern "C" void g(int a);\nvoid f() { g(1); }\n'):
        return "r56 fired on a one-parameter prototype"
    return None


@check("the body-scoped rules read a function whose opening brace is on its own line",
       "_r46_body only accepted `ARM f(...) {` on one line, so r51 never offered `short x` on the "
       "Allman-style ov023:021ddc98 and every body-scoped rule skipped 162 of 710 parked sources")
def _allman_body():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM int f(short* p)\n'
            '{\n'
            '    int x;\n'
            '    x = p[0];\n'
            '    return x + p[1];\n'
            '}\n')
    got = dict(C.r51_short_spill(text))
    if "    short x;\n" not in got.get("shortspill:x@3", ""):
        return "r51 offered nothing on a brace-below function: %s" % sorted(got)
    return None


@check("r63 splits `v = e OP k;` into `v = e; v OP= k;` and leaves unsafe forms alone",
       "A single-definition loop bound is forward-substituted; `last = shown; last -= 1;` closed "
       "SCHED 13 (ov023:021db634)")
def _r63():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM void f(unsigned char shown, int* a, int b)\n'
            '{\n'
            '    int last;\n'
            '    int n = b * 4 + 1;\n'
            '    last = shown - 1;\n'
            '    b = a[0] + b;\n'
            '    b = b >> 2;\n'
            '    b = a[1] + 1 < b;\n'
            '    b = g(b - 1);\n'
            '    a[last] = n;\n'
            '}\n')
    got = dict(C.r63_split_assign_op(text))
    want = {"splitop:last@5": "    last = shown;\n    last -= 1;\n",
            "splitop:n@4": "    int n = b * 4;\n    n += 1;\n"}
    for label, needle in want.items():
        if needle not in got.get(label, ""):
            return "r63 did not emit %r for %s: %s" % (needle, label, sorted(got))
    if set(got) != set(want):
        return "r63 split an unsafe form: %s" % sorted(set(got) - set(want))
    return None


@check("r62 aligns one word-aligned extern data object, preferring an array, never a function",
       "An alignment-qualified object switches mwcc's IR optimizer off for the function (ov015:0218cc24)")
def _r62():
    C = load("colorsweep")
    text = ('extern char data_021940db[];\n'
            'extern int data_0211e33c;\n'
            'extern "C" unsigned char data_02193d48[];\n'
            'extern "C" void g(int x);\n'
            '// USA: func_f\n'
            'extern "C" ARM int f(int i) {\n'
            '    g(i);\n'
            '    return data_02193d48[i] + data_0211e33c + data_021940db[i];\n}\n')
    got = C.r62_iro_align(text)
    if len(got) != 1:
        return "r62 produced %d candidates, want 1" % len(got)
    new = got[0][1]
    if 'extern "C" unsigned char data_02193d48[] __attribute__((aligned(4)));' not in new:
        return "r62 did not align the word-aligned extern array"
    if new.count("__attribute__") != 1:
        return "r62 aligned more than one object"
    if C.r62_iro_align('extern int data_0211e33c;\nextern "C" void g(int x);\n')[0][1] != \
            'extern int data_0211e33c __attribute__((aligned(4)));\nextern "C" void g(int x);\n':
        return "r62 did not fall back to a scalar"
    return None


@check("r61 writes a zero memset as __clear",
       "The game zero-fills with __clear(buf, n), not memset(buf, 0, n) (main:02021f88)")
def _r61():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM void f(char* p) {\n'
            '    char buf[16];\n'
            '    memset(buf, 0, sizeof(buf));\n'
            '    memset(p, 1, 4);\n'
            '}\n')
    got = C.r61_memset_to_clear(text)
    if not got:
        return "r61 produced no candidate"
    new = got[0][1]
    if "__clear(buf, sizeof(buf));" not in new or "memset(p, 1, 4);" not in new:
        return "r61 did not rewrite only the zero memset"
    if 'extern "C" void __clear(void* buf, int n);' not in new:
        return "r61 did not declare __clear"
    return None


@check("r60 marks a read-only extern table const",
       "A const byte table lets its ldrb hoist above a volatile store (ov005:0215b520)")
def _r60():
    C = load("colorsweep")
    text = ('extern "C" unsigned char data_tbl[];\n'
            'extern "C" int data_rw[];\n'
            '// USA: func_f\n'
            'extern "C" ARM int f(int i) {\n'
            '    data_rw[i] = 1;\n'
            '    return data_tbl[i];\n}\n')
    got = dict(C.r60_const_extern_table(text))
    if 'extern "C" const unsigned char data_tbl[];' not in got.get("constextern:data_tbl", ""):
        return "r60 did not const the read-only table"
    if "constextern:data_rw" in got:
        return "r60 consted a table that is written"
    ptrs = ('extern Cmd* data_q[9];\n'
            'extern "C" Cmd* data_w[2];\n'
            '// USA: func_f\n'
            'extern "C" ARM Cmd* f(int i) {\n'
            '    data_w[i] = 0;\n'
            '    return data_q[i];\n}\n')
    got = dict(C.r60_const_extern_table(ptrs))
    if "extern Cmd* const data_q[9];" not in got.get("constextern:data_q", ""):
        return "r60 did not const the read-only pointer table (main:020d22f4): %s" % sorted(got)
    if "constextern:data_w" in got:
        return "r60 consted a pointer table that is written"
    return None


@check("r59 stores a late value into an earlier local of the same type",
       "A fresh local swapped two callee-saved registers; reusing the loop's node fixed it (ov002:0216033c)")
def _r59():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM void f(struct L* l) {\n'
            '    struct Node* node = l->head;\n'
            '    use(node);\n'
            '    struct Node* root = GetRoot(l);\n'
            '    use(root);\n'
            '}\n')
    got = dict(C.r59_reuse_earlier_local(text))
    new = got.get("reuse:root->node@4")
    if not new:
        return "r59 produced no candidate: %s" % sorted(got)
    if "    node = GetRoot(l);\n    use(node);" not in new:
        return "r59 did not rewrite the declaration and its uses"
    return None


@check("r58 gives a later loop its own counter",
       "Two loops sharing one counter share its register; the ROM had two (ov004:02169b4c)")
def _r58():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM void f(int* a, int n) {\n'
            '    int i;\n'
            '    for (i = 0; i < n; i++) {\n'
            '        a[i] = 0;\n'
            '    }\n'
            '    for (i = 0; i < n; i++) {\n'
            '        a[i] += i;\n'
            '    }\n'
            '    g(i);\n'
            '}\n')
    got = C.r58_split_loop_counter(text)
    if not got:
        return "r58 produced no candidate"
    new = got[0][1]
    if "    int i2;\n" not in new or "for (i2 = 0; i2 < n; i2++) {\n        a[i2] += i2;" not in new:
        return "r58 did not rename the second loop's counter"
    if "for (i = 0; i < n; i++) {\n        a[i] = 0;" not in new or "g(i);" not in new:
        return "r58 touched code outside the second loop"
    return None


@check("r57 moves the call after an if block into its arm",
       "The ROM's blt skipped the clamp AND the call, so the call was inside the arm (ov002:0216acf0)")
def _r57():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM void f(int* list, int idx, int count) {\n'
            '    if (idx >= count) {\n'
            '        idx = count - 1;\n'
            '    }\n'
            '    g(list, idx);\n'
            '}\n')
    got = C.r57_call_into_preceding_if(text)
    if not got:
        return "r57 produced no candidate"
    if "        idx = count - 1;\n        g(list, idx);\n    }\n" not in got[0][1]:
        return "r57 did not move the call inside the arm"
    if C.r57_call_into_preceding_if(text.replace("    g(list, idx);", "    x = 1;")):
        return "r57 fired on a statement that is not a call"
    return None


@check("r10 marks a never-written scalar local const",
       "`const short id = ...` fixed a function-wide r7/r8 swap on ov024:021eb5d0")
def _r10_scalar():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM int f(short* p) {\n'
            '    short id = p[2];\n'
            '    int n = 0;\n'
            '    n += id;\n'
            '    return n;\n}\n')
    names = [n for n, _t in C.r10_const_local(text)]
    if not any(n.startswith("constscalar:id") for n in names):
        return "r10 did not offer const on a read-only scalar local"
    if any(n.startswith("constscalar:n") for n in names):
        return "r10 offered const on a local that is written later"
    return None


@check("r55 loads both values into their locals before transforming either",
       "Loading both first keeps the second load live across the first transform, which swaps their "
       "scratch registers (ov023:021e63bc); statement order, raw locals and getters are inert")
def _r55():
    C = load("colorsweep")
    text = ('// USA: func_f\n'
            'extern "C" ARM int f(short* pos) {\n'
            '    int x = (short)(pos[0] << 3) + 0xa;\n'
            '    int ytop = (short)(pos[1] << 3) + 0x10;\n'
            '    return x + ytop;\n}\n')
    got = C.r55_load_then_transform(text)
    if not got:
        return "r55 produced no candidate"
    want = ("    int x = pos[0];\n    int ytop = pos[1];\n    x = (short)(x << 3) + 0xa;\n"
            "    ytop = (short)(ytop << 3) + 0x10;\n")
    if want not in got[0][1]:
        return "r55 did not render load-then-transform"
    if C.r55_load_then_transform(text.replace("(pos[1] << 3)", "(pos[x] << 3)")):
        return "r55 fired when the second expression reads the first local"
    if C.r55_load_then_transform(text.replace("(short)(pos[0] << 3) + 0xa", "g(pos[0])")):
        return "r55 fired on an expression that calls a function"
    return None


@check("levercheck counts a LANDED evolve crack that core.md does not cite, and leverwatch fires on it once",
       "evolve agents never write levers.tsv, so leverwatch could only idle out during evolve work, a "
       "landed evolve crack never reached the promotion gate, and an in-memory announced set re-fired "
       "the same lever on every re-arm")
def _levercheck_boards():
    import shutil
    import subprocess
    import tempfile
    d = tempfile.mkdtemp()
    cfg = os.path.join(d, "cfg")
    os.makedirs(os.path.join(cfg, "overlays", "ov017"))
    open(os.path.join(cfg, "delinks.txt"), "w").write("    .text start:0x02011111 end:0x02011200\n")
    open(os.path.join(cfg, "overlays", "ov017", "delinks.txt"), "w").write(
        "    .text start:0x02133333 end:0x02133400\n")
    boards = os.path.join(d, "boards")
    os.makedirs(boards)
    open(os.path.join(boards, "02011111_board.md"), "w").write("EVO g1_x RULE: a lever\n")
    open(os.path.join(boards, "02133333_board.md"), "w").write("EVO SCORED g2 f.cpp 0 MATCH\n")
    open(os.path.join(boards, "02155555_board.md"), "w").write("EVO g1_y RULE: not landed yet\n")
    doc = os.path.join(d, "core.md")
    open(doc, "w").write("cites 02133333\n")
    env = {**os.environ, "LEVERCHECK_TSV": os.path.join(d, "none.tsv"), "LEVERCHECK_DOCS": doc,
           "LEVERCHECK_DECLINED": os.path.join(d, "none.txt"), "LEVERCHECK_BOARDS": boards,
           "LEVERCHECK_CFG": cfg, "LEVERWATCH_SEEN": os.path.join(d, "seen.txt")}
    r = subprocess.run([sys.executable, f"{KIT}/levercheck.py", "--keys"], capture_output=True, text=True, env=env)
    keys = [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]
    if keys != ["02011111"] or r.returncode != 1:
        return "levercheck --keys gave %s (exit %d); expected only the landed uncited 02011111" % (keys, r.returncode)
    bash = shutil.which("bash") or "bash"
    first = subprocess.run([bash, f"{KIT}/leverwatch.sh", "--once", "1"], capture_output=True, text=True,
                           env=env, timeout=60)
    if first.returncode != 0 or first.stdout.count("LEVER NEEDS PROMOTING") != 1 or "02011111" not in first.stdout:
        return "leverwatch --once did not fire exactly once on 02011111: %r" % first.stdout[:200]
    try:
        again = subprocess.run([bash, f"{KIT}/leverwatch.sh", "--once", "1"], capture_output=True, text=True,
                               env=env, timeout=5)
        return "leverwatch --once exited on an already-announced lever: %r" % again.stdout[:200]
    except subprocess.TimeoutExpired as e:
        if e.stdout and b"LEVER" in (e.stdout if isinstance(e.stdout, bytes) else e.stdout.encode()):
            return "leverwatch re-announced a lever it had already announced"
    return None


@check("r28 rewrites a field read to the literal its equality arm compared against",
       "020db3f4 sits 6 bytes and one wrong mnemonic out because `field + 15` re-loads the field "
       "instead of reusing the register the compare already materialised the literal in. The "
       "rewrite is only valid on `==` arms and only on READS")
def _r28():
    C = load("colorsweep")
    text = ('ARM void f(Obj* o, char* p)\n{\n    if (o->f6a == -16) {\n'
            '        memset(p, o->f6a + 15, 0x800);\n    }\n}\n')
    got = dict(C.r28_literal_in_equality_arm(text))
    if not got:
        return "no candidate produced for the 020db3f4 shape"
    cand = got.get("litarm@o->f6a@0", "")
    if "(-16) + 15" not in cand:
        return "the literal form is missing; the arm's constant must replace the field read"
    if "if (o->f6a == -16)" not in cand:
        return "the condition itself was rewritten, which folds the test away"
    for bad in ('if (o->f6a != -16) { g(o->f6a); }',
                'if (o->f6a == -16) { o->f6a = 3; }',
                'if (o->f6a == -16) { g(&o->f6a); }'):
        if C.r28_literal_in_equality_arm('ARM void f(Obj* o)\n{\n    %s\n}\n' % bad):
            return "fired where the rewrite is not value-preserving: %s" % bad
    return None


@check("the six 2026-09-05 rules each fire on their own lever shape and on nothing clean",
       "each was promoted into core.md as prose after a worker paid for it; a rule that does not "
       "fire is prose the sweep cannot use, and one that fires on unrelated code spends the budget "
       "a real candidate needed")
def _r29_r34():
    C = load("colorsweep")
    shapes = {
        "r29_field_reread": ('ARM void f(Obj* o)\n{\n    int c = o->cur;\n    g(c);\n}\n',
                             "g(o->cur);"),
        "r30_cse_repeat_expr": ('ARM void f(Slot* arr, int n)\n{\n    g(&arr[n]);\n    h(&arr[n]);\n}\n',
                                "_cse_arrn = &arr[n];"),
        "r31_hoist_loop_bound": ('ARM void f(int w)\n{\n    int i;\n    for (i = 0; i < w - 9; i++) {\n'
                                 '        g(i);\n    }\n}\n', "= w - 9;"),
        "r32_sink_store_into_arms": ('ARM void f(char* o, int c)\n{\n    int v;\n    if (c == 0) {\n'
                                     '        v = 0;\n    } else {\n        v = 1;\n    }\n'
                                     '    o[0x5d] = v;\n}\n', "o[0x5d] = 0;"),
        "r33_field_signedness": ('struct S {\n    int idx;\n};\n// USA: func_1\n'
                                 'ARM void f(struct S* s)\n{\n    g(s->idx);\n}\n',
                                 "unsigned int idx;"),
        "r34_call_move_earlier": ('ARM void f(Obj* o)\n{\n    int a = g(o);\n    if (a != 0) {\n'
                                  '        h(o);\n    }\n    int b = k(o);\n    use(a, b);\n}\n',
                                  None),
    }
    for name, (text, needle) in shapes.items():
        got = getattr(C, name)(text)
        if not got:
            return "%s produced no candidate for the shape its own lever has" % name
        if needle and not any(needle in cand for _l, cand in got):
            return "%s fired but never emitted %r" % (name, needle)
        for _l, cand in got:
            if cand.count("(") != cand.count(")") or cand.count("{") != cand.count("}"):
                return "%s emitted an unbalanced candidate" % name
    clean = 'extern "C" ARM int g(int a)\n{\n    return a;\n}\n'
    for name in shapes:
        if getattr(C, name)(clean):
            return "%s fires on a source containing none of its shapes" % name
    return None


@check("r12 emits the zero-accumulator form that forces a redundant copy",
       "a whole family (02081f20, 020df77c, 0215f060, 0216f634) comes out exactly 4 bytes SHORT "
       "because clean C lets mwcc skip a register copy the original kept. `x = 0; x = x + e;` is "
       "the construct that makes mwcc emit `add rD,rS,#0` -- proven by the committed "
       "SumKeyedLookups02086bf4 -- and `x = 0; x = e - x;` gives the sub form 020df77c reports")
def _r12():
    C = load("colorsweep")
    text = 'ARM void f(Obj* o)\n{\n    int t = o->timer;\n    o->x = t;\n}\n'
    got = dict(C.r12_zero_accumulator(text))
    if not got:
        return "no candidate produced (DECL is anchored ^...$ without re.M -- feed it one line)"
    plus = got.get("zeroacc+:t", "")
    minus = got.get("zeroacc-:t", "")
    if "int t = 0;" not in plus or "t = t + o->timer;" not in plus:
        return "the add form is not `T x = 0;` + `x = x + e;`"
    if "t = o->timer - t;" not in minus:
        return "the sub form is missing; 020df77c needs `sub rD,rS,#0`"
    # must not fire on pointers or floats, where adding zero is not the same rewrite
    for bad in ('    char* p = o->name;\n', '    float f = o->rate;\n'):
        t2 = 'ARM void f(Obj* o)\n{\n' + bad + '}\n'
        if C.r12_zero_accumulator(t2):
            return "fired on a non-integer declaration: %r" % bad.strip()
    return None


@check("r4 swaps same-type and bare declarations, not just differing initialised ones",
       "`_independent` counted every identifier left of `=` as WRITTEN, which for a declaration "
       "includes the type: `int a = 1;` and `int b = 2;` both wrote `int`, intersected, and were "
       "refused. A bare `int b;` has no `=` at all, so its written set came out empty and it was "
       "refused too. Recipe #9 -- the primary callee-saved lever -- therefore fired only on an "
       "adjacent pair with DIFFERENT type words that were BOTH initialised, which is almost no real "
       "declaration run, and 02218604's hoist had to be found by a paid session")
def _r4_same_type():
    C = load("colorsweep")

    def n(x, y):
        return len(C.r4_decl_reorder("ARM int f(S* s)\n{\n%s\n%s\n    return 0;\n}\n" % (x, y)))

    for name, x, y in (("same type, both initialised", "    int a = 1;", "    int b = 2;"),
                       ("same type, second bare", "    int a = 1;", "    int b;"),
                       ("both bare", "    int a;", "    int b;"),
                       ("bare below an initialised pointer",
                        "    TextBuffer* buf = &s->buf;", "    int len;")):
        if not n(x, y):
            return "no candidate for %s" % name
    for name, x, y in (("second reads the first", "    int a = 1;", "    int b = a;"),
                       ("first reads the second", "    int a = b;", "    int b = 2;"),
                       ("two call initialisers", "    int a = f();", "    int b = g();"),
                       ("an increment in the pair", "    int a = 1;", "    int b = c++;")):
        if n(x, y):
            return "swapped a dependent pair: %s" % name
    return None


@check("r41 folds the -1 form back to 0 as well as away from it",
       "the rule shipped one-way, generating only `x >= 0` -> `x > -1`. The sweep hill-climbs from "
       "OUR source toward the ROM, so the direction it needs most is removing an `mvn` our source "
       "carries and the ROM does not -- main:02081f20 sat at 23 bytes on exactly that and no rule "
       "could reach it")
def _r41_both_ways():
    C = load("colorsweep")
    text = ('ARM void f(Obj* o)\n{\n    if (o->timer <= -1) {\n        o->timer = o->b;\n'
            '    }\n    if (o->a > -1) {\n        g();\n    }\n    if (o->c < 0) {\n'
            '        h();\n    }\n}\n')
    got = [c for _t, c in C.r41_nonfoldable_constant(text)]
    if not any("o->timer < 0" in c for c in got):
        return "`x <= -1` was never folded to `x < 0`"
    if not any("o->a >= 0" in c for c in got):
        return "`x > -1` was never folded to `x >= 0`"
    if not any("o->c <= -1" in c for c in got):
        return "the original `x < 0` -> `x <= -1` direction was lost"
    return None


# ---------------------------------------------------------------- residue.py

@check("LOOP-SHAPE means the loop moved, not that the function changed length",
       "`branchy` compared the ROM's instruction at an offset against OURS at the same offset, so "
       "one extra instruction made every later branch 'differ' and 15 functions were filed under "
       "advice about hoisting a load out of a loop that was never restructured. It also excluded "
       "every mnemonic starting with `bl`, which silently dropped the blt/ble/bls/blo back-edges "
       "the class exists to catch")
def _loop_shape_class():
    R = load("residue")

    def drive(target_ins, our_ins):
        step = 4
        orig = bytes(range(1, 1 + step * len(target_ins)))
        mine = bytes(range(101, 101 + step * len(our_ins)))
        tmap = {i * step: t for i, t in enumerate(target_ins)}
        mmap = {i * step: m for i, m in enumerate(our_ins)}
        R.decode = lambda buf, isa: tmap if buf is orig else mmap
        return R.classify(orig, mine, "arm", set(), step * len(target_ins),
                          total=step * len(our_ins))[0]

    cases = [
        ("a branch facing a non-branch is a misalignment, not a target change",
         ["ldrsb r0, [r4, #8]", "cmp r0, #0", "bge #0x104", "mov r2, r0"],
         ["mvn r1, #0", "ldrsb r2, [r4, #8]", "cmp r2, r1", "bgt #0x104"], "SHAPE"),
        ("every target moved by the same amount is a length change",
         ["beq #0x10c", "b #0x104", "b #0x104", "beq #0x104"],
         ["beq #0x108", "b #0x100", "b #0x100", "beq #0x100"], "OPERAND"),
        ("blt is B with a condition, not BL",
         ["b #0x40", "cmp r0, #4", "blt #0x10", "mov r0, r1"],
         ["b #0x40", "cmp r0, #4", "blt #0x30", "mov r0, r1"], "LOOP-SHAPE"),
        ("bleq is BL and stays masked",
         ["bleq #0x40", "mov r0, r1"], ["bleq #0x80", "mov r0, r1"], "OPERAND"),
        ("two targets moving by different amounts is a real loop shape",
         ["b #0x40", "cmp r0, #4", "blt #0x10", "mov r0, r1"],
         ["b #0x20", "cmp r0, #4", "blt #0x30", "mov r0, r1"], "LOOP-SHAPE"),
        ("a pure register permutation is untouched",
         ["mov r0, r1", "add r2, r0, r3"], ["mov r5, r1", "add r2, r5, r3"], "REGPERM"),
    ]
    for name, t, m, want in cases:
        got = drive(t, m)
        if got != want:
            return "%s: want %s, got %s" % (name, want, got)
    return None


# ---------------------------------------------------------------- ov_recover.py

@check("placement refuses hand asm and still places plain C",
       "place() had a bare `continue` when sanitize found no ARM/THUMB name, so 7 byte-verified "
       "candidates deferred as wired-0 on every wave forever, silently. Six were hand asm (never to "
       "be landed) and one was real C that no naming rule covered")
def _placement():
    src = open(f"{KIT}/ov_recover.py", encoding="utf-8").read()
    m = re.search(r"ASMPAT = re\.compile\(r'(\(\?m\)\^\\s\*asm\\b[^']*)'", src)
    if not m:
        return "the asm-detection pattern is gone from place()"
    if "ASM_ALLOW" not in src or "asm_allow.txt" not in src:
        return "the asm allowlist is gone, so refusal is all-or-nothing again"
    pat = re.compile(m.group(1))
    asm_src = "// USA: func_1\nARM\nasm void MTX_Identity33_(register void* pDst)\n{\n}\n"
    c_src = "// USA: func_2\nchar* StringReplaceLanguageTag(const char* s, char* d, int l) {\n}\n"
    if not pat.search(asm_src):
        return "hand-asm source is not detected, so it would be landed against policy"
    if pat.search(c_src):
        return "plain C is misdetected as asm, so real work would be refused"
    for token in ("PLACE-SKIP-ASM", "PLACE-FALLBACK", "PLACE-ASM-ALLOW"):
        if token not in src:
            return "place() no longer reports %s" % token
    allow = open(f"{KIT}/asm_allow.txt", encoding="utf-8").read()
    addrs = [l.split('#', 1)[0].split()[0] for l in allow.splitlines() if l.split('#', 1)[0].strip()]
    if not all(re.fullmatch(r"0[0-9a-f]{7}", a) for a in addrs):
        return "asm_allow.txt holds something that is not a bare address, so the gate is not narrow"
    if "print(f\"  PLACE-SKIP" not in src and "PLACE-SKIP" not in src:
        return "place() can still drop a candidate silently"
    return None


# ---------------------------------------------------------------- autorepair.py

@check("autorepair drops extern \"C\" on a callee the ROM mangles, and reverts only the renames",
       "ov017:021ab280 was BYTE-EXACT and parked as UNDEF-SYM because one prototype said "
       "`extern \"C\" int Vec3LengthRounded(int*)` while the ROM symbol is _Z17Vec3LengthRoundedPi; "
       "the all-or-nothing revert then threw that fix away along with a bad callee rename")
def _linkage_repairs():
    import autorepair
    src = open(f"{KIT}/autorepair.py", encoding="utf-8").read()
    if "_no_rename" not in src:
        return "the two-stage retry is gone: one bad rename again discards every other repair"
    if 'dropped extern "C"' not in src:
        return "the mangled-callee linkage repair is gone"
    names = autorepair._all_symbol_names()
    if not any(n.startswith("_Z") for n in names):
        return "no mangled symbols loaded, so the repair can never fire"
    # Any mangled symbol whose BARE name the config does not also declare is a callee a source can
    # only reach through the mangled spelling. Naming one here rots: Vec3LengthRounded was the
    # example until the update-compiler base renamed that address to Vector3fix_Length.
    probe = re.compile(r"^_Z(\d+)(.+)$")
    for n in names:
        m = probe.match(n)
        if not m:
            continue
        bare = m.group(2)[:int(m.group(1))]
        if len(bare) == int(m.group(1)) and bare not in names:
            return None
    return "no mangled callee whose bare name the config leaves undeclared, so the repair cannot fire"


# ---------------------------------------------------------------- finish_wave.sh

@check("finish_wave refuses a malformed module instead of reporting green",
       "`finish_wave.sh ov031` crashed ov_recover, integrated nothing, and still printed "
       "'OK : +0 delinked, green, no-new-commits' -- a harvest that never ran looked like a "
       "harvest with no hits")
def _module_arg():
    src = open(f"{KIT}/finish_wave.sh", encoding="utf-8").read()
    if "FATAL: module must be" not in src:
        return "no module validation present"
    if not re.search(r"main\|\[0-9\]\[0-9\]\[0-9\]\)", src):
        return "the accepted-module pattern no longer matches `main` or a 3-digit overlay"
    return None


@check("the staging sweep keeps the file it just proved",
       "the sweep compared the staged copy against $f, the path the worker WROTE, so the guard "
       "never fired and every MATCH was moved out of staging into attempts/. Integration picks a "
       "module by counting staging/*/*.cpp, so no worker match could land: six sat in attempts/ "
       "while the log said MATCH")
def _staging_sweep_keeps_match():
    import subprocess
    src = open(f"{KIT}/pull_worker.sh", encoding="utf-8").read()
    start = src.find('  mkdir -p "$SP/attempts"\n  _fb=')
    end = src.find('non-final file(s) out of staging" >> "$LOG"', start)
    if start < 0 or end < 0:
        return "cannot locate the sweep block in pull_worker.sh"
    block = src[start:src.index("\n", end) + 1]
    with tempfile.TemporaryDirectory() as d:
        stage = os.path.join(d, "stage").replace("\\", "/")
        sp = os.path.join(d, "sp").replace("\\", "/")
        os.makedirs(stage)
        os.makedirs(sp)
        open(f"{sp}/STAMP", "w").close()
        # `find -newer` is strictly greater, and the stamp and the files land in the
        # same second on this filesystem, so age the stamp or the sweep sees nothing.
        os.utime(f"{sp}/STAMP", (time.time() - 60, time.time() - 60))
        proven = f"{stage}/0201aaaa.cpp"
        variant = f"{stage}/Named_0201aaaa.cpp"
        for p in (proven, variant):
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("// USA: func_0201aaaa\nARM void f(void) {}\n")
        script = ("set -e\n"
                  f'SP="{sp}"\nSTAGE="{stage}"\nSTAMP="$SP/STAMP"\n'
                  'ADDR=0201aaaa\nTAG=func_\nMOD=main\nv=MATCH\n'
                  f'f="{d}/wip/0201aaaa.cpp"\n_swept=0\nLOG=/dev/null\n' + block)
        # the worker's own copy lives outside staging, which is the whole point of the bug
        os.makedirs(os.path.join(d, "wip"))
        with open(os.path.join(d, "wip", "0201aaaa.cpp"), "w", encoding="utf-8") as fh:
            fh.write("// USA: func_0201aaaa\n")
        bash = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "bash")
        r = subprocess.run([bash, "-c", script], capture_output=True, text=True)
        if r.returncode != 0:
            return "sweep block failed to run: %s" % (r.stderr or "").strip()[:200]
        left = sorted(n for n in os.listdir(stage) if n.endswith(".cpp"))
        if "0201aaaa.cpp" not in left:
            return "the proven match was swept out of staging (left: %s)" % left
        if "Named_0201aaaa.cpp" in left:
            return "the non-final variant was kept in staging (left: %s)" % left
    return None


def _compile_elf(text, repo):
    import io
    import subprocess
    import buildcfg
    from elftools.elf.elffile import ELFFile
    with tempfile.TemporaryDirectory() as d:
        src, obj = os.path.join(d, "t.cpp"), os.path.join(d, "t.o")
        with open(src, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        r = subprocess.run([buildcfg.CC] + list(buildcfg.FLAGS) + ["-c", src, "-o", obj], cwd=repo,
                           capture_output=True, text=True)
        if r.returncode:
            return "COMPILE " + (r.stdout + r.stderr)[-200:]
        return ELFFile(io.BytesIO(open(obj, "rb").read()))


@check("the data planner reproduces the ov026:021d8ba0 hand landing and refuses what cannot link",
       "integrate.py wired only .text, so a source that defines its own data -- the function-scope "
       "static that matched 021d8ba0 -- got its .data placed wherever the linker put it and shifted "
       "the overlay; and a static in the function BODY is a local symbol that 021dc440's object "
       "cannot link to, which only showed up as an undefined symbol at link time")
def _dataown_replay():
    import subprocess
    import buildcfg
    import dataown
    repo = buildcfg.REPO
    cfg = "config/usa/arm9/overlays/ov026"
    srcpath = "src/Combat/Overlay_26/func_ov026_021d8ba0.cpp"
    before, after = "aea3f05d", "2424ebd7"

    def show(rev, path):
        r = subprocess.run(["git", "show", "%s:%s" % (rev, path)], cwd=repo, capture_output=True)
        return r.stdout.decode("utf-8").replace("\r\n", "\n")

    pristine = open(repo + "/extract/usa/arm9_overlays/ov026.bin", "rb").read()
    own = os.path.normcase(os.path.abspath(os.path.join(repo, cfg, "relocs.txt")))

    def run(text, rom):
        elf = _compile_elf(text, repo)
        if isinstance(elf, str):
            return elf
        relocs = dataown.load_relocs(repo)
        relocs[own] = (show(before, cfg + "/relocs.txt"), "\n")
        index = next(i for i, s in enumerate(elf.iter_sections()) if s.name == ".text")
        return dataown.plan(elf, index, 0x22c0, 0x021d8ba0, rom, 0x021d8a40, "026", cfg, repo, srcpath, {},
                            show(before, cfg + "/delinks.txt"), show(before, cfg + "/symbols.txt"), relocs)

    committed = show(after, srcpath)
    p = run(committed, pristine)
    if not isinstance(p, dict):
        return "the planner refused the landed source: %s" % p
    if p["symtxt"] != show(after, cfg + "/symbols.txt"):
        return "symbols.txt differs from the hand landing"
    if set(p["relocs"]) != {own} or p["relocs"][own][0] != show(after, cfg + "/relocs.txt"):
        return "relocs.txt differs from the hand landing"
    if p["delink_lines"] != "    .data start:0x021de840 end:0x021de87c\n":
        return "delink lines %r" % p["delink_lines"]

    head = "inline RowLabels& GetRowLabels() {"
    tail = "    return s;\n}\n"
    fn = 'extern "C" ARM void func_ov026_021d8ba0(BattleWork* self) {\n'
    if head not in committed or fn not in committed:
        return "cannot find the accessor or the function header to build the body-static variant"
    accessor = committed[committed.index(head):committed.index(tail, committed.index(head)) + len(tail)]
    decl = accessor[accessor.index("    static RowLabels s"):accessor.index("    return s;")]
    decl = decl.replace("static RowLabels s =", "static RowLabels sHolder =").replace("s.names", "sHolder.names")
    body = committed.replace(accessor, "").replace(fn, fn + decl).replace("GetRowLabels().", "sHolder.")
    p = run(body, pristine)
    if not (isinstance(p, str) and p.startswith("DATA-LOCALREF")):
        return "a body static that 021dc440 references was not refused: %s" % str(p)[:160]

    rom = bytearray(pristine)
    rom[0x021de858 - 0x021d8a40] ^= 1
    p = run(committed, bytes(rom))
    if not (isinstance(p, str) and p.startswith("DATA-BYTEDIFF")):
        return "a wrong initial byte was not refused: %s" % str(p)[:160]
    return None


@check("the data planner never writes a compiler-local name into another source",
       "landing main:020c6d7c renamed bss data_021112dc to its function-scope static isInitialized$13 and "
       "rewrote a dead `extern int data_021112dc;` in another source to `extern \"C\" int "
       "isInitialized$13;`, which mwcc rejects")
def _dataown_local_name():
    import subprocess
    import buildcfg
    import dataown
    repo = buildcfg.REPO
    cfg = "config/usa/arm9"
    landed = "c1328b61"
    srcpath = "src/Combat/Main/InitializeGamecardBusOwnership.cpp"

    def show(rev, path):
        r = subprocess.run(["git", "show", "%s:%s" % (rev, path)], cwd=repo, capture_output=True)
        return r.stdout.decode("utf-8").replace("\r\n", "\n")

    elf = _compile_elf(show(landed, srcpath), repo)
    if isinstance(elf, str):
        return elf
    pristine = open(os.path.join(repo, "extract/usa/arm9/arm9.bin"), "rb").read()
    index = next(i for i, s in enumerate(elf.iter_sections()) if s.name == ".text")
    with tempfile.TemporaryDirectory() as tree:
        os.makedirs(os.path.join(tree, "src"))
        with open(os.path.join(tree, "src", "Other.cpp"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("extern int data_021112dc;\nvoid Other() {}\n")
        subprocess.run(["git", "init", "-q"], cwd=tree, capture_output=True)
        subprocess.run(["git", "add", "src"], cwd=tree, capture_output=True)
        own = os.path.normcase(os.path.abspath(os.path.join(tree, cfg, "relocs.txt")))
        relocs = {own: (show(landed + "^", cfg + "/relocs.txt"), "\n")}
        p = dataown.plan(elf, index, 0xcc, 0x020c6d7c, pristine, 0x02000000, "main", cfg, tree, srcpath, {},
                         show(landed + "^", cfg + "/delinks.txt"), show(landed + "^", cfg + "/symbols.txt"),
                         relocs)
    if not isinstance(p, dict):
        return "the planner refused the landed source: %s" % p
    if "isInitialized$13 kind:bss addr:0x021112dc local" not in p["symtxt"]:
        return "symbols.txt does not carry the local static"
    written = [path for path, (text, _nl) in p["src_edits"].items() if "isInitialized$13" in text]
    if written:
        return "a compiler-local name was written into %s" % written
    return None


@check("cf_multi runs on the current colorforce JS and a move reaches the colouring order",
       "cf_multi patched JS anchors colorforce no longer has and died on its own assert, so the "
       "`moves`/`choices` probe core.md documents could not run")
def _cf_multi_moves():
    import shutil
    import subprocess
    if os.name != "nt":
        # colorforce spawns the Win32 compiler and hooks its own x86 code inside that process, at
        # addresses belonging to that exact binary. There is no such process on this host, so the
        # probe cannot run here: SKIP, never FAIL -- and say so, so a green line is not read as a
        # passing probe.
        print("skip  cf_multi needs the Windows colour-forcing probe (frida/colorforce.py)")
        return None
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "probe.cpp")
    shutil.copy(os.path.join(KIT, "regress_fixtures", "DispatchSumOrCopyHalfwords_0218ee38.cpp"), src)

    def trace(cfg):
        r = subprocess.run([sys.executable, f"{KIT}/pad/cf_multi.py", src, "ov015", "0218ee38", "0xb8", "0xb8",
                            json.dumps(cfg)], capture_output=True, text=True, cwd=KIT)
        if r.returncode:
            return None, (r.stdout + r.stderr).strip()[-300:]
        with open(os.path.join(tmp, "probe.cfm", "trace.json")) as fh:
            return json.load(fh)[-1]["nodes"], None

    try:
        base, err = trace({})
        if err:
            return "cf_multi failed: %s" % err
        order = [n[0] for n in base]
        if len(order) < 2:
            return "the last colouring call has %d node(s)" % len(order)
        moved, err = trace({"moves": [[order[-1], 0]]})
        if err:
            return "cf_multi failed with a move: %s" % err
        if moved[0][0] != order[-1]:
            return "node %d was not moved to the front: %s" % (order[-1], [n[0] for n in moved][:6])
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("a pool word's addend is read from r_addend, not from the zero mwcc writes in place",
       "mwcc emits RELA relocations with a zero in-place word; integrate.py, wgate.py and classify.py "
       "took the addend from that word, so a pool reference to symbol+N resolved to the bare symbol and "
       "a correct candidate was rejected as RELOC-WRONG (141 such words in the committed build; "
       "classify.py dropped ov015:0218bb3c from a wave that wgate had passed)")
def _rela_addend():
    import buildcfg
    import dataown
    elf = _compile_elf('extern char data_0201aaaa[];\nextern "C" char* F() { return data_0201aaaa + 5; }\n',
                       buildcfg.REPO)
    if isinstance(elf, str):
        return elf
    text = elf.get_section_by_name(".text").data()
    rel = elf.get_section_by_name(".rela.text")
    found = [dataown.addend(rr, text) for rr in rel.iter_relocations() if rr["r_info_type"] == 2]
    if found != [5]:
        return "addends read %s, want [5]" % found
    if "dataown.addend(rr, mine)" not in open(f"{KIT}/integrate.py", encoding="utf-8").read():
        return "integrate.py no longer reads the addend through dataown.addend"
    if "rr['r_addend'] if rr.is_RELA()" not in open(f"{KIT}/wgate.py", encoding="utf-8").read():
        return "wgate.py no longer reads r_addend for a RELA pool word"
    if "dataown.addend(rr, mine)" not in open(f"{KIT}/classify.py", encoding="utf-8").read():
        return "classify.py no longer reads the addend through dataown.addend"
    return None


@check("the NitroSDK stack-size symbols are known linker symbols with the ROM's pool values",
       "main:020c6d48, 020c745c and 020c8548 load SDK_IRQ_STACKSIZE and SDK_SYS_STACKSIZE from their "
       "literal pools; lcf_symbols() did not list them, so wgate called every candidate UNDEF-SYM and "
       "integrate.py skipped it")
def _sdk_lcf_symbols():
    import buildcfg
    elf = _compile_elf('extern "C" void SDK_IRQ_STACKSIZE();\nextern "C" void SDK_SYS_STACKSIZE();\n'
                       'extern "C" long F(int a) { return a ? (long)SDK_IRQ_STACKSIZE : (long)SDK_SYS_STACKSIZE; }\n',
                       buildcfg.REPO)
    if isinstance(elf, str):
        return elf
    symtab = elf.get_section_by_name(".symtab")
    referenced = {symtab.get_symbol(rr["r_info_sym"]) for sec in elf.iter_sections()
                  if hasattr(sec, "iter_relocations") for rr in sec.iter_relocations()}
    referenced = {s.name for s in referenced if s["st_shndx"] == "SHN_UNDEF" and s.name}
    known = buildcfg.lcf_symbols()
    if referenced - set(known):
        return "not linker symbols: %s" % sorted(referenced - set(known))
    pools = {"usa": (0x020c6d78, 0x020c7574), "eur": (0x020c6d88, 0x020c7584), "jpn": (0x020c8844, 0x020c9040)}
    rom = open(f"{buildcfg.REPO}/{buildcfg.pristine('main')}", "rb").read()
    for name, addr in zip(("SDK_IRQ_STACKSIZE", "SDK_SYS_STACKSIZE"), pools[buildcfg.REGION]):
        word = int.from_bytes(rom[addr - 0x02000000:addr - 0x02000000 + 4], "little")
        if known[name] != word:
            return "%s = %#x, the ROM's pool word at %08x is %#x" % (name, known[name], addr, word)
    return None


@check("ov_recover gathers a staged file tagged with its bound name instead of skipping it",
       "gather() located a file only by a `// USA: func_<addr>` tag, so a port tagged with its curated "
       "name (`// USA: _Z22OnDMAOrTimerCompletioni`) passed wgate and was silently left out of every "
       "wave: 4 main ports and ov033:022a296c")
def _retag_bound_name():
    import ast
    src = open(f"{KIT}/ov_recover.py", encoding="utf-8").read()
    fns = {n.name: ast.get_source_segment(src, n) for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)}
    if "retag" not in fns or "bound_addrs" not in fns:
        return "ov_recover.py has no retag()/bound_addrs()"
    if "txt = retag(txt, fp)" not in src:
        return "gather() no longer retags an untagged candidate"
    ns = {"re": re, "os": os, "TAGPRE": "func_ov015_",
          "_BOUND": {"_Z3Foov": "0218bb3c", "Bar": "0218bb40"}}
    exec(fns["bound_addrs"] + "\n" + fns["retag"], ns)
    out = ns["retag"]("// USA: _Z3Foov\nARM void Foo()\n{\n}\n", "x.cpp")
    if not out.startswith("// USA: func_ov015_0218bb3c // KEEP-NAME\nARM void Foo()"):
        return "a C++ definition was retagged as %r" % out.split("\n")[0]
    out = ns["retag"]('// USA: Bar\nextern "C" ARM void Bar()\n', "y.cpp")
    if not out.startswith("// USA: func_ov015_0218bb40\n"):
        return "an extern \"C\" definition was retagged as %r" % out.split("\n")[0]
    if ns["retag"]("// USA: Nope\nARM void Nope()\n", "z.cpp") != "// USA: Nope\nARM void Nope()\n":
        return "an unknown name was rewritten"
    return None


def _scaffold(module, addr):
    import subprocess
    r = subprocess.run([sys.executable, f"{KIT}/scaffold.py", module, addr], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=KIT)
    return r.stdout + r.stderr


@check("scaffold names a callee by its symbols.txt name, never by a scratch source's definition",
       "a tracked scratch file defining `Func020c976c` under `// USA: func_020c976c` made main:020d22f4's "
       "scaffold declare a callee that does not exist")
def _scaffold_symbols_name():
    out = _scaffold("main", "020d22f4")
    if 'extern "C" void func_020c976c();' not in out or "Func020c976c" in out:
        return "callee at 0x020c976c is not declared as func_020c976c: %s" % out[-300:]
    return None


@check("scaffold declares a mangled callee as C++, never extern \"C\"",
       "main:020c8bd4's scaffold declared `extern \"C\" void EnableSystemControlBit0();` for the ROM's "
       "_Z23EnableSystemControlBit0v, which links to a symbol that does not exist")
def _scaffold_mangled_callee():
    out = _scaffold("main", "020c8bd4")
    if "void EnableSystemControlBit0();" not in out:
        return "no C++ declaration of EnableSystemControlBit0: %s" % out[-300:]
    if re.search(r'(?m)^extern "C" void (?:_Z|EnableSystemControlBit0)', out):
        return "a mangled callee was declared extern \"C\""
    return None


@check("scaffold maps a bl that relocs.txt lacks",
       "relocs.txt has no entry for main:020c8bd4's bl to DisableSystemControlBit0 at +0x50, so the call "
       "map left it out")
def _scaffold_rom_call():
    out = _scaffold("main", "020c8bd4")
    if not re.search(r"(?m)^\s+\+0x50\s+DisableSystemControlBit0$", out):
        return "the call map has no +0x50 DisableSystemControlBit0: %s" % out[-300:]
    return None


@check("prready.py refuses a pull request built on a stale kit or decomp, and passes a current one",
       "pull requests built on stale checkouts reverted the CI workflow, re-added a file for an address "
       "another file owned, renamed a symbol in one region only, recorded dead ends for matched "
       "functions and cited matches that never landed")
def _prready():
    import subprocess
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        def git(repo, *args):
            r = subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t",
                                "-c", "core.autocrlf=false", *args], capture_output=True, text=True)
            if r.returncode:
                raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")

        def commit(repo, files):
            for path, text in files.items():
                os.makedirs(os.path.dirname(os.path.join(repo, path)), exist_ok=True)
                with open(os.path.join(repo, path), "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "x")

        pub_decomp, pub_kit, decomp, kit = (f"{d}/{n}" for n in ("pub_decomp", "pub_kit", "decomp", "kit"))
        for repo, branch in ((pub_decomp, "decomp-matching"), (pub_kit, "main")):
            os.makedirs(repo)
            git(repo, "init", "-q", "-b", branch)
        commit(pub_decomp, {
            ".github/workflows/match.yml": "on: push\n",
            "config/usa/arm9/delinks.txt": "    .text       start:0x02000000 end:0x02001000 kind:code align:32\n\n"
                                           "src/A.cpp:\n    complete\n    .text start:0x02000010 end:0x02000020\n",
            "config/usa/arm9/symbols.txt": "Foo kind:function(arm,size=0x10) addr:0x02000010\n",
            "config/eur/arm9/symbols.txt": "Foo kind:function(arm,size=0x10) addr:0x02000010\n",
            "src/A.cpp": "void Foo() {}\n"})
        commit(pub_kit, {n: open(f"{KIT}/{n}", encoding="utf-8").read()
                         for n in ("prready.py", "delinked.py", "kitpaths.py")}
               | {"worker_src/deadends.md": "# dead ends\n", "worker_src/core.md": "# core\n"})
        git(d, "clone", "-q", pub_decomp, decomp)
        git(d, "clone", "-q", pub_kit, kit)
        env = dict(os.environ, DQIX_KIT_URL=pub_kit, DQIX_KIT_BRANCH="main", DQIX_DECOMP_URL=pub_decomp,
                   DQIX_DECOMP_BRANCH="decomp-matching", DQIX_REPO=decomp, DQIX_STATE=f"{d}/state")

        def prready(*args, stdin=None):
            r = subprocess.run([sys.executable, f"{kit}/prready.py", *args], env=env, input=stdin,
                               capture_output=True, text=True)
            return r.returncode, r.stdout + r.stderr

        for mode in ("kit", "decomp"):
            code, out = prready(mode)
            if code != 0:
                return f"a current checkout was refused by prready.py {mode}: {out.strip()[-300:]}"
        commit(decomp, {".github/workflows/match.yml": "on: pull_request\n",
                        "src/Dead.cpp": "void Dead() {}\n",
                        "config/usa/arm9/symbols.txt": "Bar kind:function(arm,size=0x10) addr:0x02000010\n"})
        commit(kit, {"worker_src/deadends.md": "# dead ends\n02000010\tstale\n02000040\topen\n",
                     "worker_src/core.md": "# core\nrule (02000010)\nrule (02000400)\n"})
        commit(pub_decomp, {"src/A.cpp": "void Foo() { }\n"})
        commit(pub_kit, {"README.md": "x\n"})
        want = {"decomp": ("KIT BEHIND", "DECOMP BEHIND", "OUTSIDE A MATCH .github/workflows/match.yml",
                           "DEAD FILE src/Dead.cpp", "HALF RENAME Foo"),
                "kit": ("KIT BEHIND", "NO DEAD ENDS", "UNPROVEN CITATION 02000400")}
        for mode, needles in want.items():
            code, out = prready(mode)
            missing = [n for n in needles if n not in out]
            if code != 1 or missing:
                return f"prready.py {mode}: exit {code}, missing {missing}: {out.strip()[-400:]}"
            if mode == "kit" and "CITATION 02000010" in out:
                return f"prready.py kit flagged a landed citation: {out.strip()[-400:]}"
        code, out = prready("--hook", stdin=json.dumps({"tool_input": {"command": "gh pr create -R ZevyaDev/dqix-decomp"}}))
        if code != 2 or "HALF RENAME" not in out:
            return f"the hook let gh pr create through on a stale decomp: exit {code}"
        code, out = prready("--hook", stdin=json.dumps({"tool_input": {"command": "git status"}}))
        if code != 0:
            return f"the hook blocked a command that opens no pull request: exit {code} {out.strip()[-200:]}"
    return None


# ------------------------------------------------------- END-TO-END (slow, compiles)
# The tests above pin what each rewrite RENDERS. They cannot tell you whether the sweep still
# CRACKS a function: adding a rule enlarges the neighbourhood, so a winning path that used to fit
# inside the budget can fall outside it and the only symptom is a function quietly going back to
# unmatched. Every function cracked by hand gets an entry here.
FUNCTIONAL = [
    # (module, addr, prior file, expected, budget, what it proves)
    ("main", "0209ed0c", "regress_fixtures/GatherEnabledRecords_0209ed0c.cpp", "MATCH", 200,
     "cmpflip > declfnscope: comparison operand order then function-scope declaration order"),
    ("012", "0218a9ec", "regress_fixtures/BitfieldNoCast_0218a9ec.cpp", "MATCH", 60,
     "shortcast: a bitfield read feeding an add folds its extract into the add and steals the "
     "register; a (short) cast blocks the fold and emits nothing"),
    ("025", "021ebb90", "regress_fixtures/ShortSpill14_021ebb90.cpp", "MATCH", 60,
     "shortspill+ret: a short local's spill reload stays below the previous store, and its int "
     "callee declared returning short adds no truncation"),
    ("main", "02065990", "regress_fixtures/InPlaceField5_02065990.cpp", "MATCH", 60,
     "inplace+fold: the copy's shift folded into its initialiser closes the last one-shift fold; the "
     "prior is r52's output, since from the 40-byte one the first level alone exceeds the budget"),
    ("017", "0219e384", "regress_fixtures/TwoDefOffset_0219e384.cpp", "MATCH", 60,
     "twodefoff: an indexed struct pointer built from a two-definition byte offset gives the index "
     "the lower scratch register"),
    ("031", "02223b1c", "regress_fixtures/ReleaseHandle_02223b1c.cpp", "MATCH", 60,
     "dupliteral: an absolute-address constant emits the SECOND pool word mwcc folds away"),
    ("017", "021bd5d0", "regress_fixtures/SyncCombatantFlagAndDispatch_021bd5d0.cpp", "MATCH", 150,
     "declmove: one non-adjacent declaration move fixes the register ladder that decides stm fusion"),
    ("main", "02097280", "regress_fixtures/NullPointerDiffIndex_02097280.cpp", "MATCH", 150,
     "nullptrdiff: `(int)(p - (char *)0)` is the only unfolded `sub Rd, Rs, #0`, and the binding's "
     "position in the declaration run decides which register the copy lands in"),
    ("main", "0205f9cc", "regress_fixtures/AllocateAndInsertTimedNode_0205f9cc.cpp", "MATCH", 400,
     "mlaacc: binding a multiply-accumulate's MULTIPLICAND to the result local reverses the operand "
     "load order and stops the destination reusing the addend's register, then declmove colours it"),
    ("main", "020db3f4", "regress_fixtures/RefreshCacheBuffers_020db3f4.cpp", "MATCH", 60,
     "litarm: inside a `field == K` arm the arithmetic must be written on K, which reuses the "
     "register the compare materialised; naming the field emits a fresh ldr and colours it wrong"),
    ("main", "020307d0", "regress_fixtures/SearchAndEmitEntries_020307d0.cpp", "MATCH", 150,
     "reread: re-reading a field the source already holds in a local reproduces the ROM's redundant "
     "ldr -- without it the function is one instruction SHORT"),
    ("main", "02098a84", "regress_fixtures/CompactAndCopySlots_02098a84.cpp", "MATCH", 80,
     "cse: one named binding reused at both ends of a block keeps the offset live in a callee-saved "
     "register; two identical `&arr[i]` expressions are re-formed independently"),
    ("main", "020e2110", "regress_fixtures/DrawBorderTiles_020e2110.cpp", "MATCH", 200,
     "loopbound: a compound `for` bound bound to a local ahead of the loop is computed before the "
     "loop's own inits, which is the ROM's order"),
    ("main", "02012538", "regress_fixtures/AdvanceActorState_02012538.cpp", "MATCH", 60,
     "sinkstore: storing from each arm instead of through a named flag local lets mwcc predicate "
     "the sequence in one register"),
    ("main", "020c0a40", "regress_fixtures/HashUpdate_020c0a40.cpp", "MATCH", 100,
     "signflip: a field's signedness picks asr against lsr and blt against blo -- those diff rows "
     "are a type declaration, not a colouring"),
    ("015", "0218ee38", "regress_fixtures/DispatchSumOrCopyHalfwords_0218ee38.cpp", "MATCH", 60,
     "chainconst: two adjacent stores of the same constant materialise it AFTER the pointer save; "
     "chaining them reverses that, and the chain's direction fixes the store order too"),
    ("main", "02037d88", "regress_fixtures/HandleCombatantTick_02037d88.cpp", "MATCH", 250,
     "callmove: which call runs first decides who owns the callee-saved register for the rest of "
     "the block, and the move has to be allowed to hop a braced dispatch"),
    ("main", "0202b900", "regress_fixtures/PtrSplit_0202b900.cpp", "MATCH", 60,
     "ptrsplit: a loop-invariant address bound in one expression is hoisted ahead of the "
     "neighbouring loads; splitting the bind from the advance puts the `add` back"),
    ("main", "0209c840", "regress_fixtures/NoFold_0209c840.cpp", "MATCH", 60,
     "nofold: `>= 0` folds into a cmp immediate and `> -1` cannot, so the ROM's extra `mvn` comes "
     "back -- same test, one more instruction"),
    ("main", "02053634", "regress_fixtures/BindCall_02053634.cpp", "MATCH", 90,
     "bindcall: a call left inline in a compare always takes Rm, for BOTH operand orders; naming "
     "the result restores left-to-right placement"),
    ("main", "02076df4", "regress_fixtures/NoOpCase_02076df4.cpp", "MATCH", 60,
     "nocase: an explicit `case K: return;` gives mwcc a detached epilogue copy; deleting it lets "
     "the shared one-instruction epilogue inline into the jump-table slot"),
    ("main", "02002b90", "regress_fixtures/GuardedDoWhile_02002b90.cpp", "MATCH", 60,
     "dowhile: mwcc does not rotate loops -- a `while` emits a branch to a bottom test and never "
     "becomes the ROM's top-tested do-while, so the guarded form has to be written directly"),
    ("main", "020cdfd8", "regress_fixtures/VolatileAlias_020cdfd8.cpp", "MATCH", 60,
     "volalias: reading a pointer's fields through a `const volatile` alias forbids mwcc reordering "
     "two loads against each other. The alias is on a PARAMETER, which the first cut of the rule "
     "could not see -- so it could not reproduce the crack it was derived from"),
    ("023", "021db634", "regress_fixtures/SplitLoopBound_021db634.cpp", "MATCH", 60,
     "splitop: `last = shown; last -= 1;` gives a single-definition loop bound a second definition, "
     "so it is not forward-substituted and i2 = 0 schedules first"),
    ("023", "021ddc98", "regress_fixtures/ShortCanvasX_021ddc98.cpp", "MATCH", 60,
     "shortspill on a brace-below function: `short x` loads with ldrsh into the product temp; the "
     "prior already loads x before y, since neither step alone moves the score"),
    ("main", "020d22f4", "regress_fixtures/ConstQueueTable_020d22f4.cpp", "MATCH", 60,
     "constextern on a pointer table: `T* const tbl[9]` takes the indexed load out of the worst-case "
     "alias set, so it is no longer ordered before the store to the read index"),
]


def run_functional():
    import subprocess
    import shutil
    import tempfile
    bad = 0
    for module, addr, prior, expected, budget, what in FUNCTIONAL:
        src = os.path.join(KIT, prior)
        if not os.path.exists(src):
            print("FAIL  %s: prior %s is gone -- the base a crack depends on must not be deleted"
                  % (addr, prior))
            bad += 1
            continue
        work = os.path.join(tempfile.mkdtemp(), os.path.basename(prior))
        # The gate checks the EXPORTED symbol, so the file has to carry the config's name.
        text = open(src, encoding="utf-8").read()
        stem = os.path.basename(prior)[:-4].split("_")[0]
        # An overlay function is bound as func_ovNNN_<addr>; renaming it to func_<addr> like main
        # makes the gate report WRONG-SYMBOL and the crack looks lost when it is only misnamed.
        bound = "func_" + addr if module == "main" else "func_ov%s_%s" % (module, addr)
        if stem and not stem.startswith("func_"):
            text = text.replace(stem + "_" + addr, bound).replace(stem, bound)
        # C linkage on the DEFINITION line only, and never on one that already has it: a blanket
        # string replace put `extern "C" extern "C"` in front of 0209ed0c, every candidate failed to
        # compile, and the sweep reported no RESULT at all -- which reads as "the crack was lost".
        text = re.sub(r'(?m)^(?!extern "C" )((?:ARM|THUMB) [\w* ]*?%s\()' % re.escape(bound),
                      r'extern "C" \1', text)
        open(work, "w", encoding="utf-8", newline="\n").write(text)
        # Every prior here IS a committed address -- that is what makes the crack checkable -- and
        # wgate reports ALREADY-COMMITTED instead of MATCH on some of them, which colorsweep scores
        # as a compile failure and the case reads as "the crack was lost".
        r = subprocess.run([sys.executable, os.path.join(KIT, "colorsweep.py"), module, addr, work,
                            "--depth", "3", "--budget", str(budget)],
                           capture_output=True, text=True, cwd=_kp.REPO,
                           env={**os.environ, "WGATE_ALLOW_COMMITTED": "1"})
        out = (r.stdout or "") + (r.stderr or "")
        got = "MATCH" if "RESULT MATCH" in out else "no match"
        if got != expected:
            last = [l for l in out.strip().splitlines() if l.startswith("RESULT")]
            print("FAIL  %s functional: expected %s, got %s (%s)"
                  % (addr, expected, got, last[-1] if last else "no RESULT line"))
            print("      it proves: %s" % what)
            bad += 1
        else:
            print("ok    %s functional: colorsweep still reaches %s" % (addr, expected))
    return bad


def sweep_fingerprint():
    """Hash of everything that decides whether a crack still lands."""
    import hashlib
    h = hashlib.md5()
    for p in ("colorsweep.py", "wdiff.py", "wgate.py"):
        try:
            h.update(open(f"{KIT}/{p}", "rb").read())
        except OSError:
            h.update(b"missing")
    h.update(repr(FUNCTIONAL).encode())
    return h.hexdigest()



@check("recovery delegates Python to the current interpreter and can import its ELF dependency",
       "native Windows child lookup selected a different Python and crashed integrate.py before mutation")
def _recovery_python_interpreter():
    import ast
    import subprocess
    from types import SimpleNamespace
    tree = ast.parse(open(f"{KIT}/ov_recover.py", encoding="utf-8").read())
    nodes = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name == "sh"]
    if len(nodes) != 1: return "shared recovery launcher missing"
    ns = {"sys":sys,"subprocess":subprocess,"_TRACKED":None,"_INDEX_MUTATORS":frozenset()}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"recovery-launcher","exec"),ns)
    code = "import json,sys,elftools; from elftools.elf.elffile import ELFFile; print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'elftools':elftools.__file__}))"
    result = ns["sh"]("python","-c",code)
    if result.returncode: return "selected interpreter cannot import ELF dependency: " + result.stderr[-300:]
    proof = json.loads(result.stdout)
    normalize = lambda path: os.path.normcase(os.path.abspath(path))
    if normalize(proof["executable"]) != normalize(sys.executable):
        return "delegated Python differs from parent"
    if normalize(proof["prefix"]) != normalize(sys.prefix): return "delegated environment differs"
    if os.path.commonpath((normalize(sys.prefix),normalize(proof["elftools"]))) != normalize(sys.prefix):
        return "ELF dependency loaded outside selected environment"
    calls = []
    ns["subprocess"] = SimpleNamespace(run=lambda argv,**kwargs: calls.append((argv,kwargs)))
    for command in (("git","status","--porcelain"),("ninja","check"),(sys.executable,"-V")):
        ns["sh"](*command)
        if calls[-1] != (list(command),{"capture_output":True,"text":True}):
            return "non-bare-Python command changed: " + repr(command)

@check("delegated integrator startup failure stops before gate while preserving source snapshots",
       "try_set ignored a nonzero integrator exit and ran an expensive gate or quiet-green wave")
def _delegated_failure_stops_gate():
    import ast
    import subprocess
    import shutil
    src = open(f"{KIT}/ov_recover.py", encoding="utf-8").read()
    tree = ast.parse(src)
    wanted = {"sh","try_set"}
    nodes = [n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in wanted]
    if {n.name for n in nodes} != wanted: return "launcher/try_set missing"
    os.makedirs(f"{SP}/handwork",exist_ok=True)
    root = tempfile.mkdtemp(prefix="delegate_regress_",dir=f"{SP}/handwork")
    source,hold,stage = (os.path.join(root,name) for name in ("src","hold_main","staging"))
    for directory in (source,hold,stage,os.path.join(root,"wlog")): os.makedirs(directory)
    candidate = b"// USA: func_02000000\nvoid func_02000000() {}\n"
    for directory in (hold,stage):
        with open(os.path.join(directory,"candidate.cpp"),"wb") as out: out.write(candidate)
    events = []
    def place(addrs):
        events.append("place")
        shutil.copyfile(os.path.join(hold,"candidate.cpp"),os.path.join(source,"candidate.cpp"))
    def gate():
        events.append("gate")
        return True
    ns = {"sys":sys,"os":os,"glob":__import__("glob"),"re":re,"subprocess":subprocess,
          "_TRACKED":{"stale"},"_INDEX_MUTATORS":frozenset(),"clean":lambda:events.append("clean"),
          "dirty_tracked":lambda:set(),"place":place,"tracked":lambda path:True,"gate":gate,
          "committed_addrs":lambda:set(),"REPO":root,"SRCDIR":source,"SP":root,"HOLD":hold,
          "SUF":"main","TAGPRE":"func_","INTARGS":[],"INT":os.path.join(root,"delegate.py")}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"try-set-regression","exec"),ns)
    with open(ns["INT"],"w") as out: out.write("import dqix_intentionally_missing_startup_dependency\n")
    try:
        ns["try_set"](["02000000"])
    except RuntimeError as exc:
        if "delegated integrator exited" not in str(exc): return "failure lacks visible diagnostic"
    else:
        return "startup failure did not abort"
    if "gate" in events or ns["_TRACKED"] is not None: return "startup failure reached gate or retained stale cache"
    log = open(os.path.join(root,"wlog","integ_main.txt")).read()
    if "ModuleNotFoundError" not in log: return "startup traceback not preserved"
    for directory in (source,hold,stage):
        if open(os.path.join(directory,"candidate.cpp"),"rb").read() != candidate:
            return "startup failure lost source snapshot"
    events.clear()
    with open(ns["INT"],"w") as out: out.write("print('REPAIR per-candidate rejection; other candidates remain valid')\n")
    if ns["try_set"](["02000000"]) is not True or events.count("gate") != 1:
        return "ordinary rc0 partial/rejected candidates no longer reach gate"
    if "REPAIR per-candidate rejection" not in open(os.path.join(root,"wlog","integ_main.txt")).read():
        return "ordinary integrator output not preserved"
    for directory in (source,hold,stage):
        if open(os.path.join(directory,"candidate.cpp"),"rb").read() != candidate:
            return "rc0 changed source snapshots"

@check("recovery tracked snapshots are batch, fail closed and refresh after index writes",
       "per-file Git made Windows waves spend minutes scanning; stale/failed caches could delete committed source")
def _tracked_snapshot_behaviour():
    import ast
    from types import SimpleNamespace
    src = open(f"{KIT}/ov_recover.py", encoding="utf-8").read()
    tree = ast.parse(src)
    wanted = {"sh", "repo_relative_path", "read_tracked_paths", "tracked"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    if {n.name for n in nodes} != wanted:
        return "tracked snapshot functions missing"
    paths = {f"src/Combat/Main/f{i}.cpp" for i in range(4000)} | {"src/Combat/Main/has space.cpp"}
    reads = [0]
    fail = [False]
    truncated = [False]
    def run(args, **kwargs):
        if args[:2] == ["git", "ls-files"]:
            reads[0] += 1
            payload = ("\0".join(sorted(paths)) + "\0").encode() if paths else b""
            return SimpleNamespace(returncode=128 if fail[0] else 0, stdout=payload[:-1] if truncated[0] else payload, stderr=b"simulated Git error")
        if args[:2] == ["git", "add"]:
            paths.add("src/Combat/Main/new.cpp")
        if args[:2] == ["git", "reset"]:
            paths.discard("src/Combat/Main/new.cpp")
        return SimpleNamespace(returncode=128 if fail[0] else 0, stdout="", stderr="")
    os.makedirs(f"{SP}/handwork", exist_ok=True)
    root = tempfile.mkdtemp(prefix="tracked_regress_", dir=f"{SP}/handwork")
    mutators = [n for n in tree.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id == "_INDEX_MUTATORS" for t in n.targets)]
    if len(mutators) != 1: return "index mutation policy missing"
    nodes = mutators + nodes
    ns = {"os":os, "sys":sys, "REPO":root, "subprocess":SimpleNamespace(run=run), "_TRACKED":None}
    exec(compile(ast.Module(body=nodes,type_ignores=[]), "tracked-regression", "exec"), ns)
    for _ in range(2):
        for path in paths:
            if not ns["tracked"](path): return "tracked path became untracked"
    if reads[0] != 1: return "membership still starts per-file Git"
    if not ns["tracked"](os.path.join(root,"src","Combat","Main","has space.cpp")):
        return "absolute/space path mismatch"
    if not ns["tracked"]("src\\Combat\\Main\\has space.cpp"):
        return "Windows slash alias mismatch"
    if not ns["tracked"]("../outside.cpp"): return "out-of-repo path not protected"
    if ns["tracked"]("src/Combat/Main/new.cpp"): return "untracked candidate protected as tracked"
    ns["sh"]("git","add","-A")
    if not ns["tracked"]("src/Combat/Main/new.cpp") or reads[0] != 2:
        return "newly staged source is not protected after refresh"
    for command in ("checkout","reset","commit","rm","mv","read-tree","update-index"):
        ns["sh"]("git",command)
        if ns["_TRACKED"] is not None: return "index mutation did not invalidate: " + command
        if ns["tracked"]("src/Combat/Main/new.cpp") != ("src/Combat/Main/new.cpp" in paths):
            return "membership did not refresh after " + command
    fail[0] = True
    ns["sh"]("git","reset")       # unsuccessful write must invalidate too
    removals = []
    try:
        if not ns["tracked"]("src/Combat/Main/new.cpp"): removals.append("new.cpp")
    except RuntimeError:
        pass
    else:
        return "failed/partial snapshot did not stop destructive phase"
    if removals or ns["_TRACKED"] is not None: return "failed snapshot published/reused a set"
    fail[0] = False
    truncated[0] = True
    try:
        ns["tracked"]("src/Combat/Main/new.cpp")
    except RuntimeError:
        pass
    else:
        return "successful-but-truncated snapshot did not fail closed"
    if ns["_TRACKED"] is not None: return "truncated snapshot published a set"
    truncated[0] = False
    paths.clear()
    if ns["tracked"]("src/empty.cpp"): return "successful empty index was not accepted"
    if '_r = sh("python", INT, *INTARGS)\n    _TRACKED = None' not in src:
        return "delegated mutation boundary no longer invalidates"

@check("finish_wave bulk index snapshot preserves tracked paths and fails closed before quarantine",
       "failed, unreadable or truncated snapshots could quarantine tracked source")
def _finish_bulk_snapshot_behaviour():
    import subprocess
    import shutil
    src = open(f"{KIT}/finish_wave.sh", encoding="utf-8").read()
    first = src.find("# BULK-TRACKED-BEGIN")
    last = src.find("# BULK-TRACKED-END")
    if first < 0 or last < first: return "bulk snapshot block missing"
    stop = src.find("# 5. STAGING", last)
    if stop < last: return "duplicate loop boundary missing"
    duplicate_phase = src[first:stop]
    addr_begin = src.find("addr_of() {")
    addr_end = src.find("# 4. QUARANTINE", addr_begin)
    if addr_begin < 0 or addr_end < addr_begin: return "address reader missing"
    address_reader = src[addr_begin:addr_end]
    if 'git ls-files --error-unmatch "$f"' in src: return "per-file Git query remains"
    bash = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")
    if not bash: return "bash unavailable for behavioural snapshot test"
    for mode in ("valid","empty","git-error","missing","unreadable","truncated","load-error"):
        os.makedirs(f"{SP}/handwork", exist_ok=True)
        root = tempfile.mkdtemp(prefix="fw_snapshot_",dir=f"{SP}/handwork").replace("\\","/")
        source = os.path.join(root,"src","Combat","Main")
        q = os.path.join(root,"quarantine")
        os.makedirs(source)
        os.makedirs(q)
        for filename, addr in (("has space.cpp","02000000"),("duplicate.cpp","02000000"),("new.cpp","02000001")):
            with open(os.path.join(source,filename),"w") as out:
                out.write("// USA: func_" + addr + "\n")
        with open(os.path.join(q,".done_main"),"w") as out: out.write("02000000\n")
        body = 'cd "$1" || exit 99; SP="$1/state"; Q="$1/quarantine"; OV=main; TAGPRE=func_; SRCDIR="src/Combat/Main"; calls="$1/calls"\n'
        # The block calls the interpreter the script resolved at its top; hand it the same one here.
        body += f'PY="{sys.executable}"\n'
        # Record actual quarantine moves without changing their arguments or result.
        body += 'mv() { printf "%s\\n" "$1" >> "$SP/../moves"; command mv "$@"; }\n'
        body += 'git() { echo x >> "$calls"; '
        if mode != "empty":
            body += 'printf "src/Combat/Main/has space.cpp' + ('"; ' if mode == "truncated" else '\\0"; ')
        if mode == "missing": body += 'rm -f "$_tracked_tmp"; '
        if mode == "unreadable": body += 'rm -f "$_tracked_tmp"; mkdir "$_tracked_tmp"; '
        body += 'return ' + ('128' if mode == "git-error" else '0') + '; }\n'
        if mode == "load-error": body += 'mapfile() { return 1; }\n'
        body += address_reader + duplicate_phase + '\n'
        body += 'echo safe > "$1/phase-entered"\n'
        r = subprocess.run([bash,"--noprofile","--norc","-c",body,"snapshot-test",root],capture_output=True,text=True)
        entered = os.path.isfile(root + "/phase-entered")
        success = mode in ("valid","empty")
        if success and (r.returncode != 0 or not entered):
            return mode + " snapshot failed: " + r.stderr[-200:]
        if not success and (r.returncode != 6 or entered):
            return mode + " snapshot reached destructive phase"
        expected = {"duplicate.cpp"} if mode == "valid" else ({"duplicate.cpp","has space.cpp"} if mode == "empty" else set())
        actual = {name for name in os.listdir(q) if name.endswith(".cpp")}
        if actual != expected: return mode + " quarantined wrong sources: " + repr(actual)
        remaining = {name for name in os.listdir(source) if name.endswith(".cpp")}
        if remaining != {"duplicate.cpp","has space.cpp","new.cpp"} - expected:
            return mode + " did not preserve source inputs"
        moves_path = root + "/moves"
        moves = open(moves_path).read().splitlines() if os.path.isfile(moves_path) else []
        if len(moves) != len(expected): return mode + " unexpected move calls"
        if len(open(root + "/calls").read().splitlines()) != 1:
            return "bulk loader did not run exactly one Git query"


@check("a red build log names its culprit for every attributable failure class",
       "only layout drift was parsed, so a duplicate symbol, compile error, undefined reference or "
       "malformed config line sent the wave into sequential full-ROM bisection")
def _culprits_from_red_logs():
    c = load("culprits")
    # A red build log names its tools with the SEPARATOR THIS HOST PRINTS, so the fixture models
    # the log a run here actually produces rather than always a Windows one.
    sep = os.sep
    mwld = f".{sep}tools{sep}mwccarm{sep}2.0{sep}sp2p2{sep}mwldarm.exe: "
    cands = {"src/Combat/Overlay_28/func_ov028_021d9494.cpp": ("028", "021d9494"),
             "src/Combat/Overlay_0/ProcessCombatTurn_0215d63c.cpp": ("000", "0215d63c"),
             "src/Combat/Overlay_17/func_ov017_021d4e38.cpp": ("017", "021d4e38"),
             "src/Combat/Main/ReinitController02043204.cpp": ("main", "02043204"),
             "src/Combat/Main/Innocent_02000c9c.cpp": ("main", "02000c9c")}
    logs = {
        "021d9494": mwld + 'Multiply-defined: "func_ov028_021d9494"\n' + mwld + "in Committed_021d9494.o\n"
                    + mwld + "Previously defined in\n" + mwld + "func_ov028_021d9494.o\n",
        "0215d63c": f"src{sep}Combat{sep}Overlay_0{sep}ProcessCombatTurn_0215d63c.cpp:726: undefined label 'L_0da4'\n",
        "021d4e38": mwld + "Linker command file error at line 10100\n" + mwld + "File not found: func_ov017_021d4e38.o\n",
        "02043204": mwld + "Undefined :\n" + mwld + '"ReinitController02043204(MessageWork*,\n' + mwld + 'int)"\n'
                    + mwld + 'Referenced from\n' + mwld + '"Committed_02050000()" in\n' + mwld + "Committed_02050000.o\n",
    }
    for want, log in logs.items():
        got = [a for _m, a, _p, _w in c.name(log + mwld + 'warning: The name "Innocent_02000c9c" was reused\n', cands)]
        if got != [want]:
            return f"{want}: named {got}"
    drift = "Symbol 'func_ov017_021d4e38' is expected to be at 0x021d4e38 but is at 0x00000010\n"
    if [a for _m, a, _p, _w in c.name(drift, cands)] != ["021d4e38"]:
        return "an unplaced object was not named"
    shifted = "Symbol 'x' is expected to be at 0x021d4e38 but is at 0x021d4e40\n"
    if c.name(shifted, cands):
        return "a shifted symbol blamed its own function instead of falling back to the boundary scan"
    crash = "FAILED: [code=3221225794] build/usa/src/Combat/Main/Innocent_02000c9c.o\n"
    if not c.transient(crash) or c.transient(logs["0215d63c"] + "FAILED: [code=1] build/usa/arm9.o\n"):
        return "a crashed tool was not told apart from a real compile or link failure"


@check("a symbol rename leaves another region's address defines pointing at that region",
       "the upstream merge renamed func_020c6ff8 (a JPN address) to the USA symbol at 0x020c6ff8 and "
       "turned redundant JPN defines into redirects to undeclared names: the JPN build stopped compiling")
def _rename_respects_region_blocks():
    ns = {"rewrite": load("regionblocks").rewrite}
    renames = {"func_020c2208": "Mat4x4_ConvertTo4x3", "func_020c552c": "_Z24SubmitBlock0x80IfNotBusyi",
               "func_020c6ff8": "_Z18MarkGBABusReleasedv"}
    pat = re.compile(r"\b(%s)\b" % "|".join(map(re.escape, renames)))
    jpn = {"func_020c3cd4": "Mat4x4_ConvertTo4x3", "func_020c6ff8": "func_020c6ff8"}
    text = ("#if defined(jpn)\n#define func_020c2208 func_020c3cd4\n#define func_020c552c func_020c6ff8\n"
            "#endif\nvoid f() { func_020c2208(); func_020c6ff8(); }\n")
    out, _n = ns["rewrite"](text, pat, renames, lambda region, raw: jpn.get(raw))
    want = ("#if defined(jpn)\n#define _Z24SubmitBlock0x80IfNotBusyi func_020c6ff8\n"
            "#endif\nvoid f() { Mat4x4_ConvertTo4x3(); _Z18MarkGBABusReleasedv(); }\n")
    if out != want:
        return "rewrote a region block wrongly:\n" + out


@check("regionsync keeps the ported modules that build when another ported module is red",
       "one bad JPN port turned ov025 red; regionsync restored all of config/jpn, so every later "
       "integration ported nothing to JPN")
def _regionsync_restores_only_red_modules():
    import subprocess
    from types import SimpleNamespace
    try:
        rs = load("regionsync")
    except SystemExit:
        return "importing regionsync ran it"
    os.makedirs(f"{SP}/handwork", exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="regionsync_regress_", dir=f"{SP}/handwork",
                                     ignore_cleanup_errors=True) as root:
        def git(*args):
            return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True).stdout.strip()
        main, ov025 = f"{root}/config/jpn/arm9", f"{root}/config/jpn/arm9/overlays/ov025"
        for folder in (ov025, f"{root}/extract/jpn", f"{root}/tools"):
            os.makedirs(folder)
        for folder in (main, ov025):
            with open(f"{folder}/delinks.txt", "w") as f:
                f.write("    .text start:0x02000000 end:0x02000004\n")
        with open(f"{root}/tools/port.py", "w") as f:
            f.write('import sys\nif "--help" in sys.argv:\n    print("--sync")\n    sys.exit()\n'
                    'for folder, name in (("config/jpn/arm9", "a"), ("config/jpn/arm9/overlays/ov025", "b")):\n'
                    '    with open(folder + "/delinks.txt", "a") as f:\n'
                    '        f.write(f"\\nsrc/{name}.cpp:\\n    complete\\n    .text start:0x2 end:0x4\\n")\n')
        git("init", "-q")
        git("config", "user.name", "regress")
        git("config", "user.email", "regress@localhost")
        git("add", "-A")
        git("commit", "-q", "-m", "base")

        def green():
            red = "src/b.cpp" in open(f"{ov025}/delinks.txt").read()
            return not red, "[INFO ] Check ARM9 main: OK\n[INFO ] Check overlay 25: checksum failed\n" if red else ""
        real = rs.run
        rs.run = lambda *args: (0, "") if args[0] == "ninja" else real(*args)
        rs.green, rs.configure, rs.kitpaths = green, lambda region: (0, ""), SimpleNamespace(SP=root)
        cwd = os.getcwd()
        os.chdir(root)
        try:
            code, line = rs.sync("jpn", "tools/port.py")
        finally:
            os.chdir(cwd)
        if "src/a.cpp" not in git("show", "HEAD:config/jpn/arm9/delinks.txt"):
            return f"the module that built was not committed: {line}"
        if ("src/b.cpp" in git("show", "HEAD:config/jpn/arm9/overlays/ov025/delinks.txt")
                or git("status", "--porcelain", "--", "config")):
            return f"the red module was committed or left in the tree: {line}"
        if git("log", "-1", "--format=%s") != "Port 1 matched files to JPN" or code != 1:
            return f"reported as {code}: {line}"
    return None


STAMP = f"{SP}/wlog/functional_stamp.txt"


@check("gate and differ use external state and fail closed on snapshot errors",
       "kit-relative snapshots were invisible to state pools and failed copies still reported MATCH")
def _external_gate_state():
    gate = open(f"{KIT}/wgate.py", encoding="utf-8").read()
    diff = open(f"{KIT}/wdiff.py", encoding="utf-8").read()
    if 'SCR=f"{_kp.SP}/handwork/compile"' not in gate or 'SCR = f"{_kp.SP}/handwork/compile"' not in diff:
        return "compiler scratch is not in external state"
    if 'f"{_kp.SP}/gated/"' not in gate or 'PRESERVATION-FAILED' not in gate:
        return "gate does not preserve to external state with a visible failure"
    if 'os.replace(_tmp,' not in gate or 'ELFFile(io.BytesIO(_fh.read()))' not in diff:
        return "snapshot publication is not atomic or differ retains an open object handle"

@check("repool leaves a member already spelled as the renamed nested symbol",
       "021eefd0 declared GetGrottoStruct inside struct GameState; repool pasted "
       "_ZN9GameState15GetGrottoStructEv over the member and the call, and the source stopped compiling")
def _repool_member():
    R = load("pad/repool")
    spell = {"GetGrottoStruct": "_ZN9GameState15GetGrottoStructEv", "Foo": "_Z3Foov"}
    member = ("struct G;\nstruct GameState {\n    static GameState* GetInstance();\n"
              "    G* GetGrottoStruct();\n};\n\nvoid f() { GameState::GetInstance()->GetGrottoStruct(); }\n")
    new, _what = R.rewrite(member, True, spell, {}, set(), {}, {})
    if new != member:
        return "rewrote a member declaration and its call"
    free = "int Foo();\n\nvoid f() { Foo(); }\n"
    new, _what = R.rewrite(free, True, spell, {}, set(), {}, {})
    if not new or "_Z3Foov" not in new:
        return "no longer rewrites a renamed free function"


if __name__ == "__main__":
    slow = "--slow" in sys.argv
    nbad = run_functional() if slow else 0
    if slow and not nbad and not FAILED:
        # Record WHAT was proven, so selfcheck can refuse to go green when colorsweep, the differ
        # or the gate has changed since the last end-to-end run.
        with open(STAMP, "w", encoding="utf-8") as fh:
            fh.write(sweep_fingerprint())
    if not slow:
        print("(skipped %d end-to-end crack test(s); run with --slow after ANY colorsweep change)"
              % len(FUNCTIONAL))
    print("%d passed, %d failed" % (PASSED[0], len(FAILED) + nbad))
    sys.exit(1 if (FAILED or nbad) else 0)
