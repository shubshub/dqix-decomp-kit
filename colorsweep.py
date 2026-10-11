#!/usr/bin/env python3
"""Hill-climb a COLOURABLE near-miss to a match by mechanical, meaning-preserving rewrites.

Every rule here was derived from a function that actually matched:

  R1 operand swap      strcpy/strcmp word test: `t & nw` vs `nw & t` moves the AND destination.
  R2 compound flip     `t &= nw` and `nw &= t` compute the same value in DIFFERENT registers;
                       mwccarm puts the result in the register of the operand that is NOT the
                       assignment target. Flipping the pair is how strcpy and strcmp closed.
  R3 post-inc migrate  strcat: `q = p++; *q = c;` colours r3/ip the opposite way from
                       `q = p; *p++ = c;`.
  R4 decl reorder      recipe #9: callee-saved registers are handed out in reverse definition
                       order, so swapping two independent declarations swaps r4/r5.
  R5 stmt swap         two adjacent independent statements decide which value is live first.

Scoring is wdiff's byte count, so a rewrite that makes things worse is simply dropped. Nothing
here changes program meaning, so a MATCH is a real match and a miss costs only a compile.

Usage: colorsweep.py <OV|main> <addr> <file.cpp> [--depth N] [--budget N] [--apply]
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import hashlib
import itertools
import os
import re
import subprocess
import sys

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
WDIFF = os.path.join(KIT, "wdiff.py")

IDENT = r"[A-Za-z_]\w*"
# Real code rarely combines two bare identifiers: the operands are `p->field`, `a[i]`, or
# `this->x.y`. Restricting the swap rules to IDENT made the sweep generate 14 candidates for a
# 500-instruction function and find nothing. This operand class covers member/index chains but
# still excludes calls, so swapping can never move a side effect across the operator.
OPERAND = r"[A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*(?:\[[^\[\]()]*\])?"
COMMUTATIVE = ("&", "|", "^", "+", "*")

# The "did not compile" sentinel must not collide with a real score. score() returns
# `mnemonic_mismatches * 10000 + bytes + oversize`, so a sentinel of 10**6 is reachable by any base
# with 100+ differing mnemonics -- ordinary on a 250-instruction function -- and the sweep then
# refuses to start on a source that compiles fine. 10**9 needs 100000 differing instructions.
FAILSCORE = 10 ** 9

# Weights score() last used, so _fmt_score can decode the composite. One function per process.
_W = {"mnem": 10000, "over": 10 ** 8}


def score(text, module, addr, tag):
    """Compile `text` against the ROM slot; return (bytes_differing, headline)."""
    # Per-process name: repairsweep may run a sweep while another sweep is in flight, and two
    # processes sharing one temp path would gate each other's source.
    path = os.path.join(SP, "_cs_%d_%s.cpp" % (os.getpid(), tag))
    # NEVER leave a taggable candidate in the pool. ov_recover.gather() picks up any .cpp
    # under the scratchpad carrying a `// USA:` tag, so an intermediate variant -- most of
    # which are WRONG by construction -- would enter the wave as a real candidate. Defusing
    # the tag keeps these files invisible to the pipeline while the compiler still sees the
    # same code (wdiff takes the address from argv, not from the comment).
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text.replace("// USA: func_", "// SCRATCH-USA: func_"))
    # CS_SCORER=case scores with pad/casescore.py instead of wdiff: per-case aligned, with
    # relocation and pool rows excluded. wdiff aligns the WHOLE function, so in a big dense-dispatch
    # function one wrong-length case body makes every later instruction read as a diff and the score
    # stops tracking progress -- measured 5562 "differing bytes" against 204 real ones.
    if os.environ.get("CS_SCORER") == "case":
        sys.path.insert(0, os.path.join(KIT, "pad"))
        import casescore
        n, detail = casescore.score(path)
        return n, detail
    proc = subprocess.run([sys.executable, WDIFF, module, addr, path],
                          capture_output=True, text=True, cwd=REPO)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    if not out:
        return FAILSCORE, "(no output)"
    head = out[0]
    # WEIGHTS MUST SCALE WITH THE FUNCTION. They were flat -- mnemonics x10000, oversize +1000 --
    # while `bytes` grows with the slot, so both orderings inverted on big functions: an OVERSIZE
    # candidate 400 bytes out (1400) beat a right-size one 1500 bytes out (1500), and one wrong
    # mnemonic with a 50-byte diff (10050) beat a perfect-mnemonic 10204-byte diff. Derive both from
    # the slot so the intended ranking -- right size first, then mnemonics, then bytes -- always
    # holds. FAILSCORE stays far above the worst real score (max ~52M on the largest function).
    _slot = re.search(r"slot=0x([0-9a-fA-F]+)", chr(10).join(out))
    slot = int(_slot.group(1), 16) if _slot else 4096
    mnem_w = slot + 1
    over_w = mnem_w * (slot // 4 + 2)
    if _slot:
        _W["over"], _W["mnem"] = over_w, mnem_w
    # SCAN EVERY LINE, not just the first. wdiff prints SIZE/OVERGEN and THEN the byte diff, so
    # reading line 0 alone scored every wrong-length candidate as "does not compile" and the sweep
    # refused to start -- which is precisely the case these rewrites fix (a 4-byte-too-long
    # function is usually one redundant register copy). Wrong length still loses to right length,
    # so an exact-size variant always wins, but progress inside the wrong-size space stays visible.
    penalty = over_w if any(l.startswith("SIZE/OVERGEN") for l in out) else 0
    # BYTE COUNT ALONE IS THE WRONG OBJECTIVE, and it cost a match. A diff where every differing
    # instruction has the SAME mnemonic and only the registers differ is one colouring away from
    # exact; a diff with a wrong mnemonic is a wrong C construct and no colouring will close it.
    # Fixing a mnemonic often COSTS a byte (on 0209ed0c the correct `i < total` scored 16 against
    # the wrong `total > i` at 15), so a byte-count hill-climb rejects the only move that leads to
    # the match and converges on the wrong side of it. Rank by mnemonic mismatches first.
    mnem = 0
    for line in out:
        if "|" not in line:
            continue
        lhs, rhs = line.split("|", 1)
        # Anchor on an actual diff row (`*0xADDR  mnemonic ...`). wdiff's trailing legend line
        # contains both a '*' and a '|', so a looser test scored every candidate one mismatch
        # worse and left the ranking exactly as broken as plain byte counting.
        if not re.match(r"\s*\*\s*0x[0-9a-fA-F]+\s", lhs):
            continue
        lt, rt = lhs.split(), rhs.split()
        if len(lt) > 1 and rt and lt[1] != rt[0]:
            mnem += 1
    for line in out:
        if line.startswith("MATCH"):
            return 0, head
        m = re.match(r"BYTEDIFF (\d+)", line)
        if m:
            return mnem * mnem_w + int(m.group(1)) + penalty, head
    return FAILSCORE, head        # compile failure, no-slot, no emitted section


def _fmt_score(n):
    """Decode the composite with the weights score() last used. Printing it raw as "%d bytes" reads
    as a catastrophic regression -- a 33-byte diff with 7 wrong mnemonics looks like 70033 bytes."""
    over, rest = divmod(n, _W["over"])
    mnem, nbytes = divmod(rest, _W["mnem"])
    return "%d bytes%s%s" % (nbytes,
                             (" / %d wrong mnemonic(s)" % mnem) if mnem else "",
                             " +oversize" if over else "")


def _body_lines(text):
    """Line indices that are inside the function body (skip includes and comments)."""
    return [i for i, ln in enumerate(text.split("\n"))
            if ln.strip() and not ln.lstrip().startswith(("#", "//"))]


def _is_pointer_decl(text, star):
    """Is the `*` at index `star` a POINTER DECLARATION rather than a multiplication?

    `Type* name` looks like `identifier * identifier` to the operand regex, so without this every
    pointer parameter becomes an uncompilable `name * Type` candidate that still costs a compile.
    Recognised by what precedes the star on its line: type words, identifiers, commas, parens and
    whitespace only, no assignment or operator.
    """
    bol = text.rfind(chr(10), 0, star) + 1
    before = text[bol:star]
    if "=" in before or "return" in before:
        return False
    if not re.match(r'^[\s\w,()*&:]*$', before):
        return False
    eol = text.find(chr(10), star)
    after = text[star:eol if eol != -1 else len(text)]
    if re.match(r"^\s*\*+\s*\)", after):
        return True                      # a cast, `(Type*)x` -- never a multiplication
    if not re.match(r"^\s*\*+\s*\w+\s*[,;)=]", after):
        return False
    # The token left of the star must look like a TYPE, or `g(h * i)` -- a real multiply in an
    # argument list -- is indistinguishable from `g(List* list)` and gets thrown away too.
    lhs = re.search(r"(\w+)\s*$", before)
    if not lhs:
        return False
    word = lhs.group(1)
    return (word in _TYPEWORDS or word[0].isupper() or any(c.isdigit() for c in word))


def r1_operand_swap(text):
    """`a OP b` -> `b OP a` for commutative OP over side-effect-free operands, one site at a time."""
    out = []
    pat = re.compile(r"(?<![\w.>])(%s)\s*([&|^+*])\s*(%s)(?![\w(])" % (OPERAND, OPERAND))
    for m in pat.finditer(text):
        lhs, op, rhs = m.group(1), m.group(2), m.group(3)
        if lhs == rhs or op not in COMMUTATIVE:
            continue
        if op == "*" and _is_pointer_decl(text, text.find("*", m.start(1) + len(lhs))):
            continue
        swapped = "%s %s %s" % (rhs, op, lhs)
        out.append(("swap:%s%s%s@%d" % (lhs, op, rhs, m.start()),
                    text[:m.start()] + swapped + text[m.end():]))
    return out


def r2_compound_flip(text):
    """`x OP= y;` -> `y OP= x;`, rewriting the single later use of x into y.

    Only fires when x is read exactly once after the statement, which is what makes the
    rewrite meaning-preserving: the value is identical, only its home register changes.
    """
    out = []
    lines = text.split("\n")
    pat = re.compile(r"^(\s*)(%s)\s*([&|^+*])=\s*(%s)\s*;\s*$" % (IDENT, IDENT))
    for i, ln in enumerate(lines):
        m = pat.match(ln)
        if not m:
            continue
        indent, dst, op, src = m.groups()
        rest = "\n".join(lines[i + 1:])
        if len(re.findall(r"\b%s\b" % re.escape(dst), rest)) != 1:
            continue
        if re.search(r"\b%s\b" % re.escape(src), rest):
            continue
        new = list(lines)
        new[i] = "%s%s %s= %s;" % (indent, src, op, dst)
        tail = re.sub(r"\b%s\b" % re.escape(dst), src, "\n".join(lines[i + 1:]), count=1)
        out.append(("cflip:%s%s=%s" % (dst, op, src),
                    "\n".join(new[:i + 1]) + "\n" + tail))
    return out


def r3_postinc_migrate(text):
    """`T q = p;` + `*p++ = v;`  <->  `T q = p++;` + `*q = v;` (strcat's colouring flip)."""
    out = []
    lines = text.split("\n")
    decl = re.compile(r"^(\s*)([\w:]+\s*\*+\s*)(%s)\s*=\s*(%s)\s*;\s*$" % (IDENT, IDENT))
    for i, ln in enumerate(lines):
        m = decl.match(ln)
        if not m:
            continue
        indent, typ, q, p = m.groups()
        for j in range(i + 1, min(i + 4, len(lines))):
            st = re.match(r"^(\s*)\*%s\+\+\s*=\s*(.+);\s*$" % re.escape(p), lines[j])
            if not st:
                continue
            new = list(lines)
            new[i] = "%s%s%s = %s++;" % (indent, typ, q, p)
            new[j] = "%s*%s = %s;" % (st.group(1), q, st.group(2))
            out.append(("postinc:%s/%s" % (q, p), "\n".join(new)))
            break
        # reverse direction
        rev = re.match(r"^(\s*)([\w:]+\s*\*+\s*)(%s)\s*=\s*(%s)\+\+\s*;\s*$" % (IDENT, IDENT), ln)
        if rev:
            indent, typ, q, p = rev.groups()
            for j in range(i + 1, min(i + 4, len(lines))):
                st = re.match(r"^(\s*)\*%s\s*=\s*(.+);\s*$" % re.escape(q), lines[j])
                if not st:
                    continue
                new = list(lines)
                new[i] = "%s%s%s = %s;" % (indent, typ, q, p)
                new[j] = "%s*%s++ = %s;" % (st.group(1), p, st.group(2))
                out.append(("postinc-rev:%s/%s" % (q, p), "\n".join(new)))
                break
    return out


def _independent(a, b):
    """True when neither adjacent statement can see the other's effect."""
    def names(s):
        return set(re.findall(IDENT, s))
    if "(" in a and "(" in b:                 # calls may share hidden state
        return False
    lhs_a = a.split("=")[0] if "=" in a else ""
    lhs_b = b.split("=")[0] if "=" in b else ""
    wa, wb = names(lhs_a), names(lhs_b)
    if not wa or not wb:
        return False
    if wa & names(b) or wb & names(a):
        return False
    return "++" not in a and "++" not in b and "--" not in a and "--" not in b


def _declares(s):
    """The identifier a declaration DECLARES: the last one before `=`, or before `;` when bare."""
    ids = re.findall(IDENT, s.split("=")[0])
    return ids[-1] if ids else None


def _decl_independent(a, b):
    """Two DECLARATIONS may swap when neither reads the name the other declares.

    `_independent` treats every identifier left of `=` as written, which for a declaration includes
    the TYPE: `int a = 1;` and `int b = 2;` both "wrote" `int`, so they intersected and recipe #9 --
    the primary callee-saved lever -- never fired on a same-type pair. A bare `int b;` has no `=` at
    all, so its written set came out empty and it was refused too. Between them that is nearly every
    real declaration run.
    """
    na, nb = _declares(a), _declares(b)
    if not na or not nb or na == nb:
        return False
    if "(" in a and "(" in b:                 # calls may share hidden state
        return False
    reads = lambda s: set(re.findall(IDENT, s.split("=", 1)[1])) if "=" in s else set()
    if na in reads(b) or nb in reads(a):
        return False
    return not any(op in a + b for op in ("++", "--"))


def _r4_local_lines(text):
    token = r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    code = re.sub(token, lambda m: re.sub(r'[^\n]', ' ', m.group()), text)
    code = re.sub(r'(?m)^[ \t]*#[^\n]*', '', code)
    scopes, header, local = [], '', set()
    for i, line in enumerate(code.split('\n')):
        if scopes and scopes[-1]:
            local.add(i)
        for char in line + '\n':
            if char == '{':
                record = re.search(r'\b(?:struct|class|union)\b[^(){};]*$', header)
                function = (re.search(r'\)\s*(?:const\s*)?$', header)
                            and not re.search(r'\b(?:if|for|while|switch|catch)\s*\(', header)
                            and '=' not in header)
                scopes.append(False if record else bool(function or (scopes and scopes[-1])))
                header = ''
            elif char == '}':
                if scopes:
                    scopes.pop()
                header = ''
            elif char == ';':
                header = ''
            else:
                header += char
    return local


def r4_decl_reorder(text):
    """Swap two adjacent independent declarations (recipe #9: reverse definition order)."""
    out = []
    lines = text.split("\n")
    local = _r4_local_lines(text)
    decl = re.compile(r"^\s*(unsigned |signed |const )*[\w:]+\s*\**\s*%s\s*(=[^;]*)?;\s*$" % IDENT)
    for i in range(len(lines) - 1):
        if i not in local or i + 1 not in local:
            continue
        a, b = lines[i], lines[i + 1]
        if not (decl.match(a) and decl.match(b)):
            continue
        if a.strip().startswith(("return", "//")) or not _decl_independent(a, b):
            continue
        new = list(lines)
        new[i], new[i + 1] = b, a
        out.append(("declswap@%d" % i, "\n".join(new)))
    return out


def r5_stmt_swap(text):
    """Swap two adjacent independent assignments, initialised declarations or member updates."""
    out = []
    lines = text.split("\n")
    stmt = re.compile(r"^\s*(?!(?:else|return|do|goto|case|default|delete|throw)\b)(?:%s[\s*]+)*%s(?:\(\))?(?:\s*(?:->|\.)\s*%s)*\s*[-+*/&|^]?=(?!=)[^;]+;\s*$"
                      % (IDENT, IDENT, IDENT))
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if not (stmt.match(a) and stmt.match(b)) or not _independent(a, b):
            continue
        new = list(lines)
        new[i], new[i + 1] = b, a
        out.append(("stmtswap@%d" % i, "\n".join(new)))
    return out


def r6_fold_test(text):
    """`if ((a OP b OP K) != 0)` -> `a OP= b;` + `if ((a OP K) != 0)`, and the b-sided form.

    Folding two of the three terms into a named variable is what decides which register the
    combining instruction writes; strcpy needed exactly this and no operand permutation of
    the single expression could reach it.
    """
    out = []
    lines = text.split("\n")
    pat = re.compile(r"^(\s*)(if|while)\s*\(\((%s)\s*([&|^])\s*(%s)\s*([&|^])\s*([\w()x]+)\)\s*(!=|==)\s*0\)(.*)$"
                     % (IDENT, IDENT))
    for i, ln in enumerate(lines):
        m = pat.match(ln)
        if not m:
            continue
        indent, kw, a, op1, b, op2, k, cmp_, tail = m.groups()
        if kw == "while" or op1 != op2:
            continue                       # only fold where the two operators agree
        for keep, other in ((a, b), (b, a)):
            new = list(lines)
            new[i] = ("%s%s %s= %s;\n%s%s ((%s %s %s) %s 0)%s"
                      % (indent, keep, op1, other, indent, kw, keep, op2, k, cmp_, tail))
            out.append(("fold:%s%s=%s" % (keep, op1, other), "\n".join(new)))
    return out


DECL = re.compile(r"^(\s*)((?:unsigned |signed |const |static |volatile )*[\w:]+\s*\**\s*)"
                  r"(%s)\s*=\s*([^;]+);\s*$" % IDENT)
# An uninitialised declaration (`int i;`) is still a function-scope slot that ranks ahead of
# anything declared in an inner block, so r11 has to count these when placing a hoisted one.
BAREDECL = re.compile(r"^\s*(?:unsigned |signed |const |static |volatile )*[\w:]+\s*\**\s*"
                      r"%s\s*;\s*$" % IDENT)


def r7_decl_split(text):
    """`T x = e;` -> `T x;` ... `x = e;`.

    Splitting moves the point where the value becomes live without moving the declaration,
    which is the other half of recipe #9: callee-saved registers follow definition order, so a
    value defined later takes a later register even though the declaration stayed put.
    """
    out = []
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = DECL.match(ln)
        if not m:
            continue
        indent, typ, name, expr = m.groups()
        if "(" in expr:                      # a call may not be moved across other statements
            continue
        new = list(lines)
        new[i] = "%s%s%s;\n%s%s = %s;" % (indent, typ, name, indent, name, expr)
        out.append(("declsplit:%s" % name, "\n".join(new)))
    return out


def r8_decl_hoist(text):
    """Move one declaration up to the head of its block: changes definition ORDER, not meaning.

    Only fires for a declaration whose initialiser reads nothing defined in between, so the
    hoisted statement computes exactly the same value.
    """
    out = []
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = DECL.match(ln)
        if not m:
            continue
        indent, typ, name, expr = m.groups()
        if "(" in expr:
            continue
        # find the start of this block
        start = None
        for j in range(i - 1, -1, -1):
            if lines[j].rstrip().endswith("{"):
                start = j + 1
                break
        if start is None or start >= i:
            continue
        between = "\n".join(lines[start:i])
        reads = set(re.findall(IDENT, expr))
        writes = set()
        for b in lines[start:i]:
            mm = re.match(r"^\s*(?:[\w:]+\s*\**\s*)?(%s)\s*(?:=|\+\+|--|[-+*/&|^]=)" % IDENT, b)
            if mm:
                writes.add(mm.group(1))
        if reads & writes or re.search(r"\b%s\b" % re.escape(name), between):
            continue
        new = lines[:start] + [ln] + lines[start:i] + lines[i + 1:]
        out.append(("declhoist:%s" % name, "\n".join(new)))
    return out


def r12_zero_accumulator(text):
    """`T x = e;` -> `T x = 0; x = x + e;`, which makes mwcc EMIT an `add rD, rS, #0`.

    For the whole family that comes out exactly 4 bytes SHORT with one missing instruction, the
    target holds a redundant register copy that clean C never needs. A zero-initialised accumulator
    is the one construct that reliably produces one: mwcc folds the value but still emits the
    arithmetic. Committed proof is SumKeyedLookups02086bf4 -- `short acc = 0; acc = acc + f();`
    compiles to `bl f; add r0, r0, #0; lsl r4, r0, #0x10`. Only 6 of 11717 matched functions contain
    that instruction, so it is a deliberate idiom rather than noise.

    Meaning-preserving: adding zero to the initialiser cannot change an integer result. Restricted
    to integer-typed declarations, since it must not touch pointers or floats.
    """
    out = []
    lines = text.split("\n")
    # DECL is anchored ^...$ and compiled WITHOUT re.M, so it only matches a single line -- every
    # other rule feeds it one line at a time and this one must too.
    for i, ln in enumerate(lines):
        m = DECL.match(ln)
        if not m:
            continue
        indent, typ, name, expr = m.groups()
        t = typ.strip()
        if "*" in typ or t.split()[-1] not in ("int", "short", "char", "long"):
            continue
        for op in ("+", "-"):
            # `x = 0; x = x + e` gives add-rD-rS-0; `x = 0; x = e - x` gives sub-rD-rS-0, which is
            # exactly the residual reported for 020df77c.
            rhs = "%s + %s" % (name, expr) if op == "+" else "%s - %s" % (expr, name)
            new = lines[:i] + ["%s%s%s = 0;" % (indent, typ, name),
                               "%s%s = %s;" % (indent, name, rhs)] + lines[i + 1:]
            out.append(("zeroacc%s:%s" % (op, name), "\n".join(new)))
    return out


def r11_decl_to_function_scope(text):
    """`{ ... T x = e; ... }` -> `T x;` at FUNCTION scope + `x = e;` left in place.

    r8 only lifts a declaration to the head of its OWN block, which cannot change how it ranks
    against a variable declared at function scope. mwcc orders callee-saved candidates by where the
    DECLARATION sits at function scope, and anything declared in an inner block ranks after all of
    them regardless of where it is assigned -- so a block-scoped value can never take the lower
    register while a function-scope one wants it.

    Measured on 0209ed0c: `unsigned char total` declared inside the `if` always lost r6 to `int i`.
    Emitting `unsigned char total;` ABOVE `int i;` and leaving `total = ...;` in place closed the
    function, having defeated ~40 other variants. Position matters and is the whole point: hoisting
    it BELOW `int i;` is completely inert, so every insertion point is generated.
    """
    lines = text.split("\n")
    # function body opens at the first line that is exactly `{`
    body = next((i for i, l in enumerate(lines) if l.strip() == "{"), None)
    if body is None:
        return []
    # the run of function-scope declarations right after it
    slots, k = [body + 1], body + 1
    while k < len(lines) and (DECL.match(lines[k]) or BAREDECL.match(lines[k])):
        k += 1
        slots.append(k)
    out = []
    for i in range(k, len(lines)):
        m = DECL.match(lines[i])
        if not m:
            continue
        indent, typ, name, expr = m.groups()
        # No guard on the initialiser here, unlike r8. r8 MOVES the computation and so must prove
        # nothing in between changes its inputs; r11 leaves the assignment exactly where it is and
        # moves only the declaration, which cannot change evaluation order or meaning. r8's
        # `"(" in expr` guard would reject `*(unsigned char*)(p + 0x8e07)` -- the very case that
        # closed 0209ed0c.
        if not lines[i].startswith(indent + typ.split()[0]):
            continue
        decl = "    %s%s;" % (typ, name)
        assign = "%s%s = %s;" % (indent, name, expr)
        for pos in slots:
            new = lines[:pos] + [decl] + lines[pos:i] + [assign] + lines[i + 1:]
            out.append(("declfnscope:%s@%d" % (name, pos - body), "\n".join(new)))
    return out


def r9_compare_flip(text):
    """`a < b` -> `b > a` (and the other three): same test, opposite operand order in `cmp`."""
    flip = {"<": ">", ">": "<", "<=": ">=", ">=": "<=", "==": "==", "!=": "!="}
    out = []
    pat = re.compile(r"(?<![\w.>])(%s)\s*(<=|>=|==|!=|<|>)\s*(%s)(?![\w(])" % (OPERAND, OPERAND))
    for m in pat.finditer(text):
        lhs, op, rhs = m.group(1), m.group(2), m.group(3)
        if lhs == rhs:
            continue
        swapped = "%s %s %s" % (rhs, flip[op], lhs)
        out.append(("cmpflip:%s%s%s@%d" % (lhs, op, rhs, m.start()),
                    text[:m.start()] + swapped + text[m.end():]))
    return out


def r10_const_local(text):
    """Add/remove `const` on a local pointer: changes ARGUMENT-SETUP ORDER, not the value.

    StringReplaceLanguageTag (0x020757b4) came down to two argument moves emitted in the wrong
    order for one call while the identical call in its own loop was already right. Marking the
    local `char* const tag` flipped exactly those two instructions and matched: a pointer the
    compiler knows cannot change is scheduled differently at a call site.
    """
    out = []
    lines = text.split("\n")
    add = re.compile(r"^(\s*)([\w:]+\s*)(\*+)\s*(%s)(\s*=\s*[^;]+;)\s*$" % IDENT)
    drop = re.compile(r"^(\s*)([\w:]+\s*)(\*+)\s*const\s+(%s)(\s*=\s*[^;]+;)\s*$" % IDENT)
    for i, ln in enumerate(lines):
        m = drop.match(ln)
        if m:
            new = list(lines)
            new[i] = "%s%s%s %s%s" % m.groups()
            out.append(("unconst:%s" % m.group(4), "\n".join(new)))
            continue
        m = add.match(ln)
        if not m or " const " in ln or ln.lstrip().startswith("return"):
            continue
        new = list(lines)
        new[i] = "%s%s%s const %s%s" % m.groups()
        out.append(("const:%s" % m.group(4), "\n".join(new)))
    start = _r46_body(lines)
    if start is None:
        return out
    body = "\n".join(lines[start:])
    for i in range(start, len(lines)):
        m = _R10_SCALAR.match(lines[i])
        if not m or _r52_written(m.group(3), body.replace(lines[i], "", 1)):
            continue
        new = list(lines)
        new[i] = "%sconst %s" % (m.group(1), lines[i][len(m.group(1)):])
        out.append(("constscalar:%s@%d" % (m.group(3), i), "\n".join(new)))
    return out


_R10_SCALAR = re.compile(r"^(\s+)((?:unsigned |signed )?(?:int|short|char|long))\s+([A-Za-z_]\w*)\s*=[^;]+;\s*$")


def r14_decl_move(text):
    """Lift ONE declaration out of a run and reinsert it at every other position in that run.

    r4 only swaps ADJACENT declarations, so reaching an order three positions away costs three hops
    through intermediate orders that score no better -- a plateau the hill-climb will not cross. On
    021bd5d0 the target order was `q byte8 word0 maxMP maxHP half6` and the candidate had word0 last:
    unreachable by adjacent swaps at depth 3, one hop for a move. Decl order matters here because
    `#pragma opt_propagation off` stops mwcc sinking each def to its use, so the declarations ARE the
    emission order and therefore the register ladder (which is what decides stm/stmib fusion of the
    stack arguments).
    """
    out = []
    lines = text.split("\n")
    decl = re.compile(r"^\s*(unsigned |signed |const |struct )*[\w:]+\s*\**\s*%s\s*=[^;]*;\s*$" % IDENT)
    runs, cur = [], []
    for i, ln in enumerate(lines):
        if decl.match(ln) and not ln.strip().startswith(("return", "//")):
            cur.append(i)
        else:
            if len(cur) > 2:
                runs.append(cur)
            cur = []
    if len(cur) > 2:
        runs.append(cur)
    for run in runs:
        block = [lines[i] for i in run]
        for src in range(len(block)):
            for dst in range(len(block)):
                if abs(src - dst) < 2:            # adjacent moves are r4's job
                    continue
                order = block[:src] + block[src + 1:]
                order.insert(dst, block[src])
                new = list(lines)
                for slot, ln in zip(run, order):
                    new[slot] = ln
                out.append(("declmove:%d->%d@%d" % (src, dst, run[0]), "\n".join(new)))
    return out


def r15_zero_accumulator_assign(text):
    """`x = e;` -> `x = 0; x = x + e;` for an integer local ALREADY declared above.

    r12 does this for a DECLARATION only, so a residue that lives on a plain assignment -- a call
    result copied into a variable that already exists -- was never reachable by the sweep, and
    `core.md` told workers to report `add-zero` and move on. The flagship example it called
    unbeatable after ~73 configurations, func_ov000_02153e40, is committed as exactly this shape:
    `int n = 0; n = n + f(...);`. Same law as r12: mwcc folds the zero but still emits the
    arithmetic, giving the `add rD, rS, #0` clean C never produces.

    Meaning-preserving: the assignment overwrites x, so zeroing it first cannot change the result.
    Integer locals only -- never pointers or floats.
    """
    out = []
    lines = text.split("\n")
    intdecl = re.compile(r"^\s*(?:unsigned |signed |const )*(int|short|char|long)\s+(%s)\s*[;=]" % IDENT)
    ints = {m.group(2) for l in lines for m in [intdecl.match(l)] if m}
    assign = re.compile(r"^(\s*)(%s)\s*=\s*([^;=][^;]*);\s*$" % IDENT)
    for i, ln in enumerate(lines):
        m = assign.match(ln)
        if not m:
            continue
        indent, name, expr = m.groups()
        if name not in ints or expr.strip().startswith("0"):
            continue
        for op in ("+", "-"):
            rhs = "%s + %s" % (name, expr) if op == "+" else "%s - %s" % (expr, name)
            new = lines[:i] + ["%s%s = 0;" % (indent, name),
                               "%s%s = %s;" % (indent, name, rhs)] + lines[i + 1:]
            out.append(("zeroaccassign%s:%s@%d" % (op, name, i), "\n".join(new)))
    return out


def r13_dup_pool_literal(text):
    """`&data_x` -> `((__typeof__(&data_x))0xADDR)`: forces a SECOND literal-pool word.

    mwcc folds every reference to one symbol into a single pool entry, but the ROM routinely holds
    the same address twice (one entry for the field reads, one for the call that takes the address),
    so the candidate comes out exactly 4 bytes short with one `ldr rX,[pc,#imm]` off by 4. An
    absolute-address constant is a different node to the compiler, so it gets its own pool word --
    the config-free half of the alias trick in core.md, and the form already committed in
    WaitForBattleDefaultThenFree_022357b4. `__typeof__` keeps it type-generic and is available
    because the project compiles with `-gccext,on`. Cracked the ov031 family on 2026-08-20:
    02223b1c, 022231f0, 0222d1b8, 0222dee0, 02237600.
    """
    out = []
    pat = re.compile(r"&(data_(?:ov\d+_)?([0-9a-fA-F]{8}))\b")
    for m in pat.finditer(text):
        if text[:m.start()].endswith("__typeof__("):
            continue
        sym, hexa = m.group(1), m.group(2)
        # `&data_x[i]` needs the ELEMENT pointer, not a pointer to the array: `__typeof__(&data_x)`
        # is `T(*)[]`, so indexing it steps by the whole array and the candidate either fails to
        # compile or addresses garbage. Keep the `&`, cast to `T*`.
        if text[m.end():m.end() + 1] == "[":
            rep = "&((__typeof__(&%s[0]))0x%s)" % (sym, hexa.upper())
        else:
            rep = "((__typeof__(&%s))0x%s)" % (sym, hexa.upper())
        out.append(("dupliteral:%s@%d" % (sym, m.start()),
                    text[:m.start()] + rep + text[m.end():]))
    # A SYMBOL DOES NOT NEED AN `&` TO OWN A POOL WORD. `data_x[i]` and `data_x.field` reference the
    # same address and mwcc folds them into the same single entry, so an array or struct global read
    # at two sites comes out exactly 4 bytes short with no `&` anywhere for the pattern above to
    # match. `ov023:021fa2f4` was that: `data_ov023_021fea0c[3].fn` in one branch and
    # `&data_ov023_021fea0c[idx]` in another, SIZE 0x78 against slot 0x7c, and 150 compiles of this
    # sweep moved nothing because no candidate could be generated at all.
    use = re.compile(r"(?<![&\w.>])(data_(?:ov\d+_)?([0-9a-fA-F]{8}))\s*(\[|\.)")
    for m in use.finditer(text):
        sym, hexa, how = m.group(1), m.group(2), m.group(3)
        if how == "[":
            rep = "((__typeof__(&%s[0]))0x%s)[" % (sym, hexa.upper())
        else:
            rep = "((__typeof__(&%s))0x%s)->" % (sym, hexa.upper())
        out.append(("dupliteral%s:%s@%d" % ("idx" if how == "[" else "mem", sym, m.start()),
                    text[:m.start()] + rep + text[m.end():]))
    return out


_TYPEWORDS = {"int", "short", "char", "long", "unsigned", "signed", "const", "void", "struct",
              "float", "double", "static", "register", "volatile"}


def _writes(stmt):
    """The identifier a statement assigns, or None. `int x = e;` and `x = e;` both give x."""
    if "=" not in stmt:
        return None
    lhs = stmt.split("=", 1)[0]
    if any(op in lhs for op in ("==", "!", "<", ">")):
        return None
    ids = [i for i in re.findall(IDENT, lhs) if i not in _TYPEWORDS]
    return ids[-1] if len(ids) == 1 else None       # a[i] = / p->f = are not simple writes


def _reads(stmt):
    rhs = stmt.split("=", 1)[1] if "=" in stmt else stmt
    return {i for i in re.findall(IDENT, rhs) if i not in _TYPEWORDS}


def _strip_code(ln):
    ln = re.sub(r"//.*", "", ln)
    return re.sub(r"'(\\.|[^'])*'|\"(\\.|[^\"])*\"", "", ln)


def _line_depths(lines):
    """Brace depth at the START of each line."""
    d, out = 0, []
    for ln in lines:
        code = _strip_code(ln)
        out.append(d)
        d += code.count("{") - code.count("}")
    return out


_UNBRACED_HEAD = re.compile(r"^(if|while|for|else\s+if)\b.*\)\s*$|^else\s*$")


def _is_unbraced_body(lines, k):
    """True if line k is the lone body of a brace-less if/while/for/else above it."""
    for prev in range(k - 1, -1, -1):
        s = _strip_code(lines[prev]).strip()
        if not s:
            continue
        return bool(_UNBRACED_HEAD.match(s))
    return False


def r16_stmt_move(text):
    """Lift ONE pure statement and reinsert it at a non-adjacent position it can legally reach.

    `r5_stmt_swap` only exchanges ADJACENT statements, so an ordering three positions away costs
    three hops through orders that score no better -- the same plateau `r4` had for declarations
    before `r14` fixed it. Statement order is the schedule, and workers keep reporting exactly this:
    "the target schedules extraSave's stack store ahead of extraOff/dst0's" (0221db48, 185 bytes),
    "scratch-reg/scheduling residual ... colorsweep + full pragma menu + 9 source rewrites all
    byte-identical" (020d39dc, 59 bytes with 497/556 already matching). Adjacent swaps cannot reach
    those orders, and every weaker rewrite compiles to the same bytes.

    Legality is conservative: only a call-free, side-effect-free statement with a single simple
    assignment target moves, it may not cross a statement containing a call (which could touch
    memory it reads), and it may not cross any statement that reads or writes the same name.
    `_independent` is not reused here -- it counts the type keyword as a written name, so
    `int x = a[0];` and `int y = b[0];` looked dependent through `int` and nothing ever moved.

    The move never crosses a brace or a control header: the span must be straight-line code at ONE
    depth. Checking only the statement lines between the two positions misses a `while (x) {` in
    between -- it holds no `;` -- and lifts a loop body out of its loop.
    """
    out = []
    lines = text.split(chr(10))
    depth = _line_depths(lines)
    idx = [i for i in _body_lines(text)
           if lines[i].strip().endswith(";")
           and not lines[i].strip().startswith(("return", "}", "{", "case", "goto", "break",
                                                "continue", "extern", "#"))]
    for pos, i in enumerate(idx):
        src = lines[i]
        w = _writes(src)
        if not w or "(" in src or "++" in src or "--" in src or "*" in src.split("=", 1)[0]:
            continue
        if _is_unbraced_body(lines, i):
            continue
        for tpos, j in enumerate(idx):
            if abs(tpos - pos) < 2:                  # adjacent moves are r5's job
                continue
            if _is_unbraced_body(lines, j):
                continue
            lo, hi = (min(i, j), max(i, j))
            span = range(lo, hi + 1)
            if any(depth[k] != depth[i] for k in span):
                continue
            if any("{" in _strip_code(lines[k]) or "}" in _strip_code(lines[k]) for k in span):
                continue
            crossed = [_strip_code(lines[k]) for k in span if k != i]
            if any("(" in c or "++" in c or "--" in c for c in crossed):
                continue
            if any(w in _reads(c) or _writes(c) == w or (_writes(c) in _reads(src) if _writes(c) else False)
                   for c in crossed):
                continue
            rest = [ln for k, ln in enumerate(lines) if k != i]
            tgt = j - 1 if j > i else j
            out.append(("stmtmove:%d->%d" % (i, j),
                        chr(10).join(rest[:tgt] + [src] + rest[tgt:])))
    return out


def r17_decl_permute(text):
    """Enumerate FULL permutations of a short declaration run, not one move at a time.

    `r4` swaps adjacent declarations and `r14` moves one; both are single steps in a hill-climb, so a
    residue that needs several declarations reordered TOGETHER sits on a plateau neither can cross --
    every one-step neighbour scores the same or worse. That is what "regalloc cascade across many
    locals, colorsweep inert 3x" means (0204e038, 400 bytes, $8.21, closest 217/400), and it is the
    shape `permsweep.py` cracked on 021bd5d0 by enumerating orders exhaustively.

    Bounded on purpose: only runs of 3..5 declarations, and at most PERMUTE_CAP candidates, because
    the sweep's budget is counted in compiles. 3 decls = 5 extra orders, 4 = 23, 5 = 119 sampled down
    to the cap.
    """
    PERMUTE_CAP = 40
    out = []
    lines = text.split(chr(10))
    decl = re.compile(r"^\s*(unsigned |signed |const |struct )*[\w:]+\s*\**\s*%s\s*=[^;]*;\s*$" % IDENT)
    runs, cur = [], []
    for i, ln in enumerate(lines):
        if decl.match(ln) and not ln.strip().startswith(("return", "//")):
            cur.append(i)
        else:
            if 3 <= len(cur) <= 5:
                runs.append(cur)
            cur = []
    if 3 <= len(cur) <= 5:
        runs.append(cur)
    for run in runs:
        block = [lines[i] for i in run]
        perms = [p for p in itertools.permutations(block) if list(p) != block]
        if len(perms) > PERMUTE_CAP:
            step = len(perms) // PERMUTE_CAP
            perms = perms[::step][:PERMUTE_CAP]
        for n, order in enumerate(perms):
            new = list(lines)
            for slot, ln in zip(run, order):
                new[slot] = ln
            out.append(("declperm:%d@%d" % (n, run[0]), chr(10).join(new)))
    return out


NARROW = r"\*\(\s*(?:unsigned\s+)?(?:char|short)\s*\*\s*\)"


def r18_narrow_bind(text):
    """Bind a narrow load to an `int` local before comparing it, and the reverse.

    A `char`/`short` load compared with a signed operator emits the UNSIGNED condition when the
    load is written inline (`blo`/`bls`/`bhi`) and the signed one (`blt`/`ble`/`bgt`) when it is
    bound to an `int` first. On 02061c04 this closed a 20-branch range ladder in case 0xc5 and two
    more single tests, all of which had been read as "the target compares differently".
    """
    out = []
    lines = text.split("\n")
    bind = re.compile(r"^(\s*)if \((\*\((?:[^()]|\([^()]*\))*\))\s*(<=|>=|<|>)\s*([\w']+)\)\s*$")
    for i, ln in enumerate(lines):
        m = bind.match(ln)
        if not m or ln.lstrip().startswith("//"):
            continue
        if not re.search(r"(?:unsigned|signed)?\s*(?:char|short)\s*\*", m.group(2)):
            continue
        pad, load, op, rhs = m.groups()
        name = "_nb%d" % i
        new = list(lines)
        new[i:i + 1] = ["%sint %s = %s;" % (pad, name, load),
                        "%sif (%s %s %s)" % (pad, name, op, rhs)]
        out.append(("narrowbind@%d" % i, "\n".join(new)))
    return out


def r19_ternary_split(text):
    """`X = C ? A : B;` -> `if (C) X = A; else X = B;` and back.

    The target emits two CONDITIONAL stores (`strhge` / `strhlt`); the ternary emits a conditional
    move into one register and a single store. Same value, one instruction apart, and the split
    form is what case 0xbc needed.
    """
    out = []
    lines = text.split("\n")
    tern = re.compile(r"^(\s*)([^;=]+?)\s*=\s*\(?([^;?]+?)\)?\s*\?\s*([^;:]+?)\s*:\s*([^;]+?);\s*$")
    for i, ln in enumerate(lines):
        m = tern.match(ln)
        if not m or "?" in m.group(4) or "?" in m.group(5):
            continue
        pad, dst, cond, a, b = m.groups()
        new = list(lines)
        new[i:i + 1] = ["%sif (%s)" % (pad, cond.strip()),
                        "%s    %s = %s;" % (pad, dst.strip(), a.strip()),
                        "%selse" % pad,
                        "%s    %s = %s;" % (pad, dst.strip(), b.strip())]
        out.append(("ternsplit@%d" % i, "\n".join(new)))
    return out


def r20_bool_materialise(text):
    """`f(a == b)` -> `int t = a == b; f(t);`.

    The target builds a boolean argument in a register and copies it (`mov r2,#0` … `mov r2,#1` …
    `mov r0,r2`) where the inline comparison emits a conditional move at the call. Case 0xc5's
    first call and case 0xa2 both turned on this.
    """
    out = []
    lines = text.split("\n")
    call = re.compile(r"^(\s*)([\w:]+)\(([^;]*?)([\w>\-\.\[\]()]+\s*(?:==|!=)\s*[\w>\-\.\[\]()]+)"
                      r"([^;]*)\);\s*$")
    for i, ln in enumerate(lines):
        m = call.match(ln)
        if not m or ln.lstrip().startswith("//"):
            continue
        # The capture can start mid-expression and pick up an unbalanced fragment; a candidate that
        # does not parse wastes a compile and reads as "the rewrite made it worse".
        if m.group(4).count("(") != m.group(4).count(")"):
            continue
        pad, fn, pre, cond, post = m.groups()
        name = "_bm%d" % i
        new = list(lines)
        new[i:i + 1] = ["%sint %s = %s;" % (pad, name, cond.strip()),
                        "%s%s(%s%s%s);" % (pad, fn, pre, name, post)]
        out.append(("boolvar@%d" % i, "\n".join(new)))
    return out


def r21_cast_width(text):
    """Add or remove a narrowing cast on a call argument.

    The conversion the target performs is a property of the CALLEE's real parameter type, which is
    ours to choose in the extern declaration -- and one cast too many costs a sign-extending pair
    at every call site (case 0xd8: three shifts against the target's two).
    """
    casts = ("(short)", "(unsigned short)", "(unsigned char)", "(signed char)")
    out = []
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(("//", "#", "*")) or "(" not in ln:
            continue
        for c in casts:
            j = ln.find(c)
            while j >= 0:
                new = list(lines)
                new[i] = ln[:j] + ln[j + len(c):]
                out.append(("uncast:%s@%d.%d" % (c, i, j), "\n".join(new)))
                j = ln.find(c, j + 1)
    # Only the REMOVE direction. Adding a cast needs the argument's real type to be meaningful, and
    # proposing `(short)somePointer` at every call site buries the whole budget in candidates that
    # cannot compile.
    return out


# r17 enumerates permutations and is by far the widest rule, so it stays LAST: neighbours() dedupes
# in order, and putting it earlier buries every cheap single-step neighbour under 40 permutations.
def r22_inline_single_use(text):
    """Fold a local that is declared and used exactly once into its use, when the two are adjacent.

    EVERY other rule here rewrites statement ORDER, and mwcc's scheduler discards source order for
    independent operations -- 020cdfd8 compiles byte-identically with its two loads written either
    way round. What order cannot change is how many values are LIVE at once, and that is what picks
    scratch registers: the ROM reusing r3 for both arguments of a call where we hold `ip` and `r1`
    means its source never had two named temporaries alive there. Removing the temporary is a change
    to the expression TREE, which is the thing the scheduler actually reads.

    Deliberately narrow. The declaration and its single use must be consecutive statements, so
    nothing can be reordered across a side effect and no other statement can write to the inputs in
    between; the initialiser must be a plain expression; and the name must occur exactly once more
    in the whole file, so this cannot silently change a second reference.
    """
    out = []
    lines = text.split("\n")
    decl = re.compile(r"^(\s*)((?:unsigned |signed |const |struct |static )*[A-Za-z_]\w*"
                      r"(?:\s*\*)*)\s+([A-Za-z_]\w*)\s*=\s*(.+);\s*$")
    for i, ln in enumerate(lines):
        m = decl.match(ln)
        if not m or ln.lstrip().startswith("//") or i + 1 >= len(lines):
            continue
        pad, _type, name, init = m.groups()
        if re.search(r"[,=]|\+\+|--", init) or "?" in init:
            continue
        # COUNT REAL USES, NOT MEMBER NAMES. `\bx\b` also matches the `x` in `dst->x` and `src->x`,
        # so on 020cdfd8 -- the function this rule was written for -- every candidate name looked
        # like it was read four times and the rule never fired once.
        word = r"(?<![.>\w])%s\b" % re.escape(name)
        # ...and count only inside the FUNCTION. Above the `// USA:` tag sit the struct definitions,
        # whose field declarations (`short x;`) are bare names too, so 020cdfd8's four temps each
        # counted twice over and the rule stayed silent a second time.
        _u = text.find("// USA:")
        body = text[_u:] if _u >= 0 else text
        if len(re.findall(word, body)) != 2:
            continue
        # The use is usually NOT the next line. `T a = x; T b = y; use(a); use(b);` is the shape the
        # whole scratch-register family is written in, and an adjacency-only rule skips every one of
        # them. Walk forward while the lines in between are declarations with side-effect-free
        # initialisers -- those cannot write to this initialiser's inputs, so nothing is reordered
        # across an effect even though the statements are not adjacent.
        j = i + 1
        while j < len(lines) and not re.search(word, lines[j]):
            between = decl.match(lines[j])
            if not between or re.search(r"[(]", between.group(4)) or j - i > 4:
                j = -1
                break
            j += 1
        if j < 0 or j >= len(lines):
            continue
        use = lines[j]
        if len(re.findall(word, use)) != 1:
            continue
        if re.match(r"^\s*(?:\}|\{|//)", use) or not use.strip():
            continue
        new = list(lines)
        new[j] = re.sub(word, "(%s)" % init, use)
        del new[i]
        out.append(("inline1:%s" % name, "\n".join(new)))
    return out


def r23_single_exit_result(text):
    """`if (c) { return K; } ... return M;` -> one result variable, body inside the negated test.

    The ROM signature is a return constant materialised UNCONDITIONALLY before the compare, with a
    conditional pop after it:

        cmp r7, #0x23 / mov r0, #0 / pophs {...}        target
        cmp r6, #0x23 / movhs r0, #0 / pophs {...}      an early `return 0;`

    An early return lets mwcc predicate the constant; a result variable that is live past the test
    forces it to be materialised first, and mwcc still emits the conditional pop as its own early
    exit. Closed `main:020a1bb4` and its twin `020a1ccc` (33 and 62 bytes -> 32 each).

    Only fires when both returns are integer literals, since the result variable is typed `int`.
    """
    if "_r23" in text:
        return []
    lines = text.split("\n")
    ends = [i for i, l in enumerate(lines) if l.rstrip() == "}"]
    if not ends:
        return []
    lit = r"-?(?:0[xX][0-9a-fA-F]+|\d+)"
    finals = [i for i in range(ends[-1] - 1, -1, -1)
              if re.match(r"^\s*return\s+%s\s*;\s*$" % lit, lines[i])]
    if not finals:
        return []
    fin = finals[0]
    mval = re.match(r"^\s*return\s+(%s)\s*;\s*$" % lit, lines[fin]).group(1)
    out = []
    guard = re.compile(r"^(\s*)if\s*\((.+)\)\s*\{\s*$")
    for i, ln in enumerate(lines):
        m = guard.match(ln)
        if not m or i + 2 >= fin:
            continue
        mr = re.match(r"^\s*return\s+(%s)\s*;\s*$" % lit, lines[i + 1])
        if not mr or lines[i + 2].strip() != "}":
            continue
        pad, cond, k = m.group(1), m.group(2), mr.group(1)
        new = list(lines)
        new[fin] = "%s    _r23 = %s;\n%s}\n%sreturn _r23;" % (pad, mval, pad, pad)
        new[i:i + 3] = ["%sint _r23 = %s;" % (pad, k), "%sif (!(%s)) {" % (pad, cond)]
        out.append(("singleexit@%d" % i, "\n".join(new)))
    return out


def r24_arm_invert(text):
    """`if (c) { small } else { big }` -> `if (!(c)) { big } else { small }`.

    mwcc predicates the SECOND arm far more readily than the first, so a ROM that branches over the
    big arm is reproduced by writing that arm as the `if`:

        moveq r4, #0 / beq .skip / strb / strb / mov r4,#1     target
        moveq r4, #0 / strbne / strbne / movne r4, #1          from `if (n==0) {..} else {..}`

    Pure negation, so it holds whatever the arms contain, and it cannot loop: it fires only when the
    else-arm is strictly larger, which the swap reverses.
    """
    lines = text.split(chr(10))
    depth = _line_depths(lines)
    head = re.compile(r"^(\s*)if\s*\((.+)\)\s*\{\s*$")
    out = []
    for i, ln in enumerate(lines):
        m = head.match(ln)
        if not m:
            continue
        pad, cond = m.group(1), m.group(2)
        d = depth[i]
        mid = end = None
        for j in range(i + 1, len(lines)):
            # A line that CLOSES the arm starts one level in: `_line_depths` reports the depth
            # BEFORE the line's own braces are counted, so `} else {` and the final `}` both sit at
            # d+1 while the `if` header sits at d. Matching on d found nothing at all.
            if depth[j] != d + 1:
                continue
            s = lines[j].strip()
            if mid is None and s == "} else {":
                mid = j
            elif mid is not None and s == "}":
                end = j
                break
            elif s.startswith("} else if"):
                break
        if mid is None or end is None:
            continue
        a, b = lines[i + 1:mid], lines[mid + 1:end]
        n_a = len([l for l in a if l.strip()])
        n_b = len([l for l in b if l.strip()])
        if not n_a or n_b <= n_a:
            continue
        new = (lines[:i]
               + ["%sif (!(%s)) {" % (pad, cond)] + b + ["%s} else {" % pad] + a + [lines[end]]
               + lines[end + 1:])
        out.append(("arminvert@%d" % i, chr(10).join(new)))
    return out


def r25_null_pointer_diff(text):
    """Give an integer parameter a `sub Rd, Rs, #0` by taking it as `char *` and binding it back
    with a null-pointer difference.

    `x - 0` is folded by the front end in every spelling -- literal, enum, `static const`, template
    non-type parameter, propagated local, all 19 compiler builds -- but POINTER minus a null pointer
    is not, so `(int)(p - (char *)0)` is the only source form that reaches codegen as a real
    subtract of an immediate zero. That instruction is also a live-range split: it copies the
    parameter out of its incoming register, which shifts every later scratch assignment by one.

    The parameter is renamed rather than the body, so nothing else in the function moves. Each
    insertion point is a separate candidate because the binding's position in the declaration run
    decides which register the copy lands in.
    """
    if "(char *)0" in text:
        return []
    lines = text.split(chr(10))
    head = re.compile(r"^(.*?\b(\w+)\()([^)]*)(\)\s*\{)\s*$")
    out = []
    for i, ln in enumerate(lines):
        m = head.match(ln)
        if not m or "//" in ln or ln.lstrip().startswith("#"):
            continue
        params = m.group(3).split(",")
        for pi, p in enumerate(params):
            pm = re.match(r"^(\s*)(?:int|s32|unsigned int|unsigned|u32)\s+(\w+)\s*$", p)
            if not pm:
                continue
            name = pm.group(2)
            newp = list(params)
            newp[pi] = "%schar *%s_p" % (pm.group(1), name)
            hdr = m.group(1) + ",".join(newp) + m.group(4)
            bind = "    int %s = (int)(%s_p - (char *)0);" % (name, name)
            # after each leading declaration, and before all of them
            spots = [i + 1]
            for j in range(i + 1, min(i + 6, len(lines))):
                if re.match(r"^\s*\w[\w \*]*\s+\w+\s*(=[^;]*)?;\s*$", lines[j]):
                    spots.append(j + 1)
                else:
                    break
            for s in spots:
                new = lines[:i] + [hdr] + lines[i + 1:s] + [bind] + lines[s:]
                out.append(("nullptrdiff@%s@%d" % (name, s - i), chr(10).join(new)))
    return out


def _split_top(expr, op):
    """Split `expr` at top-level occurrences of `op`, ignoring anything inside brackets.

    `op` == '*' means the BINARY operator only: a '*' that opens a dereference or sits inside a cast
    is not a split point, which is decided by the preceding non-space character.
    """
    parts, depth, start, prev = [], 0, 0, ""
    for i, ch in enumerate(expr):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == op and depth == 0 and not (op == "*" and prev in ("", "(", "*", "+", "-", "/",
                                                                     "%", "&", "|", "^", "=", "<",
                                                                     ">", ",", "!", "~")):
            parts.append(expr[start:i])
            start = i + 1
        if not ch.isspace():
            prev = ch
    parts.append(expr[start:])
    return [p.strip() for p in parts]


def r26_mla_accumulator(text):
    """`T x = A * K + B;` -> `T x = A; x = x * K + B;` (and the same binding B instead).

    A multiply-accumulate written as one expression lets mwcc choose which operand becomes the
    accumulator, and it chooses the ADDEND: it loads B first and reuses B's register as the `mla`
    destination. The ROM frequently does the opposite -- load the MULTIPLICAND first into a register
    of its own (often `ip`) and write the result somewhere fresh. Binding the multiplicand to the
    result local and then self-assigning forces that shape. On main:0205f9cc it took the residue
    from 10 bytes (both loads in the wrong order, destination reusing the addend) to 5 (a plain
    r3/r4 colour), which r14_decl_move then closed to MATCH.

    Meaning-preserving only while the two operands cannot observe each other, so a right-hand side
    containing a call is skipped -- reordering those loads would reorder side effects.
    """
    out = []
    lines = text.split("\n")
    decl = re.compile(r"^(\s*)((?:unsigned |signed |const |struct )*[\w:]+\s+\**)(%s)\s*=\s*(.+);\s*$"
                      % IDENT)
    call = re.compile(r"(?<![\w)])(?!sizeof\b)[A-Za-z_]\w*\s*\(")
    for i, ln in enumerate(lines):
        m = decl.match(ln)
        if not m or "//" in ln or "?" in ln:
            continue
        indent, typ, name, rhs = m.groups()
        if call.search(rhs) or "," in rhs:
            continue
        terms = _split_top(rhs, "+")
        if len(terms) != 2:
            continue
        muls = [t for t in terms if len(_split_top(t, "*")) == 2]
        if len(muls) != 1:
            continue
        mul = muls[0]
        addend = terms[1] if terms[0] == mul else terms[0]
        a, k = _split_top(mul, "*")
        if not a or not k or not addend:
            continue
        for tag, first, rest in (("mul", a, "%s * %s + %s" % (name, k, addend)),
                                 ("add", addend, "%s * %s + %s" % (a, k, name))):
            new = list(lines)
            new[i] = "%s%s%s = %s;" % (indent, typ, name, first)
            new.insert(i + 1, "%s%s = %s;" % (indent, name, rest))
            out.append(("mlaacc:%s@%s" % (tag, name), "\n".join(new)))
    return out


def r27_pointer_roundtrip(text):
    """`T* p = &g;` -> `T* p = &g; p++;` with every `p->` use rewritten to `(p - 1)->`.

    This is the only known C formulation that defeats copy propagation on a global's address
    WITHOUT the pragma. mwcc cannot fold `&g` into the use while the pointer carries a pending
    increment, so the address is materialised early and stays live across several instructions --
    exactly the ROM shape that `#pragma opt_propagation off` was being used to buy. A later pass
    cancels the `++` against the `- 1`, so the emitted arithmetic is unchanged and the only
    difference is where the address is formed.

    Verified on ZoneFeatures WarpScript_Opcode_6b (0x0201d044) and WarpScript_Opcode_7c
    (0x0201e018): both matched byte-exact at the -O2 default after this rewrite, having previously
    matched only under a file-wide `opt_propagation off`.

    Two forms. A: a pointer local already initialised to `&global`. B: a global whose members are
    read directly (`g.field`), where the file also declares `extern T g;` -- bind it to a local
    first, then apply A. Emits the `p--` / `(p + 1)` mirror as well, since which direction the
    compiler keeps is not predictable from the source.

    Meaning-preserving only while `p` is never reassigned after its declaration, so a pointer that
    is stepped or re-pointed later is skipped.
    """
    out = []
    lines = text.split("\n")
    decl = re.compile(r"^(\s*)([\w:]+)\s*\*\s*(%s)\s*=\s*&\s*(%s)\s*;\s*$" % (IDENT, IDENT))

    def _roundtrip(src_lines, i, indent, name, step, back):
        body = src_lines[i + 1:]
        # A pointer that moves later would make (p - 1) a different address.
        if re.search(r"\b%s\b\s*(?:=[^=]|\+\+|--|\+=|-=)" % re.escape(name), "\n".join(body)):
            return None
        body = [re.sub(r"\b%s\s*->" % re.escape(name), "(%s %s)->" % (name, back), ln)
                for ln in body]
        return src_lines[:i + 1] + ["%s%s%s;" % (indent, name, step)] + body

    for i, ln in enumerate(lines):
        m = decl.match(ln)
        if not m:
            continue
        indent, name = m.group(1), m.group(3)
        for step, back in (("++", "- 1"), ("--", "+ 1")):
            new = _roundtrip(lines, i, indent, name, step, back)
            if new:
                out.append(("roundtrip%s:%s" % (step, name), "\n".join(new)))

    # Form B: no local yet. Bind the global, then hand the result back through form A next hop.
    for m in re.finditer(r"(?m)^\s*extern\s+(?:struct\s+)?([\w:]+)\s+(%s)\s*;\s*$" % IDENT, text):
        typ, glob = m.group(1), m.group(2)
        uses = [i for i, ln in enumerate(lines) if re.search(r"\b%s\s*\." % re.escape(glob), ln)]
        if not uses:
            continue
        first = uses[0]
        indent = re.match(r"\s*", lines[first]).group(0)
        new = list(lines)
        for i in uses:
            new[i] = re.sub(r"\b%s\s*\." % re.escape(glob), "instance_%s->" % glob, new[i])
        new.insert(first, "%s%s* instance_%s = &%s;" % (indent, typ, glob, glob))
        out.append(("bindglobal:%s" % glob, "\n".join(new)))
    return out


def r28_literal_in_equality_arm(text):
    """Inside `if (X == K) { ... }`, rewrite a READ of `X` in the body to the literal `K`.

    The two spellings are numerically identical and mwcc allocates them differently: the comparison
    has already materialised `K` in a register, so the literal form does its arithmetic on THAT
    register, while naming the field emits a fresh `ldr` and picks whatever register the load
    lands in. `020db3f4` is 6 bytes and one wrong mnemonic apart on exactly this, and the ROM takes
    the compare's register (`memset(addr, -16 + 15, ...)`, not `obj->field6a + 15`).

    Only equality arms, only reads, one site per candidate. `!=`, `<` and `>` arms carry no such
    equivalence and a write through `X` would change the program.
    """
    out = []
    cond = re.compile(r"(?:\belse\s+)?\bif\s*\(\s*((?:%s)(?:\s*(?:->|\.)\s*%s)*)\s*==\s*"
                      r"(-?(?:0[xX][0-9a-fA-F]+|\d+))\s*\)\s*\{" % (IDENT, IDENT))
    for m in cond.finditer(text):
        expr, lit = m.group(1), m.group(2)
        depth, end = 0, None
        for i in range(m.end() - 1, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            continue
        body = text[m.end():end]
        repl = "(%s)" % lit if lit.startswith("-") else lit
        pat = re.compile(r"(?<![\w.>])%s(?![\w\s]*(?:->|\.)|\s*(?:=[^=]|\+\+|--))"
                         % re.escape(expr).replace(r"\-\>", r"\s*->\s*").replace(r"\.", r"\s*\.\s*"))
        for n, hit in enumerate(list(pat.finditer(body))):
            if body[max(0, hit.start() - 1)] == "&":
                continue
            nb = body[:hit.start()] + repl + body[hit.end():]
            out.append(("litarm@%s@%d" % (expr, n),
                        text[:m.end()] + nb + text[end:]))
    return out


def r29_field_reread(text):
    """`T x = o->f;` … `use(x)` -> keep the local but read `o->f` again at ONE use site.

    The mirror of `r22`: that rule removes a temporary to shorten a live range, this one lengthens
    the ROM's by reproducing a redundant `ldr` the clean C never emits. Only a plain field load is
    re-read, and only while nothing between the declaration and that use writes the object, so the
    two reads are the same value (`020307d0`).
    """
    out = []
    lines = text.split("\n")
    # The binding is as often a plain assignment to an already-declared local as a declaration with
    # an initialiser -- 020307d0 declares `unsigned char* cursor;` at the top of the function and
    # only assigns it later -- and a decl-only rule fires on neither half of that shape.
    decl = re.compile(r"^(\s*)((?:unsigned |signed |const |struct )*[A-Za-z_]\w*(?:\s*\*)*\s+)?"
                      r"([A-Za-z_]\w*)\s*=\s*((?:[A-Za-z_]\w*)(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)+)\s*;\s*$")
    for i, ln in enumerate(lines):
        m = decl.match(ln)
        if not m or ln.lstrip().startswith("//"):
            continue
        name, field = m.group(3), m.group(4)
        obj = re.match(r"[A-Za-z_]\w*", field).group(0)
        member = field.split(">")[-1].split(".")[-1].strip()
        word = re.compile(r"(?<![.>\w])%s\b(?!\s*(?:=[^=]|\+\+|--))" % re.escape(name))
        for j in range(i + 1, len(lines)):
            code = _strip_code(lines[j])
            if re.search(r"\b%s\b\s*(?:=[^=]|\+\+|--)" % re.escape(obj), code):
                break
            if re.search(r"%s\s*(?:=[^=]|\+\+|--)" % re.escape(field), code):
                break
            if re.search(r"(?:->|\.)\s*%s\s*(?:=[^=]|\+\+|--)" % re.escape(member), code):
                break
            if re.search(r"\b%s\b\s*(?:=[^=]|\+\+|--)" % re.escape(name), code):
                break
            if not word.search(lines[j]):
                continue
            new = list(lines)
            new[j] = word.sub(field, lines[j], count=1)
            out.append(("reread:%s@%d" % (name, j), "\n".join(new)))
    return out


def r30_cse_repeat_expr(text):
    """Two textually identical `&expr` occurrences -> ONE named local, reused at both.

    Written twice, mwcc re-forms the address at each site and neither copy stays live; bound once
    and reused, the offset is kept in a callee-saved register across everything in between, which
    is the ROM shape whenever the same object is touched at both ends of a long block (`02098a84`).
    """
    out = []
    lines = text.split("\n")
    body_from = max(text.find("// USA:"), 0)
    exprs = {}
    for m in re.finditer(r"&\s*([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*|\[[^\[\]]+\])+)",
                         text[body_from:]):
        exprs[m.group(0)] = exprs.get(m.group(0), 0) + 1
    for expr, n in sorted(exprs.items()):
        if n < 2 or "(" in expr:
            continue
        hits = [i for i, ln in enumerate(lines) if expr in ln and not ln.lstrip().startswith("//")]
        if len(hits) < 2:
            continue
        base = re.match(r"&\s*([A-Za-z_]\w*)", expr).group(1)
        # The base is as often a PARAMETER as a local, so read the signature too: 02098a84's `arr`
        # is `struct S02098834* arr` in the header and nothing in the body declares it.
        d = re.search(r"(?:[(,]|^)\s*((?:const |struct |unsigned |signed )*[A-Za-z_]\w*)\s*\*?\s*"
                      r"%s\s*(?:[,)]|\[|;|=)" % re.escape(base), text, re.M)
        if not d:
            continue
        typ = d.group(1)
        name = "_cse_%s" % re.sub(r"\W", "", expr)[:24]
        indent = re.match(r"\s*", lines[hits[0]]).group(0)
        new = [ln.replace(expr, name) if i in hits else ln for i, ln in enumerate(lines)]
        new.insert(hits[0], "%s%s* %s = %s;" % (indent, typ, name, expr))
        out.append(("cse:%s" % expr.strip(), "\n".join(new)))
    return out


def r31_hoist_loop_bound(text):
    """`for (i = 0; i < a - K; i++)` -> `int b = a - K;` before the loop, `i < b` inside it.

    A compound bound folded into the condition is computed where the condition is, so it lands
    AFTER the loop's own inits; bound to a named local ahead of the `for`, it is computed first and
    the init `mov`s follow it, which is the order the ROM has (`020e2110`).
    """
    out = []
    lines = text.split("\n")
    forpat = re.compile(r"^(\s*)for\s*\(([^;]*);([^;]*?)([<>]=?|!=)\s*([^;]+?)\s*;([^)]*)\)\s*\{?\s*$")
    for i, ln in enumerate(lines):
        m = forpat.match(ln)
        if not m:
            continue
        bound = m.group(5).strip()
        if not re.search(r"[-+*/]", bound) or bound.count("(") != bound.count(")"):
            continue
        names = set(re.findall(r"[A-Za-z_]\w*", bound))
        depth, moved = 0, False
        for k in range(i, len(lines)):
            depth += lines[k].count("{") - lines[k].count("}")
            if k > i and any(re.search(r"\b%s\b\s*(?:=[^=]|\+\+|--)" % re.escape(n), lines[k])
                             for n in names):
                moved = True
            if k > i and depth <= 0:
                break
        if moved:
            continue
        # The whole bound, and -- when it is `(A) op B` -- just the parenthesised part, which is the
        # form the ROM has on 020e2110: `int n = width - 9;` with the `>> 3` left in the condition.
        parts = [bound]
        inner = re.match(r"^\((.+)\)\s*(?:>>|<<|[-+*/&|^])\s*\S+$", bound)
        if inner:
            parts.append(inner.group(1).strip())
        for pi, piece in enumerate(parts):
            name = "_bound%d" % i
            rest = bound if piece == bound else bound.replace("(%s)" % piece, name, 1)
            new = list(lines)
            new[i] = "%sfor (%s;%s%s %s;%s)%s" % (m.group(1), m.group(2), m.group(3), m.group(4),
                                                  name if piece == bound else rest, m.group(6),
                                                  " {" if ln.rstrip().endswith("{") else "")
            new.insert(i, "%sint %s = %s;" % (m.group(1), name, piece))
            out.append(("loopbound@%d.%d" % (i, pi), "\n".join(new)))
    return out


def r32_sink_store_into_arms(text):
    """`v = A;` / `v = B;` in an if/else then one `o->f = v;` -> the store itself in both arms.

    A named local parked between the two assignments and the store keeps the value in its own
    register; storing directly from each arm lets mwcc keep the whole sequence in one register and
    predicate it, which is what removes the extra `mov` (`02012538`). The inverse of `r20`.
    """
    out = []
    lines = text.split("\n")
    depth = _line_depths(lines)
    assign = re.compile(r"^(\s*)([A-Za-z_]\w*)\s*=\s*([^;=][^;]*);\s*$")
    store = re.compile(r"^(\s*)([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*|\[[^\[\]]+\])+)\s*=\s*"
                       r"([A-Za-z_]\w*)\s*;\s*$")
    decl = re.compile(r"^\s*(?:unsigned |signed |const )*(?:int|char|short|long|bool)\s+"
                      r"([A-Za-z_]\w*)\s*(?:=[^;]*)?;\s*$")

    starts = []
    _p = 0
    for ln in lines:
        starts.append(_p)
        _p += len(ln) + 1

    def _arm_end(k):
        """Line holding the `}` that closes the LAST `{` on line k -- counted in characters, since
        `} else {` is brace-balanced and a line-counting scan walks straight past it."""
        open_at = starts[k] + lines[k].rfind("{")
        if lines[k].rfind("{") < 0:
            return -1
        d = 0
        for n in range(open_at, len(text)):
            if text[n] == "{":
                d += 1
            elif text[n] == "}":
                d -= 1
                if d == 0:
                    return sum(1 for c in text[:n] if c == "\n")
        return -1

    for i, ln in enumerate(lines):
        if not re.match(r"^\s*if\s*\(.*\)\s*\{\s*$", ln):
            continue
        then_end = _arm_end(i)
        if then_end < 0 or not re.match(r"^\s*\}\s*else\s*\{\s*$", lines[then_end]):
            continue
        else_end = _arm_end(then_end)
        if else_end < 0:
            continue
        # A local assigned in BOTH arms -- at the arm's own depth, so a nested if does not count --
        # and read exactly once afterwards, in a plain store. Anchoring on the line right after the
        # brace missed 02012538, whose then-arm writes a field first and the flag second.
        def _writes_in(lo, hi, arm_depth=depth[i] + 1):
            got = {}
            for k in range(lo + 1, hi):
                a = assign.match(lines[k])
                if a and depth[k] == arm_depth:
                    got[a.group(2)] = k
            return got
        thens, elses = _writes_in(i, then_end), _writes_in(then_end, else_end)
        for var in sorted(set(thens) & set(elses)):
            if not any(decl.match(x) and decl.match(x).group(1) == var for x in lines[:i]):
                continue
            after = [k for k in range(else_end + 1, len(lines))
                     if re.search(r"(?<![.>\w])%s\b" % re.escape(var), _strip_code(lines[k]))]
            if not after:
                continue
            s = store.match(lines[after[0]])
            if not s or s.group(3) != var or len(after) > 1:
                continue
            new = list(lines)
            for k in (thens[var], elses[var]):
                new[k] = "%s%s = %s;" % (assign.match(lines[k]).group(1), s.group(2),
                                         assign.match(lines[k]).group(3))
            del new[after[0]]
            dead = [k for k in range(len(new))
                    if decl.match(new[k]) and decl.match(new[k]).group(1) == var
                    and not re.search(r"=", new[k])]
            for k in reversed(dead):
                del new[k]
            out.append(("sinkstore:%s" % var, "\n".join(new)))
    return out


def r33_field_signedness(text):
    """Flip one struct FIELD between signed and unsigned.

    The field's signedness picks the SHIFT and the CONDITION mnemonics -- `asr` against `lsr` on a
    right shift, `blt`/`bge` against `blo`/`bhs` on a compare -- so a diff whose only wrong rows are
    those pairs is a type declaration, not a colouring (`020c0a40`). Only fields the body actually
    names are flipped, so a wide struct does not flood the neighbourhood.
    """
    out = []
    lines = text.split("\n")
    body_from = max(text.find("// USA:"), 0)
    body = text[body_from:]
    fld = re.compile(r"^(\s*)(unsigned |signed )?(int|short|char|long)\s+([A-Za-z_]\w*)\s*(\[[^\]]*\])?;\s*$")
    depth = 0
    for i, ln in enumerate(lines):
        opened = depth
        depth += ln.count("{") - ln.count("}")
        if opened == 0:
            continue
        m = fld.match(ln)
        if not m:
            continue
        name = m.group(4)
        if not re.search(r"(?:->|\.)\s*%s\b" % re.escape(name), body):
            continue
        flipped = "int" if m.group(3) == "int" and m.group(2) else m.group(3)
        pre = "" if m.group(2) else "unsigned "
        new = list(lines)
        new[i] = "%s%s%s %s%s;" % (m.group(1), pre, flipped, name, m.group(5) or "")
        out.append(("signflip:%s" % name, "\n".join(new)))
    return out


def r34_call_move_earlier(text):
    """Move ONE call statement earlier inside its block, past calls it does not feed.

    `r16` refuses anything containing a call, because a call can touch memory -- but WHICH call runs
    first is exactly what decides which local owns the callee-saved register for the rest of the
    block, and the ROM often computes a pointer before a dispatch that the clean translation writes
    after it (`02037d88`). Kept honest by the gate: a reordering that changes the program cannot
    come out byte-identical to the ROM.

    Conservative: straight-line code at one depth, and the moved statement may not cross a write to
    any name it reads or a read of the name it writes.
    """
    out = []
    lines = text.split("\n")
    depth = _line_depths(lines)
    idx = [i for i in _body_lines(text)
           if lines[i].strip().endswith(";") and "(" in lines[i]
           and not lines[i].strip().startswith(("return", "}", "{", "case", "goto", "break",
                                                "continue", "extern", "#", "for", "while", "if",
                                                "switch", "do"))]
    for pos, i in enumerate(idx):
        src = lines[i]
        if _is_unbraced_body(lines, i):
            continue
        w = _writes(src)
        reads = _reads(src)
        for j in idx[:pos]:
            if _is_unbraced_body(lines, j):
                continue
            span = range(j, i + 1)
            # The move may cross a whole nested BLOCK, as long as it never leaves the one it is in:
            # 02037d88's lever hops a braced if/dispatch, which a same-depth-every-line test refuses.
            if depth[j] != depth[i] or any(depth[k] < depth[i] for k in span):
                continue
            # Only moves that HOP A NESTED BLOCK. Shuffling two adjacent calls is what r5 and r16
            # already search, and 55 such neighbours pushed 02037d88's answer to rank 56 of 184 --
            # past any budget a pool sweep gives a 944-byte function.
            if not any(depth[k] > depth[i] for k in span):
                continue
            crossed = [_strip_code(lines[k]) for k in span if k != i]
            if any((w and (w in _reads(c) or _writes(c) == w)) or
                   (_writes(c) and _writes(c) in reads) for c in crossed):
                continue
            rest = [ln for k, ln in enumerate(lines) if k != i]
            # Rank by how many statements AT THIS DEPTH the move hops, not by line distance: a
            # braced dispatch is dozens of lines and two statements, and 02037d88's answer is the
            # second-nearest target. Sorted, it costs a couple of compiles; unsorted it is 184.
            hops = sum(1 for k in idx if j <= k < i and depth[k] == depth[i])
            if hops > 3:
                continue
            out.append((hops, "callmove:%d->%d" % (i, j), "\n".join(rest[:j] + [src] + rest[j:])))
    return [(label, cand) for _h, label, cand in sorted(out, key=lambda t: t[0])]


def r35_chain_const_stores(text):
    """Two adjacent stores of the SAME constant -> one chained assignment, in both directions.

    `a->x = 0; a->y = 0;` makes mwcc save the destination pointer into its callee-saved register
    BEFORE materialising the constant; `a->x = a->y = 0;` reverses that, which is the order the ROM
    uses (`ov015:0218ee38`). The chain also fixes the STORE order -- `A = B = k` stores B first --
    so both directions are emitted and the gate picks the one that matches.
    """
    out = []
    lines = text.split("\n")
    store = re.compile(r"^(\s*)([^=;]+?)\s*=\s*(-?(?:0[xX][0-9a-fA-F]+|\d+))\s*;\s*$")
    decl = re.compile(r"^\s*(?:unsigned |signed |const |volatile |struct |static )*"
                      r"(?:int|char|short|long|bool|float|double|\w+_t)\b\s*\**\s*\w+\s*$")

    def is_store(m):
        """A memory store, never a declaration: chaining `int x = 0; int y = 0;` is not even valid."""
        lv = m.group(2)
        return (not decl.match(lv)) and (("->" in lv) or ("." in lv) or ("[" in lv)
                                         or lv.lstrip().startswith("*"))

    for i in range(len(lines) - 1):
        a, b = store.match(lines[i]), store.match(lines[i + 1])
        if not (a and b) or not (is_store(a) and is_store(b)):
            continue
        if a.group(1) != b.group(1) or a.group(3) != b.group(3):
            continue
        if a.group(2).strip() == b.group(2).strip():
            continue
        for first, second in ((a, b), (b, a)):
            new = list(lines)
            new[i] = "%s%s = %s = %s;" % (a.group(1), first.group(2).strip(),
                                          second.group(2).strip(), a.group(3))
            del new[i + 1]
            out.append(("chainconst:%d:%s" % (i, first.group(2).strip()), "\n".join(new)))
    return out


_EXTERN_DECL = re.compile(
    r"^(\s*extern\s+)(?!volatile\b)((?:const\s+|unsigned\s+|signed\s+|struct\s+|long\s+|short\s+)*"
    r"[A-Za-z_]\w*(?:\s*\*)*\s+)([A-Za-z_]\w*)(\s*(?:\[[^\]]*\])?\s*;)\s*$")


def r36_volatile_extern(text):
    """Qualify an extern global volatile so its load cannot be hoisted past an independent access.

    mwcc reorders a global read ahead of a preceding unrelated store, which shows up as a SCHED
    residue -- the same instructions in the wrong order -- that no register rewrite can reach.
    `volatile` on the declaration pins it. The ROM's shape often needs SEVERAL of a function's
    globals pinned at once (020a1bb4 needed three), so the whole set is offered as well as each
    one, and the all-at-once variant comes first because that is the one that landed twice.
    """
    lines = text.split("\n")
    hits = [i for i, ln in enumerate(lines) if _EXTERN_DECL.match(ln)]
    if not hits:
        return []

    def qualify(idx):
        m = _EXTERN_DECL.match(lines[idx])
        return "%svolatile %s%s%s" % (m.group(1), m.group(2), m.group(3), m.group(4))

    out = []
    if len(hits) > 1:
        new = list(lines)
        for i in hits:
            new[i] = qualify(i)
        out.append(("volextern:all", "\n".join(new)))
    for i in hits:
        new = list(lines)
        new[i] = qualify(i)
        out.append(("volextern:%s" % _EXTERN_DECL.match(lines[i]).group(3), "\n".join(new)))
    return out


def _assign_at(code):
    """Index just past the statement's assignment operator, or None if it does not assign."""
    depth = 0
    for k, ch in enumerate(code):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "=" and depth == 0:
            if code[k - 1:k] in ("=", "!", "<", ">", "+", "-", "*", "/", "&", "|", "^", "%"):
                continue
            if code[k + 1:k + 2] == "=":
                continue
            return k + 1
    return None


_PTR_DECL = re.compile(
    r"^(\s*)((?:const\s+|unsigned\s+|signed\s+|struct\s+)*[A-Za-z_]\w*)\s*\*\s*"
    r"([A-Za-z_]\w*)\s*(=[^;]*)?;\s*$")


def r37_volatile_alias(text):
    """Read a pointer's fields through a `const volatile` alias so two loads keep source order.

    mwcc is free to swap two independent loads off one object, and does; the ROM's order comes back
    when they go through a volatile-qualified alias, which forbids reordering them against each
    other (020cdfd8, where a plain cast was inert and only `const volatile` moved the bytes).
    Read sites only -- the alias is const, so a function that stores through the pointer keeps its
    stores on the original name.
    """
    lines = text.split("\n")
    depth = _line_depths(lines)
    out = []
    # PARAMETERS COUNT, and leaving them out meant the rule could not reproduce the match it was
    # derived from: 020cdfd8's alias is on `src`, a parameter. The signature itself is never touched
    # -- the alias is a new statement at the top of the body -- so the mangled name cannot move.
    sites = [(i, _PTR_DECL.match(ln)) for i, ln in enumerate(lines)]
    sites = [(i, m.group(1), m.group(2).replace("const ", "").strip(), m.group(3), i)
             for i, m in sites if m and depth[i] >= 1
             and not lines[i].lstrip().startswith(("//", "return"))]
    for i, ln in enumerate(lines):
        if depth[i] != 0 or not _strip_code(ln).rstrip().endswith("{") or "(" not in ln:
            continue
        sig = ln[:ln.rindex("{")]
        j = i
        while "(" not in sig and j > 0:
            j -= 1
            sig = lines[j] + sig
        if "(" not in sig or ")" not in sig:
            continue
        params = sig[sig.index("(") + 1:sig.rindex(")")]
        for part in params.split(","):
            pm = re.match(r"\s*((?:const\s+|unsigned\s+|signed\s+|struct\s+)*[A-Za-z_]\w*)"
                          r"\s*\*\s*([A-Za-z_]\w*)\s*$", part)
            if pm:
                sites.append((i, "    ", pm.group(1).replace("const ", "").strip(),
                              pm.group(2), i))
    for _o, indent, ctype, name, anchor in sorted(sites):
        i = anchor
        if ctype in _TYPEWORDS and ctype not in ("char", "int", "short", "long"):
            continue
        arrow = re.compile(r"(?<![.>\w])%s\s*->" % re.escape(name))
        reads = []
        for j in range(i + 1, len(lines)):
            code = _strip_code(lines[j])
            if not arrow.search(code):
                continue
            at = _assign_at(code)
            if at is None or arrow.search(code[at:]):
                reads.append(j)
        if len(reads) < 2:
            continue
        if any(re.search(r"\b%s\b\s*(?:=[^=]|\+\+|--)" % re.escape(name), _strip_code(lines[j]))
               for j in range(i + 1, reads[-1])):
            continue
        alias = "%s_v" % name
        if re.search(r"\b%s\b" % re.escape(alias), text):
            continue
        new = list(lines)
        for j in reads:
            at = _assign_at(_strip_code(new[j]))
            if at is None:
                new[j] = arrow.sub("%s->" % alias, new[j])
            else:
                new[j] = new[j][:at] + arrow.sub("%s->" % alias, new[j][at:])
        new.insert(i + 1, "%sconst volatile %s* %s = %s;" % (indent, ctype, alias, name))
        out.append(("volalias:%s" % name, "\n".join(new)))
    return out


def _brace_block(lines, i):
    """(first body line, closing-brace line) for a header at line i that opens a brace, else None."""
    if not _strip_code(lines[i]).rstrip().endswith("{"):
        return None
    depth = 0
    for j in range(i, len(lines)):
        code = _strip_code(lines[j])
        depth += code.count("{") - code.count("}")
        if depth == 0 and j > i:
            return (i + 1, j)
    return None


_WHILE_HEAD = re.compile(r"^(\s*)while\s*\((.+)\)\s*\{\s*$")
_FOR_HEAD = re.compile(r"^(\s*)for\s*\(([^;]*);([^;]*);([^;]*)\)\s*\{\s*$")


def r38_guarded_do_while(text):
    """`while (c) {B}` -> `if (c) { do {B} while (c); }`, and the same for a `for`.

    mwcc does not rotate loops: a `while` emits a branch to a bottom test and will never become the
    ROM's top-tested do-while, so the form has to be written directly. This is the single most
    productive loop edit measured -- three zero-fill loops rewritten by hand moved `020c0a40` from
    0x1f0 to 0x1fc -- and it was recipe-only, so the free sweep could never apply it. A `for` with
    `continue` is refused: the increment moves into the body and `continue` would skip it.
    """
    out = []
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        mw = _WHILE_HEAD.match(ln)
        mf = None if mw else _FOR_HEAD.match(ln)
        if not mw and not mf:
            continue
        span = _brace_block(lines, i)
        if not span:
            continue
        first, close = span
        body = lines[first:close]
        if any(re.match(r"^\s*(do|while|for)\b", _strip_code(b)) for b in body):
            continue
        indent = (mw or mf).group(1)
        if mw:
            cond, init, step = mw.group(2).strip(), None, None
        else:
            init, cond, step = (mf.group(2).strip(), mf.group(3).strip(), mf.group(4).strip())
            if not cond or not step:
                continue
            if any(re.search(r"\bcontinue\b", _strip_code(b)) for b in body):
                continue
        new = lines[:i]
        if init:
            new.append("%s%s;" % (indent, init))
        new.append("%sif (%s) {" % (indent, cond))
        new.append("%s    do {" % indent)
        new.extend("    " + b for b in body)
        if step:
            new.append("%s        %s;" % (indent, step))
        new.append("%s    } while (%s);" % (indent, cond))
        new.append("%s}" % indent)
        new.extend(lines[close + 1:])
        out.append(("dowhile@%d" % i, "\n".join(new)))
    return out


# The declaration half must end in whitespace or a `*`, or it eats the first character of the name:
# `entries = ...` matched decl="e" name="ntries" and emitted `ntries += 0x10;`.
_PTR_ADD = re.compile(r"^(\s*)((?:[A-Za-z_]\w*[\s\*]+)*)([A-Za-z_]\w*)\s*=\s*"
                      r"(\([^)]*\*\s*\)\s*)?([A-Za-z_]\w*)\s*\+\s*(0x[0-9a-fA-F]+|\d+)\s*;\s*$")


def r39_split_pointer_add(text):
    """`p = (T*)base + K;` -> `p = (T*)base; p += K;` so the `add` is not hoisted with the bind.

    mwcc hoists a loop-invariant address computation ahead of the neighbouring loads; the ROM
    computes it among them. Splitting the bind from the advance puts the `add` back where the ROM
    has it (`0202b900`, SCHED 14 -> byte-exact). The reverse fold is offered too, for the case where
    ours emits the `add` late.
    """
    out = []
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        m = _PTR_ADD.match(ln)
        if not m or _line_depths(lines)[i] < 1:
            continue
        indent, decl, name, cast, base, off = m.groups()
        new = list(lines)
        new[i] = "%s%s%s = %s%s;" % (indent, decl or "", name, cast or "", base)
        new.insert(i + 1, "%s%s += %s;" % (indent, name, off))
        out.append(("ptrsplit:%s" % name, "\n".join(new)))
    return out


# The call must not contain parentheses of its own, or the capture runs past its closing paren and
# takes the `if`'s: `GetField(bs)` became `GetField(bs))` and the test lost its own bracket.
# The tail is allowed to be EMPTY, because the condition this rule was derived from continues on the
# NEXT line -- requiring `)` or `&&` here made the rule fire on that function's sibling sites (which
# must keep the inline form) and never on the site that actually needed it.
_CMP_CALL = re.compile(r"^(\s*)(\}?\s*(?:else\s+)?if\s*\()([A-Za-z_]\w*)\s*(==|!=)\s*"
                       r"([A-Za-z_]\w*\s*\([^()]*\))(.*)$")
_CMP_TAIL_OK = re.compile(r"^\s*(?:\)|&&|\|\|)?\s*[){]*\s*$")


def r40_bind_call_operand(text):
    """`if (x == Call())` -> `T t = Call(); if (t == x)`, binding the call above the test.

    A call left inline in a compare always takes Rm: mwcc canonicalises it, and it does so for BOTH
    source orders, so swapping the operands in place changes nothing. Naming the result erases its
    call-ness and restores left-to-right placement (`02053634`, 2 bytes). The bind is inserted
    directly above the test, which is BELOW the other operand's own definition -- above it regresses.
    """
    out = []
    lines = text.split("\n")
    depth = _line_depths(lines)
    for i, ln in enumerate(lines):
        m = _CMP_CALL.match(ln)
        if not m or depth[i] < 1:
            continue
        indent, head, var, op, call, rest = m.groups()
        # Anything else after the call -- `+ 1`, a cast, another term -- means the rewrite would
        # change what is compared, not just where it materialises.
        if not _CMP_TAIL_OK.match(rest):
            continue
        tmp = "_c%d" % i
        if re.search(r"\b%s\b" % tmp, text):
            continue
        new = list(lines)
        new[i] = "%s%s%s %s %s%s" % (indent, head, tmp, op, var, rest)
        new.insert(i, "%sint %s = %s;" % (indent, tmp, call))
        out.append(("bindcall:%d" % i, "\n".join(new)))
    return out


_ZERO_CMP = re.compile(r"(?<![\w>])([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*)\s*(>=|<)\s*0\b")
_MINUS_ONE_CMP = re.compile(
    r"(?<![\w>])([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*)\s*(>|<=)\s*-\s*1\b")


def r41_nonfoldable_constant(text):
    """`x >= 0` <-> `x > -1` (and `x < 0` <-> `x <= -1`): same test, one instruction apart.

    A comparison against 0 folds into a `cmp` immediate; against -1 it cannot, so mwcc materialises
    `mvn rN,#0` first. When the ROM has the extra instruction, the constant is the whole difference
    (`0209c840`).

    BOTH directions are generated. The rule shipped one-way, which is the direction we need least:
    the sweep hill-climbs from OUR source toward the ROM, so the common case is our source carrying
    the `mvn` the ROM does not have. `main:02081f20` sat at 23 bytes for that alone and no rule could
    remove it.
    """
    out = []
    lines = text.split("\n")
    depth = _line_depths(lines)
    for i, ln in enumerate(lines):
        if depth[i] < 1 or ln.lstrip().startswith(("//", "#")):
            continue
        for m in _ZERO_CMP.finditer(_strip_code(ln)):
            rep = "%s %s -1" % (m.group(1), ">" if m.group(2) == ">=" else "<=")
            new = list(lines)
            new[i] = ln[:m.start()] + rep + ln[m.end():]
            out.append(("nofold:%d" % i, "\n".join(new)))
        for m in _MINUS_ONE_CMP.finditer(_strip_code(ln)):
            rep = "%s %s 0" % (m.group(1), ">=" if m.group(2) == ">" else "<")
            new = list(lines)
            new[i] = ln[:m.start()] + rep + ln[m.end():]
            out.append(("fold:%d" % i, "\n".join(new)))
    return out


def r42_drop_noop_case(text):
    """Delete a `case K:` whose whole body is `return;`, letting K fall through unhandled.

    An explicit no-op arm gives mwcc a real branch target and a detached copy of the epilogue;
    deleting it lets the shared one-instruction epilogue inline into the jump-table slot, which is
    what the ROM has (`02076df4`, 12 bytes with the if/else merge). Only a bare `return;` is
    removed -- anything else in the arm changes behaviour.
    """
    out = []
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if not re.match(r"^\s*case\s+[^:]+:\s*$", _strip_code(ln)):
            continue
        j = i + 1
        while j < len(lines) and not _strip_code(lines[j]).strip():
            j += 1
        if j >= len(lines) or _strip_code(lines[j]).strip() != "return;":
            continue
        new = [l for k, l in enumerate(lines) if k not in (i, j)]
        out.append(("nocase:%d" % i, "\n".join(new)))
    return out


_MEMBER_READ = re.compile(r"(?<![\w)\]])([A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)+)")
_ASSIGN_OP = re.compile(r"(?<![=!<>+\-*/&|^%])=(?!=)")
_CAST_STORE = re.compile(
    r"^(\s*)\*\(\s*((?:unsigned|signed|const)?\s*[A-Za-z_]\w*)\s*\*\s*\)\s*"
    r"(.+?)\s*(&|\||\^|\+|-|<<|>>)?=\s*([^;]+);\s*$")


def r43_short_cast_in_add(text):
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith(("//", "#", "*")) or "+" not in ln:
            continue
        if "*" not in ln.split("+", 1)[1] and "<<" not in ln:
            continue
        a = _ASSIGN_OP.search(ln)
        start = a.end() if a else 0
        for m in _MEMBER_READ.finditer(ln, start):
            expr = m.group(1)
            if "(short)" + expr in ln:
                continue
            new = list(lines)
            new[i] = ln[:m.start(1)] + "(short)" + expr + ln[m.end(1):]
            out.append(("shortcast:%s@%d" % (expr, i), "\n".join(new)))
    return out


def r44_volatile_split_store(text):
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        m = _CAST_STORE.match(ln)
        if not m:
            continue
        indent, ctype, addr, op, rhs = m.groups()
        ctype = " ".join(ctype.split())
        if "volatile" in ln:
            continue
        vol = "%s*(volatile %s*)%s = " % (indent, ctype, addr)
        new = list(lines)
        new[i] = vol + ("*(%s*)%s %s %s;" % (ctype, addr, op, rhs) if op else "%s;" % rhs)
        out.append(("volsplit@%d" % i, "\n".join(new)))
    return out


_MASK_OR = re.compile(
    r"^(\s*)([A-Za-z_][\w.\->\[\]]*)\s*=\s*\(\s*\2\s*&\s*([^()]+|\([^()]*\))\s*\)\s*\|\s*([^;]+);\s*$")
_OR_MASK = re.compile(
    r"^(\s*)([A-Za-z_][\w.\->\[\]]*)\s*=\s*([^;]+?)\s*\|\s*\(\s*\2\s*&\s*([^()]+|\([^()]*\))\s*\);\s*$")


def r45_accumulate_or(text):
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        m = _MASK_OR.match(ln)
        if m:
            indent, lhs, mask, val = m.groups()
        else:
            m = _OR_MASK.match(ln)
            if not m:
                continue
            indent, lhs, val, mask = m.groups()
        new = list(lines)
        new[i] = ("%s{ unsigned int _acc%d = %s; %s &= %s; %s |= _acc%d; }"
                  % (indent, i, val.strip(), lhs, mask.strip(), lhs, i))
        out.append(("accumor@%d" % i, "\n".join(new)))
    return out


_R46_DECL_INT = re.compile(r"^\s+int\s+([A-Za-z_]\w*)\s*;\s*$")
_R46_FOR_INT = re.compile(r"for\s*\(\s*int\s+([A-Za-z_]\w*)\s*=")


def _r46_body(lines):
    for i, ln in enumerate(lines):
        if not re.search(r"\b(?:ARM|THUMB)\b.*\(", ln):
            continue
        if ln.rstrip().endswith("{"):
            return i + 1
        if ln.rstrip().endswith(")") and i + 1 < len(lines) and lines[i + 1].strip() == "{":
            return i + 2
    return None


def _r46_leading(lines, start):
    end = start
    while end < len(lines):
        s = lines[end].strip()
        if s.startswith(("if", "for", "while", "switch", "do", "}")) or s.endswith("{"):
            break
        end += 1
    return end


def r46_counter_position(text):
    lines = text.split("\n")
    start = _r46_body(lines)
    if start is None:
        return []
    end = _r46_leading(lines, start)
    out = []
    counters = []
    for i in range(start, end):
        m = _R46_DECL_INT.match(lines[i])
        if m and re.search(r"for\s*\(\s*%s\s*=" % re.escape(m.group(1)), text):
            counters.append(i)
    if counters:
        block = [lines[i] for i in counters]
        drop = set(counters)
        rest = [ln for k, ln in enumerate(lines) if k not in drop]
        for pos in range(start, end - len(counters) + 1):
            new = rest[:pos] + block + rest[pos:]
            if new != lines:
                out.append(("ctrblock@%d" % pos, "\n".join(new)))
    for name in sorted(set(_R46_FOR_INT.findall(text))):
        base = re.sub(r"for\s*\(\s*int\s+%s\s*=" % re.escape(name), "for (%s =" % name, text, count=1)
        bl = base.split("\n")
        bend = _r46_leading(bl, start)
        indent = re.match(r"^(\s*)", bl[start]).group(1) or "    "
        for pos in range(start, bend + 1):
            new = bl[:pos] + ["%sint %s;" % (indent, name)] + bl[pos:]
            out.append(("hoist:%s@%d" % (name, pos), "\n".join(new)))
    return out


_R47_IF_ASSIGN = re.compile(
    r"^(\s*)if\s*\((.*)\)\s*([A-Za-z_][\w.\->\[\]]*)\s*=\s*([^;=][^;]*);\s*$")


def _r47_top_and_splits(cond):
    depth, out = 0, []
    for k, ch in enumerate(cond):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and cond.startswith(" && ", k):
            out.append(k)
    return out


def r47_ternary_store(text):
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        m = _R47_IF_ASSIGN.match(ln)
        if not m:
            continue
        indent, cond, lhs, rhs = m.groups()
        for k in _r47_top_and_splits(cond):
            a, b = cond[:k].strip(), cond[k + 4:].strip()
            new = list(lines)
            new[i] = "%sif (%s) %s = %s ? %s : %s;" % (indent, a, lhs, b, rhs.strip(), lhs)
            out.append(("ternstore@%d.%d" % (i, k), "\n".join(new)))
    return out


_R48_DECL = re.compile(
    r"^(\s*)((?:struct\s+)?[A-Za-z_]\w*)\s*\*\s*([A-Za-z_]\w*)\s*=\s*&\s*([A-Za-z_]\w*)\s*->\s*"
    r"[A-Za-z_]\w*\s*\[\s*([^\]]+?)\s*\]\s*;\s*$")


def r48_inline_index(text):
    lines = text.split("\n")
    start = _r46_body(lines)
    if start is None:
        return []
    sig = start - 1
    at = sig - 1 if sig > 0 and lines[sig - 1].lstrip().startswith("//") else sig
    out = []
    for i, ln in enumerate(lines):
        if i < start:
            continue
        m = _R48_DECL.match(ln)
        if not m:
            continue
        indent, ty, name, base, idx = m.groups()
        helper = "_AtR48_%d" % i
        new = list(lines)
        new[i] = "%s%s* %s = %s(%s, %s);" % (indent, ty, name, helper, base, idx)
        new.insert(at, "static inline %s* %s(void* o, int n) { return (%s*)o + n; }" % (ty, helper, ty))
        out.append(("inlidx:%s@%d" % (name, i), "\n".join(new)))
    return out


_R49_FOR_HEAD = re.compile(r"^(\s*)for\s*\(.*\)\s*\{\s*$")
_R49_ASSIGN = re.compile(r"^(\s*)([A-Za-z_][\w.\->\[\]]*)\s*=\s*[^=;][^;]*;\s*$")


def r49_sink_into_loop(text):
    lines = text.split("\n")
    out = []
    for i in range(1, len(lines)):
        f = _R49_FOR_HEAD.match(lines[i])
        a = _R49_ASSIGN.match(lines[i - 1])
        if not f or not a or a.group(1) != f.group(1):
            continue
        new = lines[:i - 1] + [lines[i], f.group(1) + "    " + lines[i - 1].strip()] + lines[i + 1:]
        out.append(("sinkloop@%d" % i, "\n".join(new)))
    return out


_R50_FLAG_DECL = re.compile(r"^(\s+)int\s+([A-Za-z_]\w*)\s*=\s*[01]\s*;\s*$")


def _r50_is_flag(name, text):
    for m in re.finditer(r"(?<![\w.>])%s\s*(\+\+|--|[-+*/|&^]?=)(?!=)\s*([^;]*);" % re.escape(name),
                         text):
        if m.group(1) != "=" or m.group(2).strip() not in ("0", "1"):
            return False
    return not re.search(r"(\+\+|--)\s*%s\b" % re.escape(name), text)


def r50_narrow_flag(text):
    lines = text.split("\n")
    flags = [(i, m.group(2)) for i, ln in enumerate(lines)
             for m in [_R50_FLAG_DECL.match(ln)] if m and _r50_is_flag(m.group(2), text)]
    out = []
    for i, name in flags:
        new = list(lines)
        new[i] = lines[i].replace("int " + name, "unsigned char " + name, 1)
        out.append(("u8flag:%s@%d" % (name, i), "\n".join(new)))
    if len(flags) > 1:
        new = list(lines)
        for i, name in flags:
            new[i] = lines[i].replace("int " + name, "unsigned char " + name, 1)
        out.insert(0, ("u8flags:all", "\n".join(new)))
    return out


_R51_LOCAL = re.compile(r"^(\s+)int(\s+)([A-Za-z_]\w*)(\s*(?:=[^;,]*)?;\s*)$")
_R51_PROTO = r'(?m)^((?:extern\s+"C"\s+)?)int(\s+%s\s*\([^;{]*\)\s*;)'


def r51_short_spill(text):
    lines = text.split("\n")
    start = _r46_body(lines)
    if start is None:
        return []
    body = "\n".join(lines[start:])
    found = []
    for i in range(start, len(lines)):
        m = _R51_LOCAL.match(lines[i])
        if not m:
            continue
        name = re.escape(m.group(3))
        uses = len(re.findall(r"(?<![\w.>])%s\b" % name, body))
        callees = []
        for a in re.finditer(r"(?<![\w.>])%s\s*=(?!=)([^;]*);" % name, body):
            for f in re.findall(r"\b([A-Za-z_]\w*)\s*\(", a.group(1)):
                if f not in callees and re.search(_R51_PROTO % re.escape(f), text):
                    callees.append(f)
        new = list(lines)
        new[i] = "%sshort%s%s%s" % (m.group(1), m.group(2), m.group(3), m.group(4))
        found.append((-uses, i, m.group(3), "\n".join(new), callees))
    found.sort(key=lambda t: (t[0], t[1]))
    out = []
    for _u, i, name, local, callees in found:
        if callees:
            both = local
            for f in callees:
                both = re.sub(_R51_PROTO % re.escape(f), r"\1short\2", both, count=1)
            out.append(("shortspill+ret:%s@%d" % (name, i), both))
    out += [("shortspill:%s@%d" % (name, i), local) for _u, i, name, local, _c in found]
    return out


_R52_PTR = r"(?:(?:unsigned |signed |const |struct )*[A-Za-z_]\w*\s*\*+)"
_R52_ADDR_ASSIGN = re.compile(r"^(\s*)(?:%s\s*)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*\s*\+\s*(?:0x[0-9a-fA-F]+|\d+))\s*;\s*$"
                              % _R52_PTR)
_R52_WRITE = r"\s*(?:[-+*/|&^]?=(?!=)|\+\+|--)"
_R53_COPY = re.compile(r"^(\s*)((?:unsigned |signed )?(?:int|short|long|char))\s+([A-Za-z_]\w*)\s*=\s*([^;]+?)\s*;\s*$")
_R54_DECL = re.compile(r"^(\s*)((?:struct\s+)?[A-Za-z_]\w*)\s*\*\s*([A-Za-z_]\w*)\s*=\s*"
                       r"\(\s*((?:struct\s+)?[A-Za-z_]\w*)\s*\*\s*\)\s*\((.+)\)\s*\+\s*([^;]+?)\s*;\s*$")


def _r52_word(name):
    return r"(?<![\w.>])%s\b" % re.escape(name)


def _r52_written(name, body):
    lhs = r"(?<![\w.>*])%s\b" % re.escape(name)
    return re.findall(lhs + _R52_WRITE, body) + re.findall(r"(?:\+\+|--)\s*" + lhs, body)


def r52_inline_address_local(text):
    lines = text.split("\n")
    start = _r46_body(lines)
    if start is None:
        return []
    body = "\n".join(lines[start:])
    out = []
    for i in range(start, len(lines)):
        m = _R52_ADDR_ASSIGN.match(lines[i])
        if not m:
            continue
        name, expr = m.group(2), m.group(3)
        base = expr.split("+")[0].strip()
        if len(_r52_written(name, body)) != 1 or _r52_written(base, body):
            continue
        bare = re.compile(r"^\s*%s\s*%s\s*;\s*$" % (_R52_PTR, re.escape(name)))
        new = []
        for j, ln in enumerate(lines):
            if j == i or (j >= start and bare.match(ln)):
                continue
            new.append(re.sub(_r52_word(name), "(%s)" % expr, ln) if j > i else ln)
        plain = "\n".join(new)
        e = re.escape(expr)
        comp = re.sub(r"\*\(%s\)\s*=\s*\*\(%s\)\s*([-+|&^])\s*([^;]+);" % (e, e),
                      lambda k: "*(%s) %s= %s;" % (expr, k.group(1), k.group(2)), plain)
        if comp != plain:
            out.append(("inlineaddr+comp:%s@%d" % (name, i), comp))
        out.append(("inlineaddr:%s@%d" % (name, i), plain))
    return out


def r53_update_in_place(text):
    lines = text.split("\n")
    start = _r46_body(lines)
    if start is None:
        return []
    out = []
    for i in range(start, len(lines)):
        m = _R53_COPY.match(lines[i])
        if not m or not re.match(r"^\*\s*\(|.*(?:->|\.)\w+$", m.group(4)):
            continue
        typ, p, lv = m.group(2), m.group(3), m.group(4)
        if re.search(r"\b(?!int\b|short\b|char\b|long\b|unsigned\b|signed\b)[A-Za-z_]\w*\s*\(", lv):
            continue
        upd = re.compile(r"^(\s*)%s\s+([A-Za-z_]\w*)\s*=\s*%s\s*([-+])\s*([^;]+);\s*$" % (re.escape(typ), re.escape(p)))
        for k in range(i + 1, len(lines)):
            u = upd.match(lines[k])
            if not u:
                continue
            c, op, e = u.group(2), u.group(3), u.group(4)
            store = re.compile(r"^\s*%s\s*=\s*%s\s*;\s*$" % (re.escape(lv), re.escape(c)))
            s = next((j for j in range(k + 1, len(lines)) if store.match(lines[j])), None)
            if s is None:
                break
            new = list(lines)
            new[k] = "%s%s %s= %s;" % (u.group(1), lv, op, e)
            for j in range(k + 1, len(lines)):
                if j != s:
                    new[j] = re.sub(_r52_word(c), lv, new[j])
            del new[s]
            out.append(("inplace:%s@%d" % (c, k), "\n".join(new)))
            rest = "\n".join(new[i + 1:])
            shifts = set(re.findall(r"\(?%s\s*>>\s*(\d+)\)?" % _r52_word(p), rest))
            uses = len(re.findall(_r52_word(p), rest))
            if len(shifts) == 1 and uses == len(re.findall(r"%s\s*>>\s*\d+" % _r52_word(p), rest)):
                sh = shifts.pop()
                fold = list(new)
                fold[i] = "%s%s %s = %s >> %s;" % (m.group(1), typ, p, lv, sh)
                for j in range(i + 1, len(fold)):
                    fold[j] = re.sub(r"\(\s*%s\s*>>\s*%s\s*\)|%s\s*>>\s*%s" % (_r52_word(p), sh, _r52_word(p), sh),
                                     p, fold[j])
                out.insert(len(out) - 1, ("inplace+fold:%s@%d" % (c, k), "\n".join(fold)))
            break
    return out


def r54_two_def_offset(text):
    lines = text.split("\n")
    start = _r46_body(lines)
    if start is None:
        return []
    out = []
    for i in range(start, len(lines)):
        m = _R54_DECL.match(lines[i])
        if not m:
            continue
        ind, typ, name, cast, base, idx = m.groups()
        if typ.split()[-1] != cast.split()[-1] or base.count("(") != base.count(")"):
            continue
        off = next((o for o in ("off", "off2", "off3", "byteOff") if not re.search(_r52_word(o), text)), None)
        if off is None:
            continue
        new = list(lines)
        new[i:i + 1] = ["%sint %s = %s;" % (ind, off, idx),
                        "%s%s *= sizeof(%s);" % (ind, off, typ),
                        "%s%s* %s = (%s*)(%s + %s);" % (ind, typ, name, typ, base, off)]
        out.append(("twodefoff:%s@%d" % (name, i), "\n".join(new)))
    return out


_R55_DECL = re.compile(r"^(\s*)((?:unsigned\s+|signed\s+)?(?:int|short|char|long))\s+([A-Za-z_]\w*)\s*=\s*([^;]+?)\s*;\s*$")
_R55_LOAD = re.compile(r"[A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)*\s*\[[^\[\]()]+\]|[A-Za-z_]\w*\s*->\s*[A-Za-z_]\w*")


def r55_load_then_transform(text):
    lines = text.split("\n")
    start = _r46_body(lines)
    if start is None:
        return []
    out = []
    for i in range(start, len(lines) - 1):
        m1, m2 = _R55_DECL.match(lines[i]), _R55_DECL.match(lines[i + 1])
        if not (m1 and m2):
            continue
        parts = []
        for ind, typ, name, expr in (m1.groups(), m2.groups()):
            loads = _R55_LOAD.findall(expr)
            if len(loads) != 1 or loads[0] == expr or re.search(r"[A-Za-z_]\w*\s*\(", expr):
                break
            parts.append((ind, typ, name, expr, loads[0]))
        if len(parts) != 2:
            continue
        (ia, ta, a, ea, la), (ib, tb, b, eb, lb) = parts
        if re.search(_r52_word(a), eb) or re.search(_r52_word(b), ea):
            continue
        new = list(lines)
        new[i:i + 2] = ["%s%s %s = %s;" % (ia, ta, a, la), "%s%s %s = %s;" % (ib, tb, b, lb),
                        "%s%s = %s;" % (ia, a, ea.replace(la, a, 1)), "%s%s = %s;" % (ib, b, eb.replace(lb, b, 1))]
        out.append(("loadfirst:%s,%s@%d" % (a, b, i), "\n".join(new)))
    return out


_R56_PROTO = re.compile(r'(?m)^(\s*extern\s+"C"\s+[^;{()]*?\b([A-Za-z_]\w*)\s*\()([^;{()]*,[^;{()]*)(\)\s*;)')


def r56_drop_last_arg(text):
    out = []
    for m in _R56_PROTO.finditer(text):
        name = m.group(2)
        calls = list(re.finditer(r"(?<![\w.>])%s\s*\(" % re.escape(name), text[m.end():]))
        if not calls:
            continue
        new = text[:m.start(3)] + m.group(3).rsplit(",", 1)[0] + text[m.end(3):]
        pos, rebuilt, ok = 0, [], True
        for c in re.finditer(r"(?<![\w.>])%s\s*\(" % re.escape(name), new):
            if c.start() < m.start() + len(m.group(1)):
                continue
            depth, j, last_comma = 1, c.end(), None
            while j < len(new) and depth:
                ch = new[j]
                if ch in "([":
                    depth += 1
                elif ch in ")]":
                    depth -= 1
                elif ch == "," and depth == 1:
                    last_comma = j
                j += 1
            if last_comma is None:
                ok = False
                break
            rebuilt.append(new[pos:last_comma])
            pos = j - 1
        if not ok or not rebuilt:
            continue
        rebuilt.append(new[pos:])
        out.append(("droparg:%s" % name, "".join(rebuilt)))
    return out


_R57_CALL = re.compile(r"^(\s*)[A-Za-z_]\w*\s*\([^;]*\)\s*;\s*$")


def r57_call_into_preceding_if(text):
    lines = text.split("\n")
    out = []
    for i in range(1, len(lines) - 1):
        close = lines[i].rstrip()
        if close.strip() != "}" or not _R57_CALL.match(lines[i + 1]):
            continue
        indent = close[:len(close) - 1]
        j = i - 1
        while j >= 0 and not (lines[j].startswith(indent) and lines[j][len(indent):].startswith("if")
                              and lines[j].rstrip().endswith("{")):
            if lines[j].startswith(indent) and lines[j][len(indent):len(indent) + 1] not in (" ", "\t", ""):
                j = -1
            j -= 1
        if j < 0:
            continue
        new = list(lines)
        new[i], new[i + 1] = indent + "    " + lines[i + 1].strip(), lines[i]
        out.append(("callintoif@%d" % (i + 1), "\n".join(new)))
    return out


_R58_DECL = r"^(\s*)((?:unsigned |signed )?(?:int|short|char|long))\s+%s\s*(?:=[^;]*)?;\s*$"


def _r58_loop_end(text, start):
    i = text.index("(", start)
    depth, j = 0, i
    while j < len(text):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if not depth:
                break
        j += 1
    k = j + 1
    while k < len(text) and text[k] in " \t\n":
        k += 1
    if k >= len(text):
        return None
    if text[k] != "{":
        e = text.find(";", k)
        return None if e < 0 else e + 1
    depth = 0
    for m in range(k, len(text)):
        if text[m] == "{":
            depth += 1
        elif text[m] == "}":
            depth -= 1
            if not depth:
                return m + 1
    return None


def r58_split_loop_counter(text):
    out = []
    for name in sorted(set(re.findall(r"\bfor\s*\(\s*([A-Za-z_]\w*)\s*=", text))):
        loops = [m.start() for m in re.finditer(r"\bfor\s*\(\s*%s\s*=" % re.escape(name), text)]
        decl = re.search(_R58_DECL % re.escape(name), text, re.M)
        if len(loops) < 2 or not decl:
            continue
        fresh = name + "2"
        if re.search(r"\b%s\b" % re.escape(fresh), text):
            continue
        for at in loops[1:]:
            end = _r58_loop_end(text, at)
            if end is None:
                continue
            body = re.sub(r"\b%s\b" % re.escape(name), fresh, text[at:end])
            new = text[:at] + body + text[end:]
            line = "%s%s %s;" % (decl.group(1), decl.group(2), fresh)
            new = new[:decl.end()] + "\n" + line + new[decl.end():]
            out.append(("splitctr:%s@%d" % (name, at), new))
    return out


_R59_DECL = re.compile(r"^(\s*)((?:struct\s+)?[A-Za-z_]\w*(?:\s*\*+\s*|\s+))([A-Za-z_]\w*)\s*(=\s*[^;]+)?;\s*$")


def r59_reuse_earlier_local(text):
    lines = text.split("\n")
    start = _r46_body(lines)
    if start is None:
        return []
    seen, out = [], []
    for i in range(start, len(lines)):
        m = _R59_DECL.match(lines[i])
        if not m or m.group(2).strip() in ("return", "else", "goto", "case"):
            continue
        typ, name, init = re.sub(r"\s+", "", m.group(2)), m.group(3), m.group(4)
        for j, t2, other in seen:
            if t2 != typ or other == name or not init:
                continue
            new = list(lines)
            new[i] = "%s%s %s;" % (m.group(1), other, init)
            for k in range(i + 1, len(new)):
                new[k] = re.sub(r"(?<![\w.>])%s\b" % re.escape(name), other, new[k])
            out.append(("reuse:%s->%s@%d" % (name, other, i), "\n".join(new)))
        seen.append((i, typ, name))
    return out


_R60_EXTERN = re.compile(r'(?m)^(\s*extern\s+(?:"C"\s+)?)((?:unsigned |signed )?(?:char|short|int|long)\s+)([A-Za-z_]\w*)(\s*\[)')
_R60_PTR_EXTERN = re.compile(r'(?m)^(\s*extern\s+(?:"C"\s+)?)((?:(?:const|unsigned|signed|struct)\s+)*[A-Za-z_]\w*\s*\*+\s*)'
                             r'([A-Za-z_]\w*)(\s*\[)')


def r60_const_extern_table(text):
    out = []
    for pat, before_name in ((_R60_EXTERN, False), (_R60_PTR_EXTERN, True)):
        for m in pat.finditer(text):
            name = m.group(3)
            if name == "const" or (not before_name and "const" in m.group(1)):
                continue
            if re.search(r"(?<![\w.>])%s\s*\[[^\]]*\]\s*(?:[-+*/|&^]?=(?!=)|\+\+|--)" % re.escape(name), text):
                continue
            if before_name:
                new = text[:m.start()] + m.group(1) + m.group(2).rstrip() + " const " + name + m.group(4) + text[m.end():]
            else:
                new = text[:m.start()] + m.group(1) + "const " + m.group(2) + name + m.group(4) + text[m.end():]
            out.append(("constextern:%s" % name, new))
    return out


_R61_MEMSET = re.compile(r"\bmemset\s*\(\s*([^,()]+(?:\([^()]*\))?[^,()]*)\s*,\s*0\s*,\s*([^;]+?)\)\s*;")


def r61_memset_to_clear(text):
    if not _R61_MEMSET.search(text):
        return []
    new = _R61_MEMSET.sub(lambda m: "__clear(%s, %s);" % (m.group(1).strip(), m.group(2).strip()), text)
    if not re.search(r"\b__clear\s*\([^;{]*\)\s*;", text.split("// USA:")[0]):
        new = re.sub(r"(?m)^(// USA:)", 'extern "C" void __clear(void* buf, int n);\n\n\\1', new, count=1)
    return [("clear", new)]


_R62_EXTERN_DATA = re.compile(r'(?m)^(\s*extern\s+(?:"C"\s+)?[^;(){}=\n]*?[A-Za-z_]\w*\s*(?:\[[^\]\n]*\])*)\s*;')


def r62_iro_align(text):
    picks = []
    for m in _R62_EXTERN_DATA.finditer(text):
        a = re.search(r"_([0-9a-fA-F]{8})\s*(?:\[|$)", m.group(1))
        if a and int(a.group(1), 16) % 4:
            continue
        picks.append((not m.group(1).rstrip().endswith("]"), m))
    if not picks:
        return []
    m = min(picks, key=lambda p: p[0])[1]
    name = re.search(r"([A-Za-z_]\w*)\s*(?:\[[^\]\n]*\])*\s*$", m.group(1)).group(1)
    return [("iroalign:%s" % name, text[:m.end(1)] + " __attribute__((aligned(4)))" + text[m.end(1):])]


_R63_ASSIGN = re.compile(r"^(\s*)((?:(?:unsigned|signed|const|volatile|struct)\s+)*[A-Za-z_][\w:]*(?:\s*\*+\s*|\s+))?"
                         r"([A-Za-z_]\w*)\s*=(?!=)\s*([^;]+?)\s*;\s*$")
_R63_TIERS = (("|",), ("^",), ("&",), ("<<", ">>"), ("+", "-"), ("*", "/", "%"))
_R63_SPLITS = ("|", "^", "&", "<<", "+", "-", "*")
_R63_OPERAND_END = re.compile(r"[\w)\]'\"]")


def _r63_top_ops(expr):
    ops, depth, i, prev = [], 0, 0, ""
    while i < len(expr):
        ch, two = expr[i], expr[i:i + 2]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0:
            if two in ("<<", ">>", "->", "::"):
                if two in ("<<", ">>"):
                    ops.append((i, two))
                i += 2
                prev = two[-1]
                continue
            if two in ("&&", "||", "==", "!=", "<=", ">=", "++", "--") or ch in "?,=<>":
                return None
            if ch in "|^&+-*/%" and _R63_OPERAND_END.match(prev or " "):
                ops.append((i, ch))
        if not ch.isspace():
            prev = ch
        i += 1
    return ops


def r63_split_assign_op(text):
    lines = text.split("\n")
    start = _r46_body(lines)
    if start is None:
        return []
    out = []
    for i in range(start, len(lines)):
        m = _R63_ASSIGN.match(lines[i])
        if not m or (m.group(2) or "").strip() in ("return", "else", "case", "goto", "delete", "throw"):
            continue
        ind, typ, name, rhs = m.group(1), m.group(2) or "", m.group(3), m.group(4)
        ops = _r63_top_ops(rhs)
        if not ops:
            continue
        tier = next(t for t in _R63_TIERS if any(op in t for _p, op in ops))
        at, op = [o for o in ops if o[1] in tier][-1]
        e, k = rhs[:at].strip(), rhs[at + len(op):].strip()
        if op not in _R63_SPLITS or not e or not k or e == name or re.search(_r52_word(name), k):
            continue
        if re.search(r"(?<![\w.>])(?!sizeof\b)[A-Za-z_]\w*\s*\(", k):
            continue
        declared = re.search(r"\b(float|double)\s+\**\s*%s\b" % re.escape(name), text)
        if declared or "float" in typ or "double" in typ:
            continue
        new = list(lines)
        new[i:i + 1] = ["%s%s%s = %s;" % (ind, typ, name, e), "%s%s %s= %s;" % (ind, name, op, k)]
        out.append(("splitop:%s@%d" % (name, i), "\n".join(new)))
    return out


RULES = (r62_iro_align, r61_memset_to_clear,r60_const_extern_table,r59_reuse_earlier_local,r58_split_loop_counter,r57_call_into_preceding_if,r56_drop_last_arg,r43_short_cast_in_add,r44_volatile_split_store, r45_accumulate_or, r46_counter_position,
         r47_ternary_store, r48_inline_index, r49_sink_into_loop, r50_narrow_flag, r51_short_spill,
         r52_inline_address_local, r53_update_in_place, r54_two_def_offset, r55_load_then_transform,
         r1_operand_swap, r2_compound_flip, r3_postinc_migrate, r4_decl_reorder, r5_stmt_swap,
         r6_fold_test, r7_decl_split, r8_decl_hoist, r9_compare_flip, r10_const_local,
         r11_decl_to_function_scope, r12_zero_accumulator, r13_dup_pool_literal, r14_decl_move,
         r15_zero_accumulator_assign, r16_stmt_move, r18_narrow_bind, r19_ternary_split,
         r20_bool_materialise, r21_cast_width, r22_inline_single_use, r23_single_exit_result,
         r24_arm_invert, r25_null_pointer_diff, r26_mla_accumulator, r27_pointer_roundtrip,
         r28_literal_in_equality_arm, r29_field_reread, r30_cse_repeat_expr, r31_hoist_loop_bound,
         r32_sink_store_into_arms, r33_field_signedness, r34_call_move_earlier,
         r35_chain_const_stores, r36_volatile_extern, r37_volatile_alias, r38_guarded_do_while,
         r39_split_pointer_add, r40_bind_call_operand, r41_nonfoldable_constant, r42_drop_noop_case,
         r63_split_assign_op, r17_decl_permute)


def neighbours(text):
    """One candidate per rule, then the second of each, and so on -- NOT rule-by-rule.

    Concatenating whole rules meant a file with many early-rule sites spent the entire budget before
    a later rule was ever scored. `ov023:021fa2f4` was 4 bytes short from a shared pool literal that
    r13 fixes; r13 is 13th of 21, and 150 compiles never reached it, so the sweep reported "no
    change" on a candidate whose score its own rule set could take from 1031 to 32. Round-robin
    makes the budget buy breadth across rules first, which is where a crack usually is.

    r17 still goes last as a block: it enumerates permutations, and interleaving 40 of them with the
    cheap single-step rewrites would push those out of a small budget instead.
    """
    seen, out = set(), []
    batches = [list(rule(text)) for rule in RULES[:-1]]
    for n in range(max((len(b) for b in batches), default=0)):
        for b in batches:
            if n < len(b):
                label, cand = b[n]
                h = hashlib.md5(cand.encode()).hexdigest()
                if h in seen or cand == text:
                    continue
                seen.add(h)
                out.append((label, cand))
    for label, cand in RULES[-1](text):
        h = hashlib.md5(cand.encode()).hexdigest()
        if h in seen or cand == text:
            continue
        seen.add(h)
        out.append((label, cand))
    return out


def main():
    module, addr, src = sys.argv[1], sys.argv[2], sys.argv[3]
    depth = int(sys.argv[sys.argv.index("--depth") + 1]) if "--depth" in sys.argv else 3
    budget = int(sys.argv[sys.argv.index("--budget") + 1]) if "--budget" in sys.argv else 80
    apply_best = "--apply" in sys.argv

    base = open(src, encoding="utf-8").read()
    spent = 0
    best_bytes, head = score(base, module, addr, "base")
    spent += 1
    print("base: %s" % head)
    if best_bytes == 0:
        return 0
    if best_bytes >= FAILSCORE:
        print("SWEEP-SKIP: base does not compile cleanly against the slot")
        return 1

    beam = [(best_bytes, base, "")]
    best = (best_bytes, base, "")
    tried = {hashlib.md5(base.encode()).hexdigest()}

    for level in range(depth):
        scored = []
        for _, text, trail in beam:
            for label, cand in neighbours(text):
                h = hashlib.md5(cand.encode()).hexdigest()
                if h in tried:
                    continue
                tried.add(h)
                if spent >= budget:
                    break
                nbytes, _hd = score(cand, module, addr, "s%d" % spent)
                spent += 1
                if nbytes >= FAILSCORE:
                    continue
                path = (trail + " > " + label).strip(" >")
                scored.append((nbytes, cand, path))
                if nbytes < best[0]:
                    best = (nbytes, cand, path)
                    print("  %s  <- %s" % (_fmt_score(nbytes), path))
                if nbytes == 0:
                    break
            if best[0] == 0 or spent >= budget:
                break
        if best[0] == 0 or spent >= budget or not scored:
            break
        scored.sort(key=lambda t: t[0])
        beam = scored[:3]

    print("RESULT %s after %d compiles: %s"
          % ("MATCH" if best[0] == 0 else _fmt_score(best[0]), spent, best[2] or "(no change)"))
    if apply_best and best[0] < best_bytes:
        with open(src, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(best[1])
        print("applied to %s" % src)
    return 0 if best[0] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
