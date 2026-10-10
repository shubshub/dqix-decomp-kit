#!/usr/bin/env python
"""Repair the link after merging a human branch, in both directions.

A merge with the human decomp leaves two symmetric breakages:

  1. OUR callers name a function the human branch folded into a class
     (CallFunc0202fa38ZeroPad is now BackgroundLoader::QueueLoadFile).
  2. THEIR files name a function we have since decompiled and renamed
     (func_0200fb08 is now NormalizeField5_0200fb08(Struct0200fb08*)).

Both are one lookup: take the address, ask the merged symbols.txt what lives
there now, rewrite the caller. Nothing is guessed -- an address or current symbol
that cannot be resolved is reported and left alone.

Two things this has to get right, both learned the hard way:

  * A caller may spell the callee with its MANGLED name
    (_Z21CallFunc0202fa38Mode2iiii), the bridge this repo uses when the caller
    has no access to the callee's argument types. A word-boundary search for the
    plain name never matches inside the mangled form, so those call sites were
    silently skipped and the link kept failing on the same symbol every round.
  * Renaming a free function to its mangled name only links if the declaration
    is extern "C". Left as a plain C++ declaration the compiler mangles the
    already-mangled name a second time, and the error returns wearing the new
    name: Undefined: _Z11UnlockMutexP5Mutex(RefNode020c80f8*).

Member calls become ((Class*)(arg0))->Method(rest); the casts are deliberate, so
the register setup is unchanged and a file that matched before still matches.

Usage: python relink_undefined.py <mwldarm-log>
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import regionblocks
import collections
import os
import re
import subprocess
import sys

REPO = _kp.REPO
LOG = sys.argv[1]
_MWLD = re.compile(r"mwldarm(?:\.exe)?:\s*")
os.chdir(REPO)


def symbol_paths():
    return ["config/usa/arm9/symbols.txt"] + sorted(
        os.path.join(r, "symbols.txt").replace("\\", "/")
        for r, _, fs in os.walk("config/usa/arm9/overlays") if "symbols.txt" in fs)


def load_symbols(rev=None):
    out = {}
    for p in symbol_paths():
        if rev:
            r = subprocess.run(["git", "show", "%s:%s" % (rev, p)], capture_output=True, text=True)
            text = r.stdout if r.returncode == 0 else ""
        else:
            text = open(p, encoding="utf-8", errors="replace").read()
        for line in text.splitlines():
            m = re.search(r"addr:0x([0-9a-fA-F]+)", line)
            if m and line.split():
                out.setdefault(int(m.group(1), 16), line.split()[0])
    return out


NOW = load_symbols()
OLD = load_symbols("decomp-matching")
MANGLED_PLAIN = re.compile(r"^_Z(\d+)(.+)$")

BY_NAME_OLD = {}                      # plain-or-mangled name -> address
PLAIN_TO_MANGLED = {}                 # plain name -> its old mangled spelling
for a, n in OLD.items():
    BY_NAME_OLD.setdefault(n, a)
    m = MANGLED_PLAIN.match(n)
    if m:
        plain = m.group(2)[:int(m.group(1))]
        BY_NAME_OLD.setdefault(plain, a)
        PLAIN_TO_MANGLED.setdefault(plain, n)

CURRENT_NAMES = set(NOW.values())

# The CURRENT table indexed the same way. Needed because rename_symbols.py has
# already put the human's plain names into our sources, and our local prototypes
# still carry our own parameter types -- so `int AllocateVRAMStagingMemory(int)`
# mangles to ...i while the symbol is ...j and the link fails on a name that is
# otherwise perfectly correct. Resolving it here routes it down the free-function
# path, which declares the MANGLED name extern "C" and makes the types moot.
BY_NAME_NOW = {}
for a, n in NOW.items():
    BY_NAME_NOW.setdefault(n, a)
    m = MANGLED_PLAIN.match(n)
    if m:
        BY_NAME_NOW.setdefault(m.group(2)[:int(m.group(1))], a)

ADDR_IN_NAME = re.compile(r"^func_(?:ov\d+_)?([0-9a-fA-F]{8})$")


def addr_of(name):
    m = ADDR_IN_NAME.match(name)
    if m:
        return int(m.group(1), 16)
    return BY_NAME_OLD.get(name, BY_NAME_NOW.get(name))


NESTED = re.compile(r"^_ZN((?:\d+[A-Za-z_][A-Za-z0-9_]*)+)E(.*)$")


def parts(s):
    out = []
    while s:
        m = re.match(r"(\d+)", s)
        if not m:
            break
        n = int(m.group(1))
        s = s[m.end():]
        out.append(s[:n])
        s = s[n:]
    return out


def member_of(sym):
    """-> (Class, Method) for a non-static member, else None."""
    m = NESTED.match(sym)
    if not m:
        return None
    p = parts(m.group(1))
    return ("::".join(p[:-1]), p[-1]) if len(p) >= 2 else None


HEADERS = {}
for root, _, files in os.walk("include"):
    for f in files:
        if f.endswith(".h"):
            p = os.path.join(root, f)
            HEADERS[os.path.relpath(p, "include").replace("\\", "/")] = open(
                p, encoding="utf-8", errors="replace").read()


def header_for(cls):
    """The header that DEFINES the class, not merely one that forward-declares it.

    `struct Model3D;` appears in several headers before the one holding the body.
    Including a forward declaration and then calling a method through it compiles
    to "illegal use of incomplete struct/union/class".
    """
    base = cls.split("::")[0]
    body = re.compile(r"\b(struct|class)\s+%s\b[^;]*\{" % re.escape(base), re.S)
    fwd = None
    for rel, text in sorted(HEADERS.items()):
        if body.search(text):
            return rel
        if fwd is None and re.search(r"\b(struct|class)\s+%s\b" % re.escape(base), text):
            fwd = rel
    return fwd


def split_args(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def signature(cls, method):
    """-> (return_type, [param types], is_static) from the human header.

    `static` must be read, not guessed. The Itanium ABI mangles a static member
    exactly like a non-static one (no `this` in either), so the mangled name
    cannot tell them apart -- guessing "no arguments means static" put
    `((Cls*)(r0))->GetInstance()` on a static and `AddFence()` with no object on
    a non-static, in the same run.

    Needed because our old free function and the human's method rarely agree on
    types: the caller had `int` where the class has `const char*`, `void**`,
    `SafeAllocator*`, and mwcc rejects the implicit conversions. Casting every
    argument to the declared parameter type is codegen-neutral -- the register
    setup is identical -- and makes the call compile as written.
    """
    hdr = header_for(cls)
    if not hdr:
        return None, None, False
    m = re.search(r"^\s*(static\s+|virtual\s+)?([A-Za-z_][\w:<>\s\*&]*?)\b%s\s*\(([^)]*)\)"
                  % re.escape(method), HEADERS[hdr], re.M)
    if not m:
        return None, None, False
    # group(2) is lazy over `[\w:\s]`, so an access label two lines up ("public:") lands
    # inside it and carries the `static` the caller must see out of group(1).
    decl = m.group(2).splitlines()[-1] if m.group(2).strip() else m.group(2)
    is_static = "static" in ((m.group(1) or "") + decl).split()
    ret = re.sub(r"\b(?:static|virtual|inline)\b", "", decl).strip()
    params = []
    for p in m.group(3).split(","):
        p = p.strip()
        if not p or p == "void":
            continue
        p = re.sub(r"\b[A-Za-z_]\w*\s*$", "", p).strip() or p   # drop the parameter name
        params.append(p)
    return ret, params, is_static


VALUE_CONTEXT = re.compile(r"(?:\breturn\b|[=<>!+\-*/&|,(])\s*$")


def rewrite_member(text, name, cls, method, mangled):
    """Rewrite calls to `name` as calls on the human class.

    One case must NOT go through the class: a method the human declares `void`
    whose result our caller still uses (`return obj->MakeHidden();`). Writing
    `m(); return 0;` would add a `mov r0,#0` the original does not have and the
    function stops matching, so those sites keep the mangled `extern "C"` bridge
    with the original return type instead -- same call, same registers.
    """
    ret, params, is_static = signature(cls, method)
    bridged = False
    out, i = [], 0
    pat = re.compile(r"\b%s\s*\(" % re.escape(name))
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            break
        depth, k = 1, m.end()
        while k < len(text) and depth:
            depth += (text[k] == "(") - (text[k] == ")")
            k += 1
        args = split_args(text[m.end():k - 1])
        out.append(text[i:m.start()])
        if ret == "void" and VALUE_CONTEXT.search(text[:m.start()].rstrip("\t ")[-80:]):
            out.append("%s(%s)" % (mangled, ", ".join(args)))
            # Remember the WIDEST bridged call: the caller emits one declaration for this symbol,
            # and a hardcoded `(void*)` prototype makes every bridged call with a different argument
            # count a compile error -- reported far from here, on a line this pass just wrote.
            bridged = max(bridged, len(args)) if bridged is not False else len(args)
            i = k
            continue
        if is_static:
            rest, obj = args, None            # a static takes no `this`
        elif args:
            rest, obj = args[1:], args[0]
        else:
            # Non-static but the caller passed nothing: recover the singleton.
            rest, obj = [], "%s::GetInstance()" % cls
        if params is not None and len(rest) > len(params):
            # Our old declaration was often over-wide -- `GetData02104304Field4(r0)`
            # against a zero-argument accessor. The extra argument sat in a
            # register the callee ignored, so it matched anyway; the typed
            # signature rejects it. Drop the surplus.
            rest = rest[:len(params)]
        if params is not None and len(params) == len(rest):
            rest = ["(%s)(%s)" % (t, a) for t, a in zip(params, rest)]
        if obj is None:
            call = "%s::%s(%s)" % (cls, method, ", ".join(rest))
        else:
            call = "((%s*)(%s))->%s(%s)" % (cls, obj, method, ", ".join(rest))
        if ret and ret.rstrip().endswith("*"):
            # Every caller we rewrite used the old int-returning free function,
            # so keep the expression an int; the cast costs no instructions.
            call = "(int)" + call
        out.append(call)
        i = k
    return "".join(out), bridged


KEYWORD = {"return", "if", "while", "for", "switch", "else", "do", "case", "sizeof"}


def strip_decl(text, n):
    """Delete the stale prototype of `n` -- and nothing else.

    Keying purely on "the line ends with );" also deletes:
      * assignments      `int commandId = _Z21GetData02104304Field4v();`
      * NESTED CALLS     `func_020301c8(GetData02104304Field4(), *handle);`
    Both compile to an error far from the edit, or -- worse -- to nothing at
    all: the second form silently dropped a whole call statement out of 46
    functions, which still built and still linked, and only showed up as those
    functions no longer matching.

    A prototype has ONLY a return type in front of the name: no assignment, no
    enclosing call, no control keyword.
    """
    pat = re.compile(r"^([^\n]*?)\b%s\s*\([^;{]*\)\s*;[ \t]*\n" % re.escape(n), re.M)

    def repl(m):
        lead = m.group(1)
        if not lead.strip() or (set(lead) & set("=(:;,{}?")):
            return m.group(0)
        if lead.strip().split()[-1].strip("!*&") in KEYWORD:
            return m.group(0)
        return ""
    return pat.sub(repl, text)


def force_extern_c(text, sym, cxx=True):
    """A declaration of a mangled symbol must be extern "C" or it mangles twice."""
    # A -lang=c translation unit rejects the specifier outright, and does not need it: the
    # mangled identifier already IS the symbol C emits.
    if not cxx:
        return text
    out, block_depth, depth = [], None, 0
    pat = re.compile(r"^(.*?)\b%s\s*\(" % re.escape(sym))
    for line in text.split("\n"):
        m = pat.match(line)
        lead = m.group(1) if m else ""
        # A DECLARATION has ONLY type tokens in front of the name. Anything else
        # there means it is a statement: a keyword (`return f(x);`), an enclosing
        # call (`sprintf(buf, fmt, f(x));`), or a label (`case 1: f(x);`).
        # Prefixing one of those yields `extern "C" case 1: ...`, reported far
        # away as "declarator expected".
        declares = (bool(m) and line.strip().endswith(";") and bool(lead.strip())
                    and not (set(lead) & set("=(:;,{}?"))
                    and lead.strip().split()[-1].strip("*&") not in KEYWORD)
        if declares and block_depth is None and 'extern "C"' not in line:
            line = re.sub(r"^(\s*)", r'\1extern "C" ', line)
        out.append(line)
        if re.search(r'extern\s+"C"\s*\{', line) and block_depth is None:
            block_depth = depth
        depth += line.count("{") - line.count("}")
        if block_depth is not None and depth <= block_depth:
            block_depth = None
    return "\n".join(out)


log = open(LOG, encoding="utf-8", errors="replace").read()
# The linker's own prefix, matched with the suffix optional: mwldarm.exe on Windows and under the
# Linux runner alike. Matching the exact spelling meant a log the host spelled differently parsed as
# zero undefined symbols, and the repair pass then reported "0 rewrites" and succeeded.
body = "\n".join(_MWLD.split(l, 1)[-1]
                 for l in log.splitlines() if _MWLD.search(l) and "warning:" not in l)
flat = re.sub(r"\s+", " ", body)
UNDEFINED = []
for part in re.split(r"Undefined : ", flat)[1:]:
    m = re.match(r'"?([^"]+?)"?\s*(?:Referenced from|$)', part)
    if m:
        UNDEFINED.append(m.group(1).split("(")[0].strip())

# Find the callers ourselves rather than reading the linker's "Referenced from"
# list. mwldarm hard-wraps its output at ~40 columns, so a long object name
# arrives split across two lines; flattening the log turns it into
# "Acquire FlushAndReleaseContext020bf1a0.o", which matches no file. Those
# callers were then never visited and the same symbols came back undefined on
# every iteration, forever.
SOURCES = []
# include/ as well as src/: a call rewritten in the .cpp whose DECLARATION sits in a header
# becomes "undefined identifier" -- a compile error this pass cannot see, since it reads the
# linker's output.
for root, _, files in [rn for d in ("src", "include") for rn in os.walk(d)]:
    for f in files:
        # .c as well as .cpp: a C caller of a renamed symbol was never visited, so the link kept
        # failing on it every round while this pass reported 0 rewrites -- indistinguishable from
        # "nothing left to repair".
        if f.endswith((".c", ".cpp", ".h", ".hpp")):
            SOURCES.append(os.path.join(root, f))

def definer_files(name):
    body = re.compile(r"^[ \t]*[A-Za-z_][\w:\t \*&<>]*?[ \t\*&]%s\s*\((?:[^;{}()]|\([^()]*\))*\)\s*(?:const\s*)?\{"
                      % re.escape(name), re.M)
    uses = re.compile(r"\b%s\b" % re.escape(name))
    out = set()
    for p in SOURCES:
        if not p.endswith((".c", ".cpp")):
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        if not body.search(text):
            continue
        out.add(os.path.normpath(p))
        for inc in re.findall(r'^\s*#\s*include\s*"([^"]+)"', text, re.M):
            h = os.path.normpath(os.path.join("include", inc))
            if os.path.isfile(h) and uses.search(open(h, encoding="utf-8", errors="replace").read()):
                out.add(h)
    return out


fixed, skipped = 0, []
for name in sorted(set(UNDEFINED)):
    a = addr_of(name)
    cur = NOW.get(a) if a is not None else None
    definers = definer_files(name)
    if cur == name or (cur is None and name in CURRENT_NAMES):
        # Already the right symbol, so the reference is not misnamed -- it is
        # mis-LINKED: a mangled name declared without extern "C" gets mangled a
        # second time. Nothing to rename, just fix the declaration.
        for p in SOURCES:
            if os.path.normpath(p) in definers:
                continue
            text = open(p, encoding="utf-8", errors="replace").read()
            if not re.search(r"\b%s\b" % re.escape(name), text):
                continue
            new = force_extern_c(text, name, p.endswith(".cpp"))
            if new != text:
                open(p, "w", encoding="utf-8", newline="\n").write(new)
                fixed += 1
                print("  %-30s -> extern \"C\" declaration in %s" % (name, os.path.relpath(p, REPO)))
        continue
    if a is None or cur is None:
        skipped.append((name, "no address" if a is None else "unchanged/unknown"))
        continue
    if not re.match(r"^[A-Za-z_]\w*$", cur):  # ".p__sinit_X" is a linker name, unspellable in C
        skipped.append((name, "current name %s is not a C identifier" % cur))
        continue
    mem = member_of(cur)
    spellings = [name] + ([PLAIN_TO_MANGLED[name]] if name in PLAIN_TO_MANGLED else [])
    for p in SOURCES:
        if os.path.normpath(p) in definers:
            continue
        text = open(p, encoding="utf-8", errors="replace").read()
        hit = [s for s in spellings if re.search(r"\b%s\b" % re.escape(s), text)]
        if not hit:
            continue                      # most files simply do not mention it
        for s in hit:
            if mem:
                cls, method = mem
                text = strip_decl(text, s)
                text, bridged = rewrite_member(text, s, cls, method, cur)
                if bridged is not False:
                    # The bridge needs a declaration of the mangled name with the SAME arity as the
                    # widest call we rewrote: a fixed one-parameter prototype fails to compile the
                    # moment a bridged call passes two, and the error lands on a line this pass
                    # just wrote, far from the cause.
                    linkage = 'extern "C" ' if p.endswith(".cpp") else ''
                    _ps = ", ".join(["void*"] * bridged) if bridged else "void"
                    decl = linkage + 'void* ' + cur + '(' + _ps + ');\n'
                    if decl not in text:
                        text = text.replace("#include <globaldefs.h>\n",
                                            "#include <globaldefs.h>\n" + decl, 1)
            else:
                # Never rewrite the `// USA: func_...` marker. It is the file's identity --
                # every tool maps file -> address through it -- and renaming a callee must not
                # change which function the file claims to be. Caught 08-18 after this pass
                # turned a marker into `// USA: _Z22ClearBitRange_021f6c3cPvjjj`.
                text, _n = regionblocks.rewrite(text, re.compile(r"\b(%s)\b" % re.escape(s)), {s: cur})
        if mem:
            hdr = header_for(mem[0])
            if hdr and hdr not in text:
                text = text.replace("#include <globaldefs.h>\n",
                                    '#include <globaldefs.h>\n#include "%s"\n' % hdr, 1)
        else:
            text = force_extern_c(text, cur, p.endswith(".cpp"))
        open(p, "w", encoding="utf-8", newline="\n").write(text)
        fixed += 1
        print("  %-30s -> %-44s %s" % (name, cur, os.path.relpath(p, REPO)))
print("relink_undefined: rewrote %d call sites" % fixed)
for n, why in sorted(set(skipped)):
    print("  SKIP %s (%s)" % (n, why))
