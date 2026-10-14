"""Land contributor pull requests on decomp-matching. Run through landpr.sh, which holds the wave lock.

    bash landpr.sh [--fixes DIR] <number>...

--fixes DIR overlays DIR's files (repo-relative paths, e.g. our cleaned versions of a pull request's flagged
sources) on the merged tree and commits them as `Clean #N` before prready.py runs. DIR/DROP lists repo paths
still implausible: each is removed with its delinks blocks (`Hold ... from #N`) and waits for cleanwork.
A merge conflict in a file DIR replaces is resolved to DIR's version. A changed path outside
src/, include/ and config/ lands only when DIR supplies it, so nothing a contributor wrote runs here.
In the integration tree, from the published tip: merges each pull request head as `Merge #N` (a
conflict only in delinks.txt, where both sides added blocks, keeps both), refuses symbolic links, strips
comments from the source it changed, requires `prready.py decomp` READY, ports between the regions
(regionsync.py), requires `ninja check` and `ninja sha1` in every region, refuses a head that moved
since it was fetched, then publishes. regionsync prints `NOT IN USA <file>: <why>` for each file another
region delinks that it could not wire into config/usa.
Exit 0: landed. 1: refused, nothing published. 2: usage.
"""
import os
import re
import subprocess
import sys

import kitpaths

sys.path.insert(0, f"{kitpaths.KIT}/pad")
from decomment import strip
import regionsync

KIT, URL, BRANCH = kitpaths.KIT, kitpaths.DECOMP_URL, kitpaths.DECOMP_BRANCH
REGIONS = ("usa", "jpn", "eur")
UNDO = []


def run(cwd, *args, env=None):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return r.returncode, r.stdout + r.stderr


def git(tree, *args):
    return run(tree, "git", *args)


def refuse(message):
    for tree, tip in UNDO:
        git(tree, "merge", "--abort")
        git(tree, "checkout", "-q", "-f", "--detach", tip)
    print(f"REFUSED: {message}")
    sys.exit(1)


def fetch(tree, ref):
    code, out = git(tree, "fetch", "-q", URL, ref)
    if code:
        refuse(f"git fetch {ref}: {out.strip()[-200:]}")
    return git(tree, "rev-parse", "FETCH_HEAD")[1].strip()


def take_fixes(tree, fixes):
    for path in git(tree, "diff", "--name-only", "--diff-filter=U")[1].split():
        src = os.path.join(fixes, path)
        if os.path.isfile(src):
            with open(src, encoding="utf-8") as fh, open(f"{tree}/{path}", "w", encoding="utf-8", newline="\n") as out:
                out.write(fh.read())
            git(tree, "add", "--", path)
    if git(tree, "diff", "--name-only", "--diff-filter=U")[1].split():
        return False
    return git(tree, "commit", "-q", "--no-edit")[0] == 0


def keep_both_delinks(tree, head):
    conflicts = git(tree, "diff", "--name-only", "--diff-filter=U")[1].split()
    if not conflicts or not all(p.endswith("delinks.txt") for p in conflicts):
        return False
    base = git(tree, "merge-base", "HEAD", head)[1].strip()
    for path in conflicts:
        ours, theirs, old = (dict(regionsync.BLOCK.findall(git(tree, "show", f"{rev}:{path}")[1]))
                             for rev in ("HEAD", head, base))
        if any(src in old and old[src] != body for src, body in theirs.items()) or not old.keys() <= theirs.keys():
            return False
        merged = git(tree, "show", f"HEAD:{path}")[1].rstrip("\n")
        for src, body in theirs.items():
            if src not in old and src not in ours:
                merged += f"\n\n{src}:\n{body.rstrip(chr(10))}"
        with open(f"{tree}/{path}", "w", encoding="utf-8") as fh:
            fh.write(merged + "\n")
        git(tree, "add", "--", path)
    return git(tree, "commit", "-q", "--no-edit")[0] == 0


def symlinks(tree, tip, head):
    raw = git(tree, "diff", "--raw", "--no-renames", tip, head)[1].splitlines()
    return [line.split("\t", 1)[1] for line in raw if line.split()[1] == "120000"]


def strip_comments(tree, tip):
    changed = git(tree, "diff", "--name-only", tip, "HEAD", "--", "src")[1].split()
    for path in changed:
        full = f"{tree}/{path}"
        if path.endswith((".c", ".cpp")) and os.path.isfile(full):
            with open(full, encoding="utf-8") as fh:
                text = fh.read()
            if strip(text) != text:
                with open(full, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(strip(text))
    if git(tree, "status", "--porcelain", "--", "src")[1].strip():
        git(tree, "add", "--", "src")
        git(tree, "commit", "-q", "-m", "Strip contribution comments")


def build_every_region(tree):
    for region in REGIONS:
        if not os.path.isdir(f"{tree}/extract/{region}"):
            refuse(f"no extracted {region} ROM in {tree}; every region must build before a landing")
        regionsync.configure(region)
        ok, log = regionsync.green()
        if not ok:
            regionsync.configure("usa")
            refuse(f"{region} is red:\n" + "\n".join(log.strip().splitlines()[-8:]))
    regionsync.configure("usa")


def drop_files(tree, paths, numbers):
    for rel in paths:
        if not os.path.exists(f"{tree}/{rel}"):
            refuse(f"--fixes DROP {rel}: no such file in the merged tree")
        git(tree, "rm", "-q", "-f", "--", rel)
        for cfg in git(tree, "ls-files", "--", "config")[1].split():
            if not cfg.endswith("delinks.txt"):
                continue
            path = f"{tree}/{cfg}"
            text = open(path, encoding="utf-8").read()
            kept = regionsync.BLOCK.sub(lambda m: "" if m.group(1) == rel else m.group(0), text)
            if kept != text:
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(re.sub(r"\n{3,}", "\n\n", kept))
                git(tree, "add", "--", cfg)
    if paths:
        git(tree, "commit", "-q", "-m", "Hold %d implausible file(s) from %s" % (len(paths), " ".join("#" + n for n in numbers)))


def apply_fixes(tree, fixes, numbers):
    drop = os.path.join(fixes, "DROP")
    if os.path.exists(drop):
        drop_files(tree, [ln.strip() for ln in open(drop, encoding="utf-8") if ln.strip()], numbers)
    written = []
    for root, _, files in os.walk(fixes):
        for name in files:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, fixes).replace("\\", "/")
            if rel == "DROP":
                continue
            if not os.path.exists(f"{tree}/{rel}"):
                refuse(f"--fixes {rel}: no such file in the merged tree")
            with open(src, encoding="utf-8") as fh, open(f"{tree}/{rel}", "w", encoding="utf-8", newline="\n") as out:
                out.write(fh.read())
            written.append(rel)
    if written and git(tree, "status", "--porcelain", "--", *written)[1].strip():
        git(tree, "add", "--", *written)
        git(tree, "commit", "-q", "-m", "Clean " + " ".join("#" + n for n in numbers))
    return written


def main():
    args = sys.argv[1:]
    fixes = None
    if "--fixes" in args:
        i = args.index("--fixes")
        fixes = args[i + 1] if i + 1 < len(args) else ""
        args = args[:i] + args[i + 2:]
        if not os.path.isdir(fixes or ""):
            sys.exit(f"--fixes {fixes!r}: not a directory")
    numbers = args
    if not numbers or not all(n.isdigit() for n in numbers):
        sys.exit(__doc__)
    code, out = run(KIT, sys.executable, f"{KIT}/integ_tree.py", "sync")
    if code:
        refuse(f"integ_tree.py sync: {out.strip()[-200:]}")
    tree = out.strip().splitlines()[-1]
    os.chdir(tree)
    tip = fetch(tree, BRANCH)
    git(tree, "checkout", "-q", "-f", "--detach", tip)
    UNDO.append((tree, tip))
    heads = {n: fetch(tree, f"pull/{n}/head") for n in numbers}
    for n, head in heads.items():
        links = symlinks(tree, tip, head)
        if links:
            refuse(f"#{n} adds symbolic links: {', '.join(links)}")
    for n, head in heads.items():
        code, out = git(tree, "merge", "--no-ff", "-m", f"Merge #{n}", head)
        if code and not (fixes and take_fixes(tree, fixes)) and not keep_both_delinks(tree, head):
            conflicts = git(tree, "diff", "--name-only", "--diff-filter=U")[1].split()
            refuse(f"#{n} conflicts in {', '.join(conflicts) or out.strip()[-200:]}; resolve it by hand")
    reviewed = apply_fixes(tree, fixes, numbers) if fixes else []
    strip_comments(tree, tip)
    code, out = run(KIT, sys.executable, f"{KIT}/prready.py", "decomp",
                    env=dict(os.environ, DQIX_REPO=tree, PRREADY_REVIEWED=os.pathsep.join(reviewed)))
    if code:
        refuse(f"prready.py decomp on the merged tree:\n{out.strip()}")
    print(run(tree, sys.executable, f"{KIT}/regionsync.py", tree)[1].strip())
    build_every_region(tree)
    for n, head in heads.items():
        if fetch(tree, f"pull/{n}/head") != head:
            refuse(f"#{n} moved after it was fetched; run landpr.sh again")
    code, out = run(KIT, sys.executable, f"{KIT}/integ_tree.py", "publish")
    if code not in (0, 2):
        refuse(f"integ_tree.py publish: {out.strip()[-300:]}")
    print(f"LANDED {' '.join('#' + n for n in numbers)} at {git(tree, 'rev-parse', '--short', 'HEAD')[1].strip()}")
    if run(tree, "ninja", "report")[0] or run(KIT, sys.executable, f"{KIT}/integ_tree.py", "report")[0]:
        print("WARN ninja report failed -- cov.py is stale until the next wave")


if __name__ == "__main__":
    main()
