import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.dirname(_kpos.path.abspath(__file__))))
import kitpaths as _kp
import argparse
import glob
import os
import re
import subprocess
import sys
from collections import Counter

REPO = _kp.REPO
SP = _kp.SP
KIT = _kp.KIT
POOLS = ["attempts", "clsbest", "quarantine", "wip"] + sorted(
    os.path.basename(p) for p in glob.glob(SP + "/hold*"))
SYM = re.compile(r"(\S+) kind:(\w+)\S* addr:0x([0-9a-fA-F]{8})")
TAG = re.compile(r"\s*//\s*(?:SCRATCH-)?USA:")
KEYWORD = {"return", "if", "while", "for", "switch", "else", "do", "case", "sizeof", "goto"}
BUILTIN = {"unsigned", "signed", "char", "short", "int", "long", "void", "float", "double", "bool"}
DECL = re.compile(r"^\s*(?:extern\s+\"C\"\s+)?(?:static\s+|inline\s+)*"
                  r"([A-Za-z_][\w\s\*&:<>]*?[\s\*&])([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*;")


def git(*args):
    return subprocess.run(["git", "-C", REPO] + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="ignore").stdout


def functions(rev):
    out = {}
    for path in git("ls-tree", "-r", "--name-only", rev, "config/usa/arm9").split():
        if path.endswith("symbols.txt"):
            m = re.search(r"/ov(\d+)/", path)
            for line in git("show", "%s:%s" % (rev, path)).splitlines():
                s = SYM.match(line)
                if s and s.group(2) == "function":
                    out[(m.group(1) if m else "main", int(s.group(3), 16))] = s.group(1)
    return out


def plain(sym):
    m = re.match(r"_Z(\d+)", sym)
    if m:
        return sym[m.end():m.end() + int(m.group(1))]
    return None if sym.startswith("_Z") else sym


def nested(sym):
    if not sym.startswith("_ZN"):
        return None
    i, parts = 4 if sym[3:4] == "K" else 3, []
    while i < len(sym) and sym[i].isdigit():
        n = re.match(r"\d+", sym[i:]).group()
        i += len(n)
        parts.append(sym[i:i + int(n)])
        i += int(n)
    return parts if len(parts) >= 2 and sym[i:i + 1] == "E" else None


def declares_member(text, cls, name):
    m = re.search(r"\b(?:struct|class)\s+%s\b[^;{]*\{" % re.escape(cls), text)
    if not m:
        return False
    depth, k = 0, m.end() - 1
    while k < len(text):
        depth += (text[k] == "{") - (text[k] == "}")
        k += 1
        if depth == 0:
            break
    return bool(re.search(r"\b%s\s*\(" % re.escape(name), text[m.end():k]))


def headers(rev):
    return {p: git("show", "%s:%s" % (rev, p))
            for p in git("ls-tree", "-r", "--name-only", rev, "include").split()
            if p.endswith((".h", ".hpp"))}


def type_names(texts):
    names = set()
    for t in texts:
        names.update(re.findall(r"\b(?:struct|class|union|enum)\s+([A-Za-z_]\w*)\s*(?::[^{;]*)?\{", t))
        names.update(re.findall(r"\}\s*([A-Za-z_]\w*)\s*;", t))
        names.update(re.findall(r"\btypedef\b[^;{]*?\b([A-Za-z_]\w*)\s*;", t))
    return names


def prototypes(texts):
    out = {}
    for t in texts:
        for line in t.splitlines():
            m = DECL.match(line)
            if m and m.group(1).strip().split()[-1].strip("*&") not in KEYWORD:
                out.setdefault(m.group(2), (m.group(1).strip(), m.group(3).strip()))
    return out


def definition(text, t):
    m = re.search(r"^[ \t]*struct\s+%s\s*\{" % re.escape(t), text, re.M)
    if not m:
        return None
    depth, k = 0, m.end() - 1
    while k < len(text):
        depth += (text[k] == "{") - (text[k] == "}")
        k += 1
        if depth == 0:
            break
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", text[m.start():text.find(";", k) + 1], flags=re.S)
    for line in body.split("\n")[1:-1]:
        f = re.match(r"\s*(?:struct\s+|const\s+|volatile\s+)*([A-Za-z_]\w*)\s*(\*?)", line)
        if f and f.group(1) not in BUILTIN and not f.group(2):
            return None
    return "\n".join(ln.rstrip() for ln in body.strip().split("\n"))


def build(rev):
    old, new = functions(rev), functions("HEAD")
    ren = {old[k]: new[k] for k in old if k in new and old[k] != new[k]}
    spell = dict(ren)
    byplain = {}
    for o, n in ren.items():
        p = plain(o)
        if p and p != o:
            byplain.setdefault(p, set()).add(n)
    current = set(new.values())
    for p, ns in byplain.items():
        if len(ns) == 1 and p not in current:
            spell.setdefault(p, next(iter(ns)))
    oh, nh = headers(rev), headers("HEAD")
    changed = [p for p in oh if oh[p] != nh.get(p)]
    protos = prototypes(oh[p] for p in changed)
    gone = type_names(oh[p] for p in changed) - type_names(nh.values())
    defs = {}
    for p in changed:
        for t in gone:
            d = definition(oh[p], t)
            if d:
                defs.setdefault(t, d)
    moved = {}
    for line in git("diff", "-M", "--name-status", rev, "HEAD", "--", "include").splitlines():
        f = line.split("\t")
        if f[0].startswith("R") and len(f) == 3:
            moved[f[1][len("include/"):]] = f[2][len("include/"):]
    return spell, protos, gone, moved, defs


def _declares(line, sym):
    m = re.match(r"^(.*?)\b%s\s*\(" % re.escape(sym), line)
    if not m or not line.strip().endswith(";"):
        return False
    lead = m.group(1)
    return (bool(lead.strip()) and not (set(lead) & set("=(:;,{}?!<>+-/%|^~"))
            and not any(tok.strip("*&") in KEYWORD for tok in lead.split()))


def force_extern_c(text, sym, cxx):
    if not cxx:
        return text
    out = []
    for line in text.split("\n"):
        if _declares(line, sym) and 'extern "C"' not in line:
            line = re.sub(r"^(\s*)", r'\1extern "C" ', line)
        out.append(line)
    return "\n".join(out)


def insert_after_includes(text, block):
    lines = text.split("\n")
    last = max((i for i, ln in enumerate(lines) if re.match(r"\s*#\s*include\b", ln)), default=-1)
    lines[last + 1:last + 1] = block
    return "\n".join(lines)


def rewrite(text, cxx, spell, protos, gone, moved, defs):
    orig = text
    for o, n in moved.items():
        text = re.sub(r'(#\s*include\s*[<"])%s([>"])' % re.escape(o), r"\g<1>%s\2" % n, text)
    words = re.compile(r"\b(%s)\b" % "|".join(sorted(map(re.escape, spell), key=len, reverse=True)))
    defined = {w for w in set(words.findall(text))
               if re.search(r"^[ \t]*[A-Za-z_][\w \t\*&:<>]*?[ \t\*&]%s\s*\([^;{}()]*\)\s*(?:const\s*)?\{"
                            % re.escape(w), text, re.M)}
    for w in set(words.findall(text)):
        parts = nested(spell[w])
        if parts and parts[-1] == w and declares_member(text, parts[-2], w):
            defined.add(w)
    used = {}
    lines = text.split("\n")
    for k, ln in enumerate(lines):
        if TAG.match(ln):
            continue

        def sub(m):
            w = m.group(1)
            if w in defined:
                return w
            used.setdefault(spell[w], w)
            return spell[w]
        lines[k] = words.sub(sub, ln)
    text = "\n".join(lines)
    add = []
    for newsym, oldw in sorted(used.items()):
        if any(_declares(ln, newsym) for ln in text.split("\n")):
            text = force_extern_c(text, newsym, cxx)
            continue
        proto = protos.get(plain(oldw) or oldw)
        if proto is None:
            return None, "no prototype for %s" % oldw
        add.append('%s%s %s(%s);' % ('extern "C" ' if cxx else "", proto[0], newsym, proto[1]))
    probe = text + "\n" + "\n".join(add)
    used_t = [t for t in sorted(gone) if re.search(r"\b%s\b" % re.escape(t), probe)]
    bare = [t for t in used_t if not re.search(r"\b(?:struct|class|union)\s+%s\s*[;{:]" % re.escape(t), text)]
    full = [defs[t] for t in bare if t in defs]
    block = ["struct %s;" % t for t in bare if t not in defs] + full + add
    if block:
        text = insert_after_includes(text, block)
    what = ", ".join("%s->%s" % (o, n) for n, o in sorted(used.items()))
    what += "".join(" +def %s" % t for t in used_t if t in defs and defs[t] in full)
    return text, (what.strip() or ("includes" if text != orig else None))


def pool_files(exclude):
    for pool in POOLS:
        for p in sorted(glob.glob("%s/%s/**/*.c*" % (SP, pool), recursive=True)):
            if not p.endswith((".c", ".cpp")) or any(a in p for a in exclude):
                continue
            text = open(p, encoding="utf-8", errors="ignore").read()
            if any(TAG.match(ln) for ln in text.split("\n")) and not any(a in text for a in exclude):
                yield p, text


def locate(path, text):
    tag = next((ln for ln in text.split("\n") if TAG.match(ln)), "") + " " + os.path.basename(path)
    a = re.search(r"(02[0-9a-fA-F]{6})", tag)
    m = re.search(r"ov(\d{3})", tag) or re.search(r"hold_(?:ov)?(\d{3})", path)
    return (m.group(1) if m else "main"), (a.group(1).lower() if a else None)


def gate(mod, addr, path):
    r = subprocess.run([sys.executable, KIT + "/wgate.py", mod, addr, path], capture_output=True, text=True,
                       cwd=REPO, env={**os.environ, "WGATE_ALLOW_COMMITTED": "1"})
    out = (r.stdout + r.stderr).strip().splitlines()
    return next((ln for ln in out if "COMPILE-FAIL" in ln or "NO-COMPILE" in ln), out[-1] if out else "no output")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--rev", default="a70058a0")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--exclude", default="")
    a = ap.parse_args()
    spell, protos, gone, moved, defs = build(a.rev)
    exclude = [x for x in a.exclude.split(",") if x]
    files = ([(p, open(p, encoding="utf-8", errors="ignore").read()) for p in a.paths]
             if a.paths else list(pool_files(exclude)))
    stats = Counter()
    for path, text in files:
        new, what = rewrite(text, path.endswith(".cpp"), spell, protos, gone, moved, defs)
        if new is None:
            stats["unfixable"] += 1
            print("SKIP  %s  %s" % (os.path.relpath(path, SP), what))
            continue
        if new == text:
            continue
        target = path if a.apply else path + ".repool"
        if a.apply:
            mod, addr = locate(path, text)
            if addr:
                before = gate(mod, addr, path)
                open(target, "w", encoding="utf-8", newline="\n").write(new)
                after = gate(mod, addr, target)
                if "COMPILE" in after and "COMPILE" not in before:
                    open(target, "w", encoding="utf-8", newline="\n").write(text)
                    stats["reverted"] += 1
                    print("REVERT %s  rewrite broke the compile: %s" % (os.path.relpath(path, SP), after[:120]))
                    continue
        stats["rewritten"] += 1
        open(target, "w", encoding="utf-8", newline="\n").write(new)
        verdict = ""
        if a.check:
            mod, addr = locate(path, new)
            verdict = gate(mod, addr, target) if addr else "no address"
            stats["NO-COMPILE" if "COMPILE" in verdict else "compiles"] += 1
        if not a.apply:
            os.remove(target)
        print("%s  %s  %s%s" % ("FIX " if a.apply else "WOULD", os.path.relpath(path, SP), what,
                                ("  | " + verdict[:160]) if verdict else ""))
    print("repool: %s" % dict(stats))


if __name__ == "__main__":
    main()
