"""Repair callee declarations that cannot resolve to the committed symbol.

A worker writes `extern "C" int Foo_02012345(int);` for a callee the ROM carries as the C++-mangled
`_Z14Foo_02012345i`. Under `extern "C"` mwcc emits an unmangled reference, which either fails to
link or -- worse -- resolves to a DIFFERENT symbol, and NEITHER shows up until the function is
already byte-exact, because wgate reports BYTEDIFF first and never reaches the link. 31 of 119 kept
artifacts carry one. `ov015:0218ee38` cost two extra rounds to exactly this.

The fix is mechanical: the committed mangled name encodes the parameter list, so the correct
declaration can be derived from it. Drop the `extern "C"`, emit the demangled signature, and forward
declare any struct it names.

    python symfix.py <file.cpp> [...]        rewrite in place, report what changed
    python symfix.py --audit                 every clsbest artifact, no writes
    python symfix.py --all                   rewrite every clsbest artifact in place
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import glob
import os
import re
import sys

import buildcfg
import residue

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO

BUILTIN = {"v": "void", "i": "int", "j": "unsigned int", "c": "char", "a": "signed char",
           "h": "unsigned char", "s": "short", "t": "unsigned short", "l": "long",
           "m": "unsigned long", "x": "long long", "y": "unsigned long long",
           "f": "float", "d": "double", "b": "bool", "w": "wchar_t"}


def demangle_params(enc):
    """-> (list of C++ type strings, set of struct names). Raises ValueError on anything unhandled."""
    out, subs, i = [], [], 0
    while i < len(enc):
        ptr = 0
        while i < len(enc) and enc[i] == "P":
            ptr += 1
            i += 1
        if i >= len(enc):
            raise ValueError("trailing P")
        ch = enc[i]
        if ch == "S":                                   # substitution: S_ or S<n>_
            j = enc.index("_", i) + 1
            idx = 0 if enc[i + 1] == "_" else int(enc[i + 1:j - 1]) + 1
            if idx >= len(subs):
                raise ValueError("bad substitution")
            base = subs[idx]
            i = j
        elif ch.isdigit():                              # <len><name>
            j = i
            while enc[j].isdigit():
                j += 1
            n = int(enc[i:j])
            base = "struct " + enc[j:j + n]
            i = j + n
            subs.append(base)
        elif ch in BUILTIN:
            base = BUILTIN[ch]
            i += 1
        else:
            raise ValueError("unhandled encoding %r" % ch)
        t = base + ("*" * ptr)
        if ptr:
            subs.append(t)
        out.append(t)
    if out == ["void"]:
        out = []
    return out


def committed_symbols():
    syms = {}
    for p in [f"{REPO}/{buildcfg.config_dir('main')}/symbols.txt"] + \
            sorted(glob.glob(f"{REPO}/{buildcfg.config_dir('main')}/overlays/*/symbols.txt")):
        for line in open(p, encoding="utf-8", errors="ignore"):
            m = re.match(r"(\S+)\s+kind:function\(", line)
            if m:
                syms[m.group(1)] = True
    return syms


SYMS = committed_symbols()
MANGLED = {}
for _s in SYMS:
    _m = re.match(r"^_Z(\d+)(.+)$", _s)
    if _m:
        _n = int(_m.group(1))
        MANGLED.setdefault(_m.group(2)[:_n], []).append((_s, _m.group(2)[_n:]))

DECL = re.compile(r'^(?P<indent>\s*)(?P<externc>extern\s+"C"\s+)(?P<rest>(?:extern\s+)?'
                  r'(?:ARM|THUMB)?\s*[\w:*&<>\s]+?\b(?P<name>[A-Za-z_]\w*)\s*\((?P<args>[^;{]*)\)\s*;)',
                  re.M)


def split_args(s):
    """Top-level comma split of a call's argument text."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def retype_calls(txt, name, params):
    """Cast every argument of each call to `name` to its declared parameter type.

    Fixing the DECLARATION alone is not enough: the call site was written against the wrong
    signature, so `f(void*)` against `f(Obj*)` stops compiling. An explicit cast between compatible
    types emits no code, so this is codegen-neutral -- ov015:0218ee38 needed exactly this cast by
    hand after its declaration was corrected.
    """
    if not params:
        return txt
    out, i = [], 0
    pat = re.compile(r"\b%s\s*\(" % re.escape(name))
    while True:
        m = pat.search(txt, i)
        if not m:
            out.append(txt[i:])
            break
        # skip the declaration itself
        line_start = txt.rfind("\n", 0, m.start()) + 1
        line = txt[line_start:txt.find("\n", m.start())]
        depth, j = 0, m.end() - 1
        while j < len(txt):
            if txt[j] == "(":
                depth += 1
            elif txt[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(txt) or re.match(r"^\s*(?:extern|struct|ARM|THUMB|static|void|int|unsigned|char|short|long|float|double|bool)\b.*\)\s*;\s*$", line):
            out.append(txt[i:j + 1])
            i = j + 1
            continue
        args = split_args(txt[m.end():j])
        if len(args) == len(params):
            cast = ", ".join("(%s)(%s)" % (t, a.strip()) if "(" + t + ")" not in a else a.strip()
                             for t, a in zip(params, args))
            out.append(txt[i:m.end()] + cast)
        else:
            out.append(txt[i:j])
        out.append(")")
        i = j + 1
    return "".join(out)


def fix_text(txt):
    """-> (new_text, [(name, signature)]) for every declaration repaired."""
    fixed, structs = [], []

    def repl(m):
        name = m.group("name")
        if name in SYMS or name not in MANGLED:
            return m.group(0)
        sym, enc = MANGLED[name][0]
        try:
            params = demangle_params(enc)
        except ValueError:
            return m.group(0)
        rest = m.group("rest")
        head = rest[:rest.index("(")]
        sig = "%s(%s);" % (head, ", ".join(params) if params else "void")
        for p in params:
            s = re.match(r"struct (\w+)", p)
            if s and s.group(1) not in structs:
                structs.append(s.group(1))
        fixed.append((name, sig.strip(), params))
        return m.group("indent") + sig

    new = DECL.sub(repl, txt)
    # NO BLUNT CALL-SITE CASTING. Casting every argument to its declared type broke 13 of 29 files
    # against 2 for the declaration fix alone: the caster cannot tell a call from a definition or a
    # nested expression reliably, and a wrong rewrite costs more than the fault it repairs. Where the
    # call genuinely disagrees with the ROM's signature, that is real information for the worker --
    # `retype_calls` is kept for a targeted single-callee fix, not applied wholesale.
    if structs:
        fwd = "".join("struct %s;\n" % s for s in structs
                      if not re.search(r"\bstruct\s+%s\s*[;{]" % s, new))
        if fwd:
            i = new.rfind("#include")
            i = new.index("\n", i) + 1 if i >= 0 else 0
            new = new[:i] + fwd + new[i:]
    return new, fixed


def owner_of(addr):
    for p in [f"{REPO}/{buildcfg.config_dir('main')}/symbols.txt"] + \
            sorted(glob.glob(f"{REPO}/{buildcfg.config_dir('main')}/overlays/*/symbols.txt")):
        mod = "main" if "overlays" not in p else re.search(r"ov(\d+)", p).group(1)
        if re.search(r"addr:0x0*%s\b" % addr.lstrip("0"),
                     open(p, encoding="utf-8", errors="ignore").read()):
            return mod
    return None


def verdict(mod, addr, path):
    import subprocess
    r = subprocess.run([sys.executable, f"{KIT}/wgate.py", mod, addr, path],
                       capture_output=True, text=True, cwd=REPO)
    return residue.headline((r.stdout or "") + (r.stderr or ""))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    if any(f in ("-h", "--help") for f in flags):
        print(__doc__)
        sys.exit(0)
    unknown = [f for f in flags if f not in ("--audit", "--all")]
    if unknown or not (args or flags):
        print(__doc__)
        sys.exit(2)
    audit = "--audit" in flags
    files = args or sorted(glob.glob(f"{SP}/clsbest/*.cpp"))
    total, touched, refused = 0, 0, 0
    for f in files:
        txt = open(f, encoding="utf-8", errors="ignore").read()
        new, fixed = fix_text(txt)
        if not fixed:
            continue
        if audit:
            touched += 1
            total += len(fixed)
            print(os.path.basename(f))
            for name, sig, _p in fixed:
                print("    %s" % sig)
            continue
        # A REPAIR IS NOT A REPAIR UNTIL THE GATE AGREES. Rewriting a declaration can expose a call
        # site that disagreed with the ROM's real signature; that file must be left alone rather than
        # left broken, because a NO-COMPILE artifact is worth less than a wrong-symbol one.
        addr = re.search(r"([0-9a-fA-F]{8})", os.path.basename(f))
        mod = owner_of(addr.group(1).lower()) if addr else None
        if not mod:
            continue
        addr = addr.group(1).lower()
        before = verdict(mod, addr, f)
        tmp = f + ".symfix"
        open(tmp, "w", encoding="utf-8", newline="\n").write(new)
        after = verdict(mod, addr, tmp)
        if after == "?" or after.startswith("RESIDUE NO-COMPILE"):
            os.remove(tmp)
            refused += 1
            print("%-9s REFUSED  %s -> %s (call sites disagree with the ROM signature)"
                  % (addr, before, after))
            continue
        os.replace(tmp, f)
        touched += 1
        total += len(fixed)
        print("%-9s %-28s -> %-28s %d decl(s)" % (addr, before, after, len(fixed)))
    print("%d declaration(s) in %d file(s)%s; %d refused"
          % (total, touched, " (audit only)" if audit else " rewritten", refused))


if __name__ == "__main__":
    main()
