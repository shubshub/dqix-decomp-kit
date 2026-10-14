"""Flag source constructs the original developers would not plausibly have written.

    python plausible.py <file.cpp> [...]      prints one line per finding; exit 1 if any

blocker.py records a miss on a flagged file as IMPLAUSIBLE; pad/evo_score.py penalises one.
"""
import bisect
import collections
import os
import re
import sys

import mangle
from pragmas import ASM_SITE, asm_allow, asm_allow_symbols

HWADDR = r"0x0?(?:4[0-9a-fA-F]{6}|2[37]f[0-9a-fA-F]{4})\b"
HWTARGET = re.compile(r"[\s(]*(?:%s|&?\s*[A-Z][A-Z0-9_]+\b(?!\s*(?:\.|->|\[)))" % HWADDR)
HW = re.compile(r"\(\s*volatile[^)]*\*\s*\)\s*" + HWADDR)
VCAST = re.compile(r"\((?:\s*const)?\s*volatile\b[^()]*\*\s*\)|\([^()]*\bvolatile\s*\*+\s*\)")
IDENT = r"[A-Za-z_]\w*"
NUM = r"(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*"
KEYWORDS = set("""if else for while do switch case default return goto break continue sizeof typedef
new delete throw try catch operator template typename using namespace public private protected
extern static inline asm __asm virtual friend explicit this true false NULL alignof decltype
static_cast reinterpret_cast const_cast dynamic_cast""".split())
BUILTIN = set("void char short int long float double bool signed unsigned".split())
QUALS = set("const volatile static register struct union enum class mutable extern".split())
SCALAR_TYPEDEFS = {"u8": "unsigned char", "s8": "signed char", "u16": "unsigned short", "s16": "short",
                   "u32": "unsigned int", "s32": "int", "u64": "unsigned long long", "s64": "long long",
                   "BOOL": "int", "vu8": "unsigned char", "vu16": "unsigned short", "vu32": "unsigned int",
                   "fx32": "int", "fx16": "short", "uint": "unsigned int", "size_t": "unsigned int"}
INTEGERS = {"int", "unsigned int", "short", "unsigned short", "signed char", "unsigned char", "bool"}
TOKEN_RE = re.compile(r"::|->|\+\+|--|<<=|>>=|[-+*/%&|^!=<>]=|&&|\|\||<<|>>|[A-Za-z_]\w*|"
                      r"0[xX][0-9a-fA-F]+[uUlL]*|\d+\.?\d*(?:[eE][-+]?\d+)?[uUlLfF]*|\.\.\.|\S")
LV = r"(?:\*\s*)?%s(?:\s*(?:->|\.)\s*%s|\s*\[[^\]=]+\])*" % (IDENT, IDENT)
ASSIGN_RE = re.compile(r"^(%s)\s*=(?!=)\s*(.+)$" % LV, re.S)
UPDATE_RE = re.compile(r"^(%s)\s*(?:[-+*/%%&|^]|<<|>>)=\s*(.+)$" % LV, re.S)
LABEL_RE = re.compile(r"^\s*(?:(case\s+[^:]+|default)\s*:(?!:)|(%s)\s*:(?!:))" % IDENT)
ASSERT_STMT = re.compile(r"^SDK_\w*ASSERT\w*\s*\(")


def strip_code(text):
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if text[k] != "\n":
                    out[k] = " "
            i = j
            continue
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                if text[j] == "\\":
                    j += 1
                j += 1
            if not (c == '"' and text[i + 1:j] == "C"):
                for k in range(i + 1, min(j, n)):
                    if text[k] != "\n":
                        out[k] = "_" if c == "'" else " "
            i = j + 1
            continue
        i += 1
    lines = "".join(out).split("\n")
    cont = False
    stack = []
    for idx, ln in enumerate(lines):
        if cont or ln.lstrip().startswith("#"):
            d = ln.strip()
            cont = ln.rstrip().endswith("\\")
            lines[idx] = " " * len(ln)
            if re.match(r"#\s*if", d):
                region = re.search(r"\b(?:jpn|eur|JPN|EUR|IS_JPN|IS_EUR)\b", d)
                neg = re.match(r"#\s*ifndef|#\s*if\s*!", d)
                stack.append(("region", bool(neg)) if region else ("other", True))
            elif re.match(r"#\s*elif", d) and stack:
                region = re.search(r"\b(?:jpn|eur|JPN|EUR|IS_JPN|IS_EUR)\b", d)
                stack[-1] = ("region", False) if region else ("other", True)
            elif re.match(r"#\s*else", d) and stack:
                k, active = stack[-1]
                stack[-1] = (k, not active if k == "region" else True)
            elif re.match(r"#\s*endif", d) and stack:
                stack.pop()
            continue
        if any(k == "region" and not active for k, active in stack):
            lines[idx] = " " * len(ln)
    return "\n".join(lines)


def _top_eq(h):
    depth = 0
    for k, c in enumerate(h):
        if c in "([":
            depth += 1
        elif c in ")]":
            depth -= 1
        elif c == "=" and depth == 0:
            prev = h[k - 1] if k else ""
            nxt = h[k + 1] if k + 1 < len(h) else ""
            if prev not in "=!<>" and nxt != "=":
                return True
    return False


def parse_params(text):
    t = text.strip()
    if not t or t == "void":
        return []
    out = []
    depth = 0
    cur = ""
    for c in t:
        if c in "(<[":
            depth += 1
        elif c in ")>]":
            depth -= 1
        if c == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += c
    out.append(cur)
    res = []
    for p in out:
        p = re.sub(r"=.*$", "", p.strip()).strip()
        if p == "...":
            res.append(("...", "..."))
            continue
        fm = re.search(r"\(\s*\*\s*(%s)\s*\)" % IDENT, p)
        if fm:
            res.append((p, fm.group(1)))
            continue
        p2 = re.sub(r"\[[^\]]*\]", "", p).strip()
        toks = re.findall(IDENT, p2)
        if not toks:
            res.append((p, None))
            continue
        last = toks[-1]
        if (len(toks) == 1 or last in BUILTIN or last in QUALS
                or p2.rstrip().endswith(("*", "&")) and not re.search(r"[*&]\s*%s\s*$" % last, p2)):
            res.append((p, None))
            continue
        typ = p2[:p2.rfind(last)].strip()
        if not typ or typ in QUALS:
            res.append((p, None))
            continue
        res.append((typ, last))
    return res


def parse_decl(stmt):
    t = stmt.strip()
    if not t or t[0] in "*&(-+!~\"'":
        return None
    toks = TOKEN_RE.findall(t)
    if not toks:
        return None
    i = 0
    quals = []
    while i < len(toks) and toks[i] in QUALS:
        quals.append(toks[i])
        i += 1
    base = []
    while i < len(toks) and toks[i] in BUILTIN:
        base.append(toks[i])
        i += 1
    if not base:
        if i >= len(toks) or not re.match(IDENT + "$", toks[i]) or toks[i] in KEYWORDS:
            return None
        base.append(toks[i])
        i += 1
        while i + 1 < len(toks) and toks[i] == "::" and re.match(IDENT + "$", toks[i + 1]):
            base.append("::" + toks[i + 1])
            i += 2
        if i < len(toks) and toks[i] == "<":
            depth = 0
            while i < len(toks):
                if toks[i] == "<":
                    depth += 1
                elif toks[i] == ">":
                    depth -= 1
                elif toks[i] == ">>":
                    depth -= 2
                base.append(toks[i])
                i += 1
                if depth <= 0:
                    break
    while i < len(toks) and toks[i] in ("const", "volatile"):
        quals.append(toks[i])
        i += 1
    decls = []
    while True:
        ptr = 0
        while i < len(toks) and toks[i] in ("*", "&", "const", "volatile", "&&"):
            if toks[i] in ("*", "&", "&&"):
                ptr += 1
            else:
                quals.append(toks[i])
            i += 1
        if i >= len(toks) or not re.match(IDENT + "$", toks[i]) or toks[i] in KEYWORDS or toks[i] in BUILTIN:
            return None
        name = toks[i]
        i += 1
        arr = False
        while i < len(toks) and toks[i] == "[":
            depth = 0
            while i < len(toks):
                if toks[i] == "[":
                    depth += 1
                elif toks[i] == "]":
                    depth -= 1
                i += 1
                if depth == 0:
                    break
            arr = True
        init = None
        if i < len(toks) and toks[i] in ("=", "(", ":"):
            start = i + 1 if toks[i] != "(" else i
            depth = 0
            j = start
            while j < len(toks):
                if toks[j] in ("(", "[", "{"):
                    depth += 1
                elif toks[j] in (")", "]", "}"):
                    depth -= 1
                elif toks[j] == "," and depth == 0:
                    break
                j += 1
            init = " ".join(toks[start:j])
            if toks[i] == "(":
                init = "(" + init[1:] if init else init
            i = j
        decls.append((name, ptr, arr, init))
        if i >= len(toks):
            break
        if toks[i] == ",":
            i += 1
            continue
        return None
    return (" ".join(base), decls, quals)


def norm_type(t):
    t = re.sub(r"\b(?:const|volatile|struct|union|class|enum)\b", " ", t)
    s = " ".join(SCALAR_TYPEDEFS.get(w, w) for w in t.replace("*", " * ").split())
    s = re.sub(r"\bsigned int\b", "int", s)
    s = re.sub(r"\bunsigned int\b", "unsigned", s)
    s = re.sub(r"\bunsigned long\b(?! long)", "unsigned", s)
    s = re.sub(r"\blong int\b", "int", s)
    s = re.sub(r"\blong\b(?! long)", "int", s)
    s = re.sub(r"\bshort int\b", "short", s)
    s = re.sub(r"\bsigned short\b", "short", s)
    s = re.sub(r"\bunsigned short int\b", "unsigned short", s)
    s = re.sub(r"(?<!signed )(?<!unsigned )\bchar\b", "signed char", s)
    s = re.sub(r"\bunsigned\b(?! (?:char|short|long))", "unsigned int", s)
    s = re.sub(r"\bunsigned int int\b", "unsigned int", s)
    return re.sub(r"\s+", " ", s).replace(" *", "*").strip()


def ref_re(name):
    return re.compile(r"(?<![\w.])(?<!->)(?<!::)%s\b" % re.escape(name))


def _norm(s):
    return re.sub(r"\s+", "", s)


class Src:
    def __init__(self, text):
        self.text = text
        self.code = strip_code(text)
        self.starts = [0] + [m.end() for m in re.finditer("\n", self.code)]
        self.lines = self.code.split("\n")
        self.funcs = []
        self.fields = {}
        self.decls = []
        self.file_vars = set()
        self._scan()

    def line(self, off):
        return bisect.bisect_right(self.starts, off)

    def match_brace(self, i):
        depth = 0
        code = self.code
        while i < len(code):
            if code[i] == "{":
                depth += 1
            elif code[i] == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return len(code) - 1

    def _scan(self):
        code = self.code
        frames = ["top"]
        i = seg = paren = 0
        while i < len(code):
            c = code[i]
            if c == "(":
                paren += 1
            elif c == ")":
                paren -= 1
            elif paren == 0 and c == ";":
                self._file_stmt(code[seg:i], seg, frames[-1])
                seg = i + 1
            elif paren == 0 and c == "}":
                if len(frames) > 1:
                    frames.pop()
                seg = i + 1
            elif paren == 0 and c == "{":
                header = code[seg:i]
                h = re.sub(r"^\s*(?:(?:public|private|protected)\s*:\s*)+", "", header).strip()
                if re.match(r'^extern\s+"C"$', h) or re.match(r"^namespace\b", h):
                    frames.append("ns")
                    seg = i + 1
                elif _top_eq(h):
                    i = self.match_brace(i) + 1
                    continue
                elif (re.search(r"\b(?:struct|class|union|enum)\b", h) and not h.endswith(")")
                      and not re.search(r"\)\s*(?:const)?\s*$", h)):
                    if re.search(r"\benum\b", h):
                        i = seg = self.match_brace(i) + 1
                        continue
                    frames.append("struct")
                    seg = i + 1
                elif (re.search(r"\)\s*(?:const\s*)?(?:throw\s*\(\s*\)\s*)?$", h)
                      or re.search(r"\)\s*:\s*[^:].*\)\s*$", h, re.S)):
                    j = self.match_brace(i)
                    self._add_func(header, i, j, frames[-1] == "struct")
                    i = seg = j + 1
                    continue
                else:
                    i = seg = self.match_brace(i) + 1
                    continue
            i += 1

    def _file_stmt(self, text, off, kind):
        t = text.strip()
        if not t:
            return
        d = parse_decl(re.sub(r'^extern\s+"C"\s+', "", t))
        if kind == "struct":
            if d:
                for name, ptr, arr, init in d[1]:
                    self.fields.setdefault(name, set()).add(d[0] + "*" * ptr + ("[]" if arr else ""))
            return
        if re.search(r"\)\s*(?:const\s*)?$", t) and not _top_eq(t):
            return
        if d:
            lineno = self.line(off + len(text) - len(text.lstrip()))
            for name, ptr, arr, init in d[1]:
                self.file_vars.add(name)
            self.decls.append((lineno, t, d))

    def _add_func(self, header, open_i, close_i, in_struct):
        h = header
        cut = re.search(r"\)\s*:\s*[^:]", h)
        if cut and h.count("(") > 1:
            h = h[:cut.start() + 1]
        end = h.rstrip()
        while end.endswith("const"):
            end = end[:-5].rstrip()
        if end.endswith("throw ( )") or end.endswith("throw()"):
            end = end[:end.rfind("throw")].rstrip()
        if not end.endswith(")"):
            return
        depth = 0
        k = len(end) - 1
        while k >= 0:
            if end[k] == ")":
                depth += 1
            elif end[k] == "(":
                depth -= 1
                if depth == 0:
                    break
            k -= 1
        m = re.search(r"((?:%s\s*::\s*)*~?%s|operator\s*\S+)\s*$" % (IDENT, IDENT), end[:k])
        self.funcs.append({
            "name": re.sub(r"\s+", "", m.group(1)) if m else "?",
            "params": parse_params(end[k + 1:-1]),
            "open": open_i,
            "close": close_i,
            "line": self.line(open_i),
            "in_struct": in_struct,
            "asm": bool(re.search(r"\basm\b", header)),
            "inline": bool(re.search(r"\binline\b", header)) or in_struct,
        })


class Stmt:
    __slots__ = ("kind", "text", "off", "line", "depth", "labels", "ctrl")

    def __init__(self, kind, text, off, line, depth):
        self.kind = kind
        self.text = text
        self.off = off
        self.line = line
        self.depth = depth
        self.labels = []
        self.ctrl = False


def _labelled(src, kind, text, off, depth):
    lead = len(text) - len(text.lstrip())
    st = Stmt(kind, text, off, src.line(off + lead), depth)
    t = text
    while True:
        m = LABEL_RE.match(t)
        if not m:
            break
        st.labels.append(m.group(1) or m.group(2))
        t = t[m.end():]
    st.text = t.strip()
    st.ctrl = re.match(r"^(?:if|else|for|while|do|switch)\b", st.text) is not None
    return st


def body_stmts(src, f):
    code = src.code
    e = f["close"]
    out = []
    depth = paren = 0
    seg = i = f["open"] + 1
    while i < e:
        c = code[i]
        if c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif paren == 0 and c == ";":
            out.append(_labelled(src, "stmt", code[seg:i], seg, depth))
            seg = i + 1
        elif paren == 0 and c == "{":
            hs = code[seg:i].strip()
            if re.search(r"(?:=|\breturn)\s*$", hs) or (hs and hs[-1] in ",(["):
                i = src.match_brace(i) + 1
                continue
            if re.match(r"^(?:struct|union|class|enum)\b", hs) and "(" not in hs or re.match(r"^(?:asm|__asm)$", hs):
                k = src.match_brace(i) + 1
                while k < e and code[k] in " \t\r\n":
                    k += 1
                if k < e and code[k] == ";":
                    k += 1
                seg = i = k
                continue
            out.append(_labelled(src, "open", code[seg:i], seg, depth))
            depth += 1
            seg = i + 1
        elif paren == 0 and c == "}":
            if code[seg:i].strip():
                out.append(_labelled(src, "stmt", code[seg:i], seg, depth))
            depth -= 1
            out.append(Stmt("close", "", i, src.line(i), depth))
            seg = i + 1
        i += 1
    if code[seg:e].strip():
        out.append(_labelled(src, "stmt", code[seg:e], seg, depth))
    return out


class Fn:
    def __init__(self, src, f):
        self.src = src
        self.f = f
        self.stmts = body_stmts(src, f)
        self.body = src.code[f["open"] + 1:f["close"]]
        self.body_off = f["open"] + 1
        self.params = [n for _t, n in f["params"] if n and n != "..."]
        self.locals = []
        self.types = {n: t for t, n in f["params"] if n}
        for st in self.stmts:
            text = st.text
            if st.kind == "open":
                m = re.match(r"^for\s*\((.*?);", text, re.S)
                text = m.group(1) if m else ""
            elif st.kind != "stmt" or st.ctrl or text.startswith(("return", "goto", "delete")):
                continue
            d = parse_decl(text) if text else None
            if not d:
                continue
            for name, ptr, arr, init in d[1]:
                self.locals.append((st, name, ptr, arr, init, d[2], st.kind == "open"))
                self.types[name] = (("volatile " if "volatile" in d[2] else "") + d[0] + "*" * ptr
                                    + ("[]" if arr else ""))
        self._refs = {}

    def refs(self, name):
        if name not in self._refs:
            self._refs[name] = [m.start() + self.body_off for m in ref_re(name).finditer(self.body)]
        return self._refs[name]

    def stmt_at(self, off):
        best = None
        for st in self.stmts:
            if st.off > off:
                break
            best = st
        return best


def ref_kind(src, off, name, stmt):
    code = src.code
    after = code[off + len(name):off + len(name) + 120]
    before = code[stmt.off:off] if stmt is not None else code[max(0, off - 80):off]
    b = re.sub(r"^\s*(?:(?:case\s+[^:]+|default|%s)\s*:(?!:)\s*)+" % IDENT, "", before.rstrip())
    start_ok = ((b == "" or b.endswith((";", "{", "}", "else", ",", "(", "="))
                 and not b.endswith(("==", "!=", "<=", ">=")))
                or re.search(r"\)\s*$", b) is not None and re.match(r"^\s*(?:if|while|for)\b", b) is not None)
    if b.endswith(("*", "&", ".", "->")) and not b.endswith("&&"):
        return "read"
    if re.match(r"\s*(?:\.\w+|\[[^\]]*\])*\s*=(?!=)", after) and start_ok:
        return "mwrite" if re.match(r"\s*(?:\.\w+|\[[^\]]*\])+", after) else "write"
    loop_head = r"^\s*for\s*\([^;]*;[^;]*;(?:[^;]*,)?\s*$"
    stmt_start = (b == "" or re.match(loop_head, b, re.S) is not None
                  or re.search(r"^\s*(?:else\s*|(?:if|while)\s*\(.*\)\s*)$", b, re.S) is not None)
    if re.match(r"\s*(?:\+\+|--|[-+*/%&|^]=|<<=|>>=)", after) and stmt_start:
        return "update"
    pre2 = b[:-2].rstrip()
    if b.endswith(("++", "--")) and (pre2 == "" or re.match(loop_head, pre2, re.S)):
        return "update"
    return "read"


def _hit(out, line, kind, what):
    out.append((line, kind, what))


LINE_RULES = [
    ("NULL-PTR-DIFF", re.compile(r"-\s*\(\s*(?:const\s+)?(?:unsigned\s+|signed\s+)?%s\s*\*+\s*\)\s*(?:0|NULL)\b"
                                 % IDENT)),
    ("TERNARY-SELF", re.compile(r"(?<![\w.>])(%s)\s*=(?!=)[^;?]*\?\s*(?:\1\s*:|[^:;?]*:\s*\1\s*;)" % LV)),
    ("COMPARE-MINUS-ONE", re.compile(r"(?:[^->]>|<=)\s*-\s*1\b(?![.\w])|(?:^|[(=,!&|?:])\s*-\s*1\s*(?:<(?![<=])|>=)")),
    ("SPLIT-POINTER-ADD", re.compile(r"\(\s*%s\s*\+\s*0[xX][0-9a-fA-F]+\s*\)\s*\+\s*0[xX][0-9a-fA-F]+00\b" % IDENT)),    ("INDEX-CAST", re.compile(r"\[\s*\(\s*(?:(?:unsigned|signed)\s+)?(?:int|long|unsigned)\s*\)\s*(%s)\s*\]"
                              % IDENT)),
    ("FNPTR-CAST-CALL", re.compile(r"\(\s*\(\s*[\w\s\*]+\(\s*\*\s*\)\s*\([^()]*\)\s*\)\s*&?\s*(%s)\s*\)\s*\("
                                   % IDENT)),
    ("LONGLONG-MASK", re.compile(r"\(\s*(?:unsigned\s+)?(?:int|unsigned|long|short|char)\s*\)\s*\(\s*\(\s*"
                                 r"(?:unsigned\s+)?long\s+long\s*\)\s*\(?[\w\->.\[\]]+\)?\s*"
                                 r"(?:[|&^]\s*0[xX][0-9a-fA-F]+[uU]?[lL]{0,2}|<<\s*\d+)\s*\)")),
    ("CC-FLAG-EMULATION", re.compile(r"\b(?:cc|flags?|nz|cmp)\s*=\s*\(\s*(?:int|unsigned int|unsigned)\s*\)\s*"
                                     r"\(?[\w\->.\[\]]+\)?\s*-\s*\(\s*(?:int|unsigned int|unsigned)\s*\)")),
    ("ABS-ADDRESS-CAST", re.compile(r"\(\s*__typeof__\s*\(|\(\s*(?:const\s+|volatile\s+)*[\w\s]+\*+\s*\)\s*"
                                    r"0[xX]0?(?:1[fF]{2}[0-9a-fA-F]{5}|2[0-3][0-9a-fA-F]{6})\b")),
]


def _line_rules(src, fn, out):
    lo, hi = fn.f["open"], fn.f["close"]
    l0, l1 = src.line(lo), src.line(hi)
    for ln in range(l0, l1 + 1):
        s = src.lines[ln - 1]
        if ln == l1:
            s = s[:hi - src.starts[ln - 1]]
        if ln == l0:
            s = " " * (lo - src.starts[ln - 1] + 1) + s[lo - src.starts[ln - 1] + 1:]
        if not s.strip():
            continue
        for kind, rx in LINE_RULES:
            for m in rx.finditer(s):
                if kind == "INDEX-CAST" and norm_type(fn.types.get(m.group(1), "?")) not in INTEGERS:
                    continue
                if kind == "FNPTR-CAST-CALL":
                    tgt = m.group(1)
                    if tgt in fn.types or tgt in src.file_vars and not tgt.startswith(("func_", "_Z")):
                        continue
                if kind == "COMPARE-MINUS-ONE" and re.search(r"->\s*-", m.group(0)):
                    continue
                _hit(out, ln, kind, m.group(0).strip()[:60])


def _binds(fn, idx):
    st = fn.stmts[idx]
    if st.kind != "stmt" or st.ctrl:
        return None
    m = ASSIGN_RE.match(st.text)
    if m and not _top_eq(m.group(2)):
        lv = _norm(m.group(1))
        return lv if re.match(IDENT + "$", lv) and lv not in fn.params else None
    d = parse_decl(st.text)
    if d and len(d[1]) == 1 and d[1][0][3] is not None:
        return d[1][0][0]
    return None


def _split_pointer_add(src, fn, out):
    for idx in range(len(fn.stmts) - 1):
        name = _binds(fn, idx)
        s2 = fn.stmts[idx + 1]
        if not name or s2.kind != "stmt" or s2.ctrl or s2.labels or not fn.types.get(name, "").endswith("*"):
            continue
        m3, m4 = UPDATE_RE.match(s2.text), ASSIGN_RE.match(s2.text)
        if m3 and _norm(m3.group(1)) == name:
            rhs, op = m3.group(2), s2.text[len(m3.group(1)):].lstrip()[:1]
        elif m4 and _norm(m4.group(1)) == name and re.match(
                r"^\(?\s*(?:\([^()]*\)\s*)*\(?\s*(?:\([^()]*\)\s*)?%s\s*(?:[-+*|&^]|<<|>>)" % re.escape(name),
                _norm(m4.group(2))):
            rhs, op = m4.group(2), "="
        else:
            continue
        if op in "+-=" and re.search(r"(?:^|[-+]\s*)%s\s*\)?\s*$" % NUM, rhs.strip()):
            _hit(out, s2.line, "SPLIT-POINTER-ADD", name)


def _pointer_round_trip(src, fn, out):
    body = fn.body
    for m in re.finditer(r"\(\s*(%s)\s*([-+])\s*1\s*\)\s*(->|\[)" % IDENT, body):
        name, sign = m.group(1), m.group(2)
        t = fn.types.get(name, "")
        if "*" not in t and "[]" not in t:
            continue
        inc, op = (r"\+\+", r"\+") if sign == "-" else ("--", "-")
        n = re.escape(name)
        if re.search(r"(?:\b%s\s*%s|%s\s*%s\b|\b%s\s*%s=\s*1\b|\b%s\s*=[^;]*%s\s*1\s*;)"
                     % (n, inc, inc, n, n, op, n, op), body[:m.start()]):
            _hit(out, src.line(fn.body_off + m.start()), "POINTER-ROUND-TRIP", name)


PUN_RE = re.compile(r"\*\s*\(\s*((?:const\s+|volatile\s+)*(?:unsigned\s+|signed\s+)?%s(?:\s+%s)?)\s*(\*+)\s*\)\s*"
                    r"(&\s*)?(%s(?:\s*(?:->|\.)\s*%s|\s*\[[^\]]*\])*)(?!\s*(?:\(|->|\.|\[|\+|-(?!>)))"
                    % (IDENT, IDENT, IDENT, IDENT))


def _puns(src, fn):
    body = fn.body
    for m in PUN_RE.finditer(body):
        ctype, stars, amp, expr = m.groups()
        if "volatile" in ctype:
            continue
        line = src.line(fn.body_off + m.start())
        tgt = norm_type(ctype + "*" * (len(stars) - 1))
        what = "*(%s%s)%s%s" % (ctype, stars, "&" if amp else "", _norm(expr))
        if amp:
            fld = re.findall(IDENT, expr)[-1]
            if re.search(r"(?:->|\.)\s*%s\s*$" % fld, expr):
                types = src.fields.get(fld) or set()
                vt = next(iter(types)) if len(types) == 1 else None
            else:
                vt = fn.types.get(expr) if re.match(IDENT + "$", expr) else None
            if vt and "[]" not in vt and norm_type(vt) == tgt:
                yield line, "NOOP-ADDR-CAST", what
            continue
        vt = fn.types.get(expr, "") if re.match(IDENT + "$", expr) else ""
        words = set(re.findall(IDENT, vt.rstrip("*"))) - QUALS
        if not vt.endswith("*") or not words or words & (BUILTIN | set(SCALAR_TYPEDEFS)):
            continue
        tail, head = body[m.end():m.end() + 16], body[max(0, m.start() - 12):m.start()]
        if (re.match(r"\s*[!=]=\s*(?:0|NULL)\b", tail)
                or re.search(r"(?:if\s*\(|!|&&|\|\|)\s*$", head) and re.match(r"\s*(?:\)|&&|\|\|)", tail)):
            yield line, "INT-PUN-NULL-TEST", what


def _int_pun_null_test(src, fn, out):
    for line, kind, what in _puns(src, fn):
        if kind == "INT-PUN-NULL-TEST":
            _hit(out, line, kind, what)


def _noop_addr_cast(src, fn, out):
    for line, kind, what in _puns(src, fn):
        if kind == "NOOP-ADDR-CAST":
            _hit(out, line, kind, what)


def _member_addr_null_test(src, fn, out):
    count = collections.Counter(l[1] for l in fn.locals)
    for st, name, ptr, arr, init, quals, in_for in fn.locals:
        if count[name] > 1 or "static" in quals or "extern" in quals or not ptr or not init:
            continue
        if not re.match(r"^&\s*[\w\->.\[\]\s]+(?:->|\.)\s*\w+$", init.strip()):
            continue
        refs = fn.refs(name)
        kinds = []
        for r in refs:
            s2 = fn.stmt_at(r)
            if s2 is st and not in_for:
                continue
            kinds.append(ref_kind(src, r, name, s2 if s2 is not None and s2.kind in ("stmt", "open") else None))
        if not kinds or "write" in kinds or "update" in kinds:
            continue
        for r in refs:
            tail = src.code[r + len(name):r + len(name) + 12]
            head = src.code[max(0, r - 12):r].rstrip()
            if head.endswith(("*", "&")) and not head.endswith("&&"):
                continue
            if (re.match(r"\s*[!=]=\s*(?:0|NULL)\b", tail)
                    or re.search(r"(?:if\s*\(|!|&&|\|\|)$", head) and re.match(r"\s*(?:\)|&&|\|\|)", tail)):
                _hit(out, src.line(r), "MEMBER-ADDR-NULL-TEST", name)
                break


def _self_store(src, fn, out):
    for st in fn.stmts:
        if st.kind != "stmt" or st.ctrl:
            continue
        m = ASSIGN_RE.match(st.text)
        if not m or _top_eq(m.group(2)):
            continue
        lv = _norm(m.group(1))
        base = re.findall(IDENT, lv)[0]
        if _norm(m.group(2)) == lv and not re.match(IDENT + "$", lv) and "volatile" not in fn.types.get(base, ""):
            _hit(out, st.line, "SELF-STORE", lv)


def _inline_bodies(src):
    for f in src.funcs:
        if f["inline"] and not f["asm"]:
            body = src.code[f["open"] + 1:f["close"]].strip()
            stmts = [x.strip() for x in body.split(";") if x.strip() and not ASSERT_STMT.match(x.strip())]
            yield f, stmts, dict((n, t) for t, n in f["params"] if n)


def _inline_local_return(src, out):
    for f, stmts, params in _inline_bodies(src):
        d = parse_decl(stmts[0]) if len(stmts) == 2 and not stmts[0].startswith("static") else None
        if d and len(d[1]) == 1 and re.match(r"^return\s+%s$" % re.escape(d[1][0][0]), stmts[1]):
            _hit(out, f["line"], "INLINE-LOCAL-RETURN", f["name"])


def _inline_offset_add(src, out):
    for f, stmts, params in _inline_bodies(src):
        if f["in_struct"] or len(stmts) != 1:
            continue
        m = re.match(r"^return\s+(?:\(\s*[^()]+\)\s*)?\(?\s*(?:\(\s*[^()]+\)\s*)?(%s)\s*[-+]\s*%s\s*\)?$"
                     % (IDENT, NUM), stmts[0])
        if not m or m.group(1) not in params:
            continue
        if params[m.group(1)].rstrip().endswith("*") or re.search(r"\(\s*[^()]*\*\s*\)", stmts[0]):
            _hit(out, f["line"], "INLINE-OFFSET-ADD", f["name"])


def _inline_identity(src, out):
    for f, stmts, params in _inline_bodies(src):
        if f["in_struct"] or len(stmts) != 1:
            continue
        m = re.match(r"^return\s+(?:\(\s*[^()]+\)\s*)?\(?\s*(%s)\s*\)?$" % IDENT, stmts[0])
        if m and m.group(1) in params and not f["name"].startswith("operator"):
            _hit(out, f["line"], "INLINE-IDENTITY", f["name"])


def _symbol_alias(src, out):
    for line, text, d in src.decls:
        for name, ptr, arr, init in d[1]:
            if re.search(r"[0-9a-fA-F]{8}_(?:dup|arg|alias)\d*$", name):
                _hit(out, line, "SYMBOL-ALIAS", name)


FN_RULES = [_split_pointer_add, _pointer_round_trip, _int_pun_null_test, _noop_addr_cast,
            _member_addr_null_test, _self_store]
MANGLED_DECL = re.compile(r'(?m)^[ \t]*extern\s+"C"\s+[^;{}()]*?\b(_Z\w+)\s*\(([^()]*)\)\s*;')


def _mangle_arity(src, out):
    for m in MANGLED_DECL.finditer(src.text):
        name, params = m.group(1), m.group(2).strip()
        if "..." in params:
            continue
        declared = 0 if params in ("", "void") else params.count(",") + 1
        mangled = mangle.mangled_arity(name)
        if mangled is None:
            continue
        most = mangled + 1 if name.startswith("_ZN") else mangled
        if declared > most:
            line = src.text.count("\n", 0, m.start(1)) + 1
            _hit(out, line, "MANGLE-ARITY-MISMATCH", "%s mangled %d declared %d" % (name, mangled, declared))


ACCESSOR = re.compile(r"inline\s+[^;{}()]*?\b([A-Za-z_]\w*)\s*\(\s*(?:const\s+)?(?:struct\s+)?[A-Za-z_][\w\s]*\*\s*"
                      r"(?:const\s+)?([A-Za-z_]\w*)\s*\)\s*(?:const\s*)?\{\s*return\s+\2\s*->\s*([A-Za-z_]\w*)\s*;\s*\}")
ALIAS = re.compile(r"(?m)^\s*(?:(?:const\s+)?(?:unsigned\s+|signed\s+)?[A-Za-z_]\w*\s*\*\s*)?([A-Za-z_]\w*)\s*=\s*"
                   r"(&?)\s*([A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*)*)\s*;")


def _mixed_access(src, out):
    text = src.text
    accessors = list(ACCESSOR.finditer(text))
    body = text
    for m in reversed(accessors):
        body = body[:m.start()] + " " * (m.end() - m.start()) + body[m.end():]
    for m in accessors:
        name, field = m.group(1), m.group(3)
        for call in re.finditer(r"\b%s\s*\(\s*(&?)\s*([A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*)*)\s*\)" % re.escape(name), body):
            amp, arg = call.group(1), call.group(2)
            direct = r"\b%s\s*%s\s*%s\b" % (re.escape(arg), r"\." if amp else "->", re.escape(field))
            if re.search(direct, body):
                line = text.count("\n", 0, call.start()) + 1
                _hit(out, line, "MIXED-ACCESS-RELOAD", "%s(%s%s) beside %s" % (name, amp, arg, field))
                break
    for m in ALIAS.finditer(text):
        alias, base = m.group(1), m.group(3)
        rest = text[m.end():]
        b = re.escape(base)
        if alias != base and len(re.findall(r"\b%s\b" % re.escape(alias), rest)) == 1 and re.search(
                r"\b%s\s*\[" % re.escape(alias), rest) and re.search(r"\b%s\s*(?:\[|\.)|\(\s*&\s*%s\s*\)\s*\[" % (b, b), rest):
            _hit(out, text.count("\n", 0, m.start()) + 1, "ALIAS-REREAD", "%s = %s" % (alias, base))


REGISTER_NAME = re.compile(r"\b(?:unsigned\s+int|int|u32|s32|unsigned)\s+(r(?:[0-9]|1[0-2]))\b")
ADDRESS_LABEL = re.compile(r"(?m)^\s*(L[0-9a-fA-F]{1,4})\s*:")


def _transliterated(src, out):
    regs = sorted(set(REGISTER_NAME.findall(src.text)), key=lambda r: int(r[1:]))
    if len(regs) >= 2:
        line = src.text.count("\n", 0, REGISTER_NAME.search(src.text).start()) + 1
        _hit(out, line, "TRANSLITERATED", "registers " + ",".join(regs))
    labels = list(ADDRESS_LABEL.finditer(src.text))
    if labels:
        _hit(out, src.text.count("\n", 0, labels[0].start()) + 1, "ADDRESS-LABEL",
             ",".join(m.group(1) for m in labels))



def _enclosing(src, off):
    for f in src.funcs:
        if f["open"] <= off <= f["close"]:
            return f["name"]
    later = [f for f in src.funcs if f["open"] > off and not re.search(r"[;}]", src.code[off:f["open"]])]
    return later[0]["name"] if later else "?"


def _hand_asm(src, out):
    for m in ASM_SITE.finditer(src.code):
        _hit(out, src.line(m.start()), "HAND-ASM", _enclosing(src, m.start()).split("::")[-1])


FILE_RULES = [_inline_local_return, _inline_offset_add, _inline_identity, _symbol_alias, _mangle_arity, _mixed_access,
              _transliterated, _hand_asm]


def _body_start(lines):
    for i, ln in enumerate(lines):
        if ln.startswith("// USA:"):
            return i
    return 0


def _segments(src, lines, start):
    segs = []
    if start:
        usa = src.starts[start]
        for f in src.funcs:
            if f["open"] >= usa:
                continue
            a, b = f["open"] + 1, min(f["close"], usa)
            la, lb = src.line(a) - 1, src.line(b) - 1
            for i in range(la, lb + 1):
                lo = a - src.starts[i] if i == la else 0
                hi = b - src.starts[i] if i == lb else len(lines[i])
                segs.append((i, lo, hi))
    return sorted(segs) + [(i, 0, len(lines[i])) for i in range(start, len(lines))]


def findings(text):
    out = []
    lines = text.split("\n")
    start = _body_start(lines)
    src = Src(text)

    for i, lo, hi in _segments(src, lines, start):
        ln = lines[i][lo:hi]
        m = re.match(r"^\s+(?:const\s+)?volatile\s+[^;(]*?\b(%s)\s*(=\s*([^;]+))?;" % IDENT, ln)
        if m and not (m.group(3) and HW.search(ln)):
            out.append((i + 1, "VOLATILE-LOCAL", m.group(1)))

        if not ln.lstrip().startswith("#"):
            for m in VCAST.finditer(ln):
                if not HWTARGET.match(ln[m.end():]):
                    out.append((i + 1, "VOLATILE-CAST", m.group(0)))

        m = re.match(r"^\s+(?:const\s+)?(?:struct\s+)?%s\s*\*+\s*(%s)\s*=\s*&[^;]+;\s*$" % (IDENT, IDENT), ln)
        if m:
            name = m.group(1)
            rest = "\n".join(lines[i + 1:])
            if not re.search(r"(?<![\w.>])%s\b" % re.escape(name), rest):
                out.append((i + 1, "DEAD-ADDRESS-LOCAL", name))

        m = re.match(r"^\s+(%s)\s*=\s*(%s)\s*(\+\s*0\s*)?;\s*$" % (IDENT, IDENT), ln)
        if m and m.group(1) == m.group(2):
            out.append((i + 1, "SELF-ASSIGN", m.group(1)))

        m = re.match(r"^\s+(%s)\s*=\s*0u?\s*-\s*(%s)\s*;\s*$" % (IDENT, IDENT), ln)
        if m and m.group(1) == m.group(2):
            name = m.group(1)
            for j in range(i - 1, max(-1, i - 4), -1):
                prev = lines[j].strip()
                if not prev or prev.startswith("//"):
                    continue
                if re.match(r"^(?:const\s+)?(?:unsigned\s+|signed\s+)?\w[\w\s]*?\b%s\s*=\s*0u?\s*;"
                            % re.escape(name), prev):
                    out.append((i + 1, "ZERO-NEGATE-PAD", name))
                break

    inl = {}
    for m in re.finditer(r"(?m)^(?:static\s+)?inline\s+[^({;]*?\b(%s)\s*\(([^)]*)\)\s*\{([^{}]*)\}" % IDENT, text):
        name, params, fbody = m.group(1), m.group(2), m.group(3).strip()
        inl[name] = fbody
    for name, fbody in inl.items():
        callees = [c for c in re.findall(r"\b(%s)\s*\(" % IDENT, fbody) if c in inl and c != name]
        stmts = [s for s in fbody.split(";") if s.strip()]
        if callees and len(stmts) <= 2:
            depth, cur, seen = 1, callees[0], {name}
            while cur in inl and cur not in seen:
                seen.add(cur)
                nxt = [c for c in re.findall(r"\b(%s)\s*\(" % IDENT, inl[cur]) if c in inl and c not in seen]
                depth += 1
                if not nxt:
                    break
                cur = nxt[0]
            if depth >= 3:
                out.append((0, "INLINE-WRAPPER-CHAIN", "%s (depth %d)" % (name, depth)))

    for f in src.funcs:
        if f["asm"]:
            continue
        fn = Fn(src, f)
        _line_rules(src, fn, out)
        for rule in FN_RULES:
            rule(src, fn, out)
    for rule in FILE_RULES:
        rule(src, out)
    return out


ADVISORY = {"VOLATILE-CAST", "VOLATILE-LOCAL", "TERNARY-SELF", "ALIAS-REREAD", "MIXED-ACCESS-RELOAD",
            "DEAD-ADDRESS-LOCAL", "COMPARE-MINUS-ONE", "MANGLE-ARITY-MISMATCH"}
ALLOW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plausible_allow.txt")
USA_ADDR = re.compile(r"//\s*USA:\s*\S*?([0-9a-fA-F]{8})\b")


def allowed():
    out = set()
    try:
        for ln in open(ALLOW_FILE, encoding="utf-8"):
            parts = ln.split()
            if len(parts) >= 3 and not ln.startswith("#"):
                out.add((parts[0].lower(), parts[1]))
    except OSError:
        pass
    return out


def blocking(text):
    addrs = {a.lower() for a in USA_ADDR.findall(text)}
    ok = allowed()
    asm_addrs, asm_names = asm_allow(), asm_allow_symbols()
    asm_ok = lambda h: h[2] in asm_names or (addrs and addrs <= asm_addrs)
    return [h for h in findings(text) if h[1] not in ADVISORY and not any((a, h[1]) in ok for a in addrs)
            and not (h[1] == "HAND-ASM" and asm_ok(h))]


def hold(path, mod, addr, sp=None):
    text = open(path, encoding="utf-8", errors="ignore").read()
    hits = blocking(text)
    if not hits:
        return []
    if sp is None:
        import kitpaths
        sp = kitpaths.SP
    sub = "main" if mod == "main" else "ov" + mod
    dest = f"{sp}/hold_plausible/{sub}/{os.path.basename(path)}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    kinds = sorted({k for _, k, _ in hits})
    lines = ",".join(sorted({str(n) for n, _, _ in hits if n}, key=int))
    hint = ";".join(f"{k}: see core.md NATURAL FORMS" for k in kinds)
    os.makedirs(f"{sp}/wlog", exist_ok=True)
    with open(f"{sp}/wlog/clean_queue.tsv", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{mod}\t{addr}\t{dest}\t{lines}\t{hint}\tstaging\n")
    return kinds


def gained(before, after):
    count = lambda text: collections.Counter(k for _, k, _ in blocking(text or ""))
    was, now = count(before), count(after)
    return sorted(k for k in now if now[k] > was[k])


def main():
    if sys.argv[1:2] == ["--hold"] and len(sys.argv) == 5:
        print(" ".join(hold(sys.argv[4], sys.argv[2], sys.argv[3])))
        return 0
    bad = 0
    only_blocking = "--blocking" in sys.argv
    check = blocking if only_blocking else findings
    for p in [a for a in sys.argv[1:] if a != "--blocking"]:
        for line, kind, what in check(open(p, encoding="utf-8", errors="ignore").read()):
            print("%s:%d: %s %s" % (p, line, kind, what))
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
