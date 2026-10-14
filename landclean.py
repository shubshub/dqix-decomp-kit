import os
import shutil
import subprocess
import sys

import kitpaths
import regionsync

sys.path.insert(0, f"{kitpaths.KIT}/pad")
from decomment import strip

KIT, SP = kitpaths.KIT, kitpaths.SP
CLEAN = f"{SP}/clean"
MANIFEST = f"{CLEAN}/manifest.tsv"
LANDED = f"{CLEAN}/landed.tsv"
REJECTED = f"{CLEAN}/rejected.tsv"
REGIONS = ("usa", "jpn", "eur")
USAGE = """bash landclean.sh [reviewed.txt]

With reviewed.txt (one repo path per line), only those rows land; the rest stay queued.
Lands rewritten sources of functions already on decomp-matching. $SP/clean/manifest.tsv lists
`<repo path>\\t<blob it replaces>\\t<why>`; $SP/clean/<repo path> is the rewrite. Under the wave lock,
from the published tip of the integration tree: drops a row whose file changed since its blob,
applies the rest, requires ninja check and sha1 in usa/jpn/eur, halves the batch until every part is
green, commits what stays green, publishes, and moves every row to landed.tsv or rejected.tsv.
Exit 0: landed or nothing to land. 1: refused."""


def run(cwd, *args):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def git(tree, *args):
    return run(tree, "git", *args)


def rows():
    out = []
    try:
        for line in open(MANIFEST, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[0]:
                out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    except OSError:
        pass
    return out


def split_stale(tree, entries):
    fresh, stale = [], []
    for rel, base, why in entries:
        current = git(tree, "rev-parse", f"HEAD:{rel}")[1].strip()
        missing = not os.path.isfile(f"{CLEAN}/{rel}")
        (stale if current != base or missing else fresh).append((rel, base, why))
    return fresh, stale


def settle(entries, check):
    if not entries or check(entries):
        return list(entries)
    if len(entries) == 1:
        return []
    half = len(entries) // 2
    return settle(entries[:half], check) + settle(entries[half:], check)


def green():
    for region in REGIONS:
        regionsync.configure(region)
        ok, _ = regionsync.green()
        if not ok:
            regionsync.configure("usa")
            return False
    regionsync.configure("usa")
    return True


def apply(tree, tip, entries):
    git(tree, "checkout", "-q", "-f", "--detach", tip)
    for rel, _, _ in entries:
        if rel.endswith((".c", ".cpp")):
            with open(f"{CLEAN}/{rel}", encoding="utf-8") as fh:
                text = strip(fh.read())
            with open(f"{tree}/{rel}", "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        else:
            shutil.copyfile(f"{CLEAN}/{rel}", f"{tree}/{rel}")


def write_rows(path, entries, mode):
    with open(path, mode, encoding="utf-8", newline="\n") as fh:
        fh.writelines(f"{rel}\t{base}\t{why}\n" for rel, base, why in entries)


def main():
    if len(sys.argv) > 2:
        sys.exit(USAGE)
    entries = rows()
    if len(sys.argv) == 2:
        reviewed = {ln.strip() for ln in open(sys.argv[1], encoding="utf-8") if ln.strip()}
        entries = [e for e in entries if e[0] in reviewed]
    if not entries:
        print("nothing to land")
        return 0
    code, out = run(KIT, sys.executable, f"{KIT}/integ_tree.py", "sync")
    if code:
        print(f"REFUSED: integ_tree.py sync: {out.strip()[-200:]}")
        return 1
    tree = out.strip().splitlines()[-1]
    os.chdir(tree)
    git(tree, "fetch", "-q", kitpaths.DECOMP_URL, kitpaths.DECOMP_BRANCH)
    tip = git(tree, "rev-parse", "FETCH_HEAD")[1].strip()
    git(tree, "checkout", "-q", "-f", "--detach", tip)
    fresh, stale = split_stale(tree, entries)
    for rel, _, _ in stale:
        print(f"STALE {rel}: changed on decomp-matching since it was rewritten; rewrite it again")

    def check(part):
        apply(tree, tip, part)
        return green()

    good = settle(fresh, check)
    red = [e for e in fresh if e not in good]
    for rel, _, _ in red:
        print(f"RED {rel}: a region does not build with it")
    apply(tree, tip, good)
    if good:
        git(tree, "add", "--", *[rel for rel, _, _ in good])
        if git(tree, "commit", "-q", "-m", f"Clean {len(good)} matched source(s)")[0]:
            print("REFUSED: nothing to commit")
            return 1
        code, out = run(KIT, sys.executable, f"{KIT}/integ_tree.py", "publish")
        if code not in (0, 2):
            git(tree, "checkout", "-q", "-f", "--detach", tip)
            print(f"REFUSED: integ_tree.py publish: {out.strip()[-300:]}")
            return 1
        write_rows(LANDED, good, "a")
    write_rows(REJECTED, stale + red, "a")
    done = set(entries)
    write_rows(MANIFEST, [e for e in rows() if e not in done], "w")
    print(f"LANDED {len(good)} of {len(entries)} at {git(tree, 'rev-parse', '--short', 'HEAD')[1].strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
