import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import json
import os
import re
import shutil
import subprocess
import sys

REPO = _kp.REPO
SP = _kp.SP
KIT = _kp.KIT
TMP = f"{SP}/wlog/countfix_diff.json"
BACKUP = f"{SP}/wlog/countfix_backup"
CFG = f"{REPO}/config/usa/arm9"


def config_files(kind):
    out = [f"{CFG}/{kind}.txt", f"{CFG}/itcm/{kind}.txt", f"{CFG}/dtcm/{kind}.txt"]
    ovdir = f"{CFG}/overlays"
    out += [f"{ovdir}/{d}/{kind}.txt" for d in sorted(os.listdir(ovdir))]
    return [f for f in out if os.path.exists(f)]


def function_locations():
    where = {}
    for f in config_files("symbols"):
        for m in re.finditer(r"^(\S+) kind:function\(\S+\) addr:0x([0-9a-f]+)", open(f, encoding="utf-8").read(), re.M):
            where.setdefault(m.group(1), []).append((os.path.dirname(f), int(m.group(2), 16)))
    return where


def diff_unit(unit):
    if os.path.exists(TMP):
        os.remove(TMP)
    # objdiff ships under the platform's own name: objdiff-cli.exe on Windows, plain objdiff-cli on
    # Linux. Naming the wrong one raised FileNotFoundError, which both landing scripts swallow
    # (>> "$LOG" 2>&1), so countfix quietly stopped correcting counts and the build carried on.
    cli = "objdiff-cli.exe" if os.name == "nt" else "objdiff-cli"
    subprocess.run([f"{REPO}/{cli}", "diff", "-p", REPO, "-u", unit, "-o", TMP, "--format", "json"],
                   capture_output=True, cwd=REPO)
    if not os.path.exists(TMP):
        return []
    d = json.load(open(TMP))
    right = {s["name"]: s for s in d["right"]["symbols"] if "instructions" in s and s.get("name")}
    pairs = []
    for s in d["left"]["symbols"]:
        if "instructions" in s and s.get("match_percent", 100) < 100 and s.get("name") in right:
            pairs.append((s, right[s["name"]]))
    return pairs


def plan(units):
    where = function_locations()
    relocs, sizes = [], []
    for unit in units:
        for left, right in diff_unit(unit):
            name = left["name"]
            if len(where.get(name, [])) != 1:
                continue
            cfgdir, faddr = where[name][0]
            base = int(left.get("address", "0"))
            li, ri = left["instructions"], right["instructions"]
            if int(left["size"]) == int(right["size"]) + 2 and li[-1].get("diff_kind") == "DIFF_DELETE" \
                    and li[-1]["instruction"].get("formatted") == "lsl r0, #0x0":
                sizes.append((f"{cfgdir}/symbols.txt", name, int(left["size"]), int(right["size"])))
                li = li[:-1]
            for a, b in zip(li, ri):
                if a.get("diff_kind") != "DIFF_ARG_MISMATCH":
                    continue
                ra, ib = a["instruction"].get("relocation"), b["instruction"]
                word = re.match(r"^\.word (0x[0-9a-f]+)$", ib.get("formatted", ""))
                if ra and ra.get("type_name") == "R_ARM_ABS32" and not ib.get("relocation") and word:
                    frm = faddr + int(a["instruction"]["address"]) - base
                    relocs.append((f"{cfgdir}/relocs.txt", frm, int(word.group(1), 16), name))
    return relocs, sizes


def apply(relocs, sizes):
    texts = {}

    def text(path):
        if path not in texts:
            texts[path] = open(path, encoding="utf-8").read()
        return texts[path]

    for path, frm, value, name in relocs:
        pat = re.compile(r"^(from:0x%08x kind:load to:0x%08x module:)(?!none$)\S+$" % (frm, value), re.M)
        found = list(pat.finditer(text(path)))
        if len(found) != 1:
            print(f"countfix: no unique reloc {hex(frm)} -> {hex(value)} in {name}")
            continue
        m = found[0]
        texts[path] = texts[path][:m.start()] + m.group(1) + "none" + texts[path][m.end():]
        print(f"countfix: absolute pool word {hex(frm)} = {hex(value)} in {name}")
    for path, name, old, new in sizes:
        pat = re.compile(r"^(%s kind:function\(thumb,size=)0x%x(\S*\) addr:0x[0-9a-f]+)$" % (re.escape(name), old), re.M)
        found = list(pat.finditer(text(path)))
        if len(found) != 1:
            print(f"countfix: no unique thumb symbol {name}")
            continue
        m = found[0]
        texts[path] = texts[path][:m.start()] + m.group(1) + hex(new) + m.group(2) + texts[path][m.end():]
        print(f"countfix: thumb size {hex(old)} -> {hex(new)} for {name}")
    changed = [p for p, t in texts.items() if t != open(p, encoding="utf-8").read()]
    shutil.rmtree(BACKUP, ignore_errors=True)
    for p in changed:
        dst = f"{BACKUP}/{os.path.relpath(p, REPO)}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(p, dst)
        open(p, "w", encoding="utf-8", newline="").write(texts[p])
    return changed


def restore():
    for root, _, files in os.walk(BACKUP):
        for f in files:
            src = os.path.join(root, f)
            shutil.copyfile(src, os.path.join(REPO, os.path.relpath(src, BACKUP)))
            print(f"countfix: restored {os.path.relpath(src, BACKUP)}")


def landed_units(since=None):
    if since:
        out = subprocess.run(["git", "diff", "--name-only", "--diff-filter=AM", since, "HEAD", "--", "src/"],
                             capture_output=True, text=True, cwd=REPO).stdout
        paths = out.splitlines()
    else:
        out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "src/"],
                             capture_output=True, text=True, cwd=REPO).stdout
        paths = [line[3:] for line in out.splitlines()]
    known = {u["name"] for u in json.load(open(f"{REPO}/objdiff.json"))["units"]}
    units = []
    for path in paths:
        unit = os.path.splitext(path.strip())[0].replace("\\", "/")
        if unit in known:
            units.append(unit)
    return units


def report_units():
    report = json.load(open(f"{REPO}/build/usa/report.json"))
    return [u["name"] for u in report["units"] if u["metadata"].get("complete")
            and any(0 < f.get("fuzzy_match_percent", 0) < 100 for f in u.get("functions", []))]


def main():
    if "--restore" in sys.argv:
        restore()
        return 0
    since = next((a[len("--since="):] for a in sys.argv if a.startswith("--since=")), None)
    units = report_units() if "--report" in sys.argv else landed_units(since)
    relocs, sizes = plan(units)
    if "--dry-run" in sys.argv:
        for r in relocs:
            print("would mark absolute:", r[3], hex(r[1]), hex(r[2]))
        for s in sizes:
            print("would resize thumb:", s[1], hex(s[2]), "->", hex(s[3]))
        return 0
    changed = apply(relocs, sizes)
    print(f"countfix: {len(units)} units checked, {len(changed)} config files changed")
    return 3 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
