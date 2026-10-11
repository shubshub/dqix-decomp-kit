"""Land contributor pull requests on decomp-matching. Run through landpr.sh, which holds the wave lock.

    bash landpr.sh <number>...

In the integration tree, from the published tip: merges each pull request head as `Merge #N` (a
conflict only in delinks.txt, where both sides added blocks, keeps both), refuses symbolic links, strips
comments from the source it changed, requires `prready.py decomp` READY, ports between the regions
(regionsync.py), requires `ninja check` and `ninja sha1` in every region, refuses a head that moved
since it was fetched, then publishes. regionsync prints `NOT IN USA <file>: <why>` for each file another
region delinks that it could not wire into config/usa.
Exit 0: landed. 1: refused, nothing published. 2: usage.
"""
import os
import subprocess
import sys

import kitpaths

sys.path.insert(0, f"{kitpaths.KIT}/pad")
from decomment import strip
import regionsync

KIT, URL, BRANCH = kitpaths.KIT, kitpaths.DECOMP_URL, kitpaths.DECOMP_BRANCH
REGIONS = ("usa", "jpn", "eur")


def run(cwd, *args, env=None):
    r = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    return r.returncode, r.stdout + r.stderr


def git(tree, *args):
    return run(tree, "git", *args)


def refuse(message):
    print(f"REFUSED: {message}")
    sys.exit(1)


def fetch(tree, ref):
    code, out = git(tree, "fetch", "-q", URL, ref)
    if code:
        refuse(f"git fetch {ref}: {out.strip()[-200:]}")
    return git(tree, "rev-parse", "FETCH_HEAD")[1].strip()


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


def main():
    numbers = sys.argv[1:]
    if not numbers or not all(n.isdigit() for n in numbers):
        sys.exit(__doc__)
    code, out = run(KIT, sys.executable, f"{KIT}/integ_tree.py", "sync")
    if code:
        refuse(f"integ_tree.py sync: {out.strip()[-200:]}")
    tree = out.strip().splitlines()[-1]
    os.chdir(tree)
    tip = fetch(tree, BRANCH)
    git(tree, "checkout", "-q", "-f", "--detach", tip)
    heads = {n: fetch(tree, f"pull/{n}/head") for n in numbers}
    for n, head in heads.items():
        links = symlinks(tree, tip, head)
        if links:
            refuse(f"#{n} adds symbolic links: {', '.join(links)}")
    for n, head in heads.items():
        code, out = git(tree, "merge", "--no-ff", "-m", f"Merge #{n}", head)
        if code and not keep_both_delinks(tree, head):
            conflicts = git(tree, "diff", "--name-only", "--diff-filter=U")[1].split()
            git(tree, "merge", "--abort")
            git(tree, "checkout", "-q", "-f", "--detach", tip)
            refuse(f"#{n} conflicts in {', '.join(conflicts) or out.strip()[-200:]}; resolve it by hand")
    strip_comments(tree, tip)
    code, out = run(KIT, sys.executable, f"{KIT}/prready.py", "decomp", env=dict(os.environ, DQIX_REPO=tree))
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
