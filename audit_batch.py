#!/usr/bin/env python3
"""Verify a batch of addresses is free BEFORE anyone reserves it (SWARM-IDENTITY.md §3).

    python audit_batch.py <addr>...        audit these addresses
    python audit_batch.py --file f         audit the addresses in f, one per line or inside backticks
    python audit_batch.py --mine <name>    every address in the open kit issues stamped [<name>]
    python audit_batch.py --open           every address ANY open kit issue claims

Each of the five checks from §3 is one source of truth, and each one has bitten:

  1. still unmatched in the decomp checkout    local config is the fast answer and it goes stale
  2. not claimed in this state directory      claim.py arbitrates ITS OWN state and nothing else
  3. no open kit issue mentions it            the whole reason the protocol exists: one GitHub
                                               login, several swarms, gh cannot tell them apart
  4. no open decomp PR touches a file for it  an open pull request reserves its changed files
  5. not landed upstream since you picked     someone merged a PR matching it minutes ago

Exit 0 when every address is free, 1 when any is held -- with WHO holds it. Read-only: it never
writes, never claims, never opens an issue.

REPO defaults to ZevyaDev/dqix-decomp and KIT_REPO to ZevyaDev/dqix-decomp-kit. The repo pair and
the branch are kitpaths' business, not this script's.
"""
import json
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

KIT_REPO = os.environ.get("DQIX_KIT_REPO", "ZevyaDev/dqix-decomp-kit")
DECOMP_REPO = os.environ.get("DQIX_DECOMP_REPO_NAME", "ZevyaDev/dqix-decomp")
API = "https://api.github.com"
BRANCH = "decomp-matching"
# How much already-matched, already-SOURCED code must lie within this many bytes of a function for
# its neighbourhood to count as helpful. Proximity is what picks the order; it does not decide
# freeness.
NEIGHBOURHOOD = 0x400
ADDR8 = re.compile(r"(?<![0-9a-fA-F])0*([0-9a-fA-F]{8})(?![0-9a-fA-F])")


def norm(addr):
    """One spelling for an address: 8 lowercase hex digits, leading zeros kept.

    Everything that carries an address -- symbols.txt, an issue body, a filename, a wave list, a
    command line -- spells it differently, and a lookup that misses on a leading zero reports a held
    function as free.
    """
    try:
        return f"{int(str(addr).strip().lower().replace('0x', ''), 16) & 0xffffffff:08x}"
    except ValueError:
        return str(addr).lower()


def _get(url):
    """GET a GitHub API path, as JSON. None when it cannot be read -- never a guess.

    `gh api` first when it is authenticated: the unauthenticated API allows 60 requests an HOUR PER
    IP, and an audit issues a dozen calls, so several swarms sharing a machine exhaust it in minutes
    and every audit after that fails. With a token the limit is 5,000 an hour and shared per account,
    which is what several swarms on one login actually need.
    """
    path = url[len(API):]
    gh = shutil.which("gh")
    if gh and _gh_authed(gh):
        r = subprocess.run([gh, "api", "-H", "Accept: application/vnd.github+json", path],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            try:
                return json.loads(r.stdout)
            except ValueError:
                pass
        if "rate limit" in (r.stderr or "").lower():
            print("note  GitHub API rate limit reached; issue and PR checks are INCOMPLETE, not clean",
                  file=sys.stderr)
            return None
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "dqix-audit_batch"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # A rate-limited response is 403 with X-RateLimit-Remaining: 0. Testing the header for the
        # word "rate limit" can never match it -- the header holds a number -- so the first version of
        # this raised on the one failure it existed to survive.
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            print("note  GitHub API rate limit reached; issue and PR checks are INCOMPLETE, not clean",
                  file=sys.stderr)
            return None
        if e.code == 403:
            print(f"note  GitHub refused the request (403): {e.reason}; issue and PR checks are "
                  "INCOMPLETE, not clean", file=sys.stderr)
            return None
        raise
    except urllib.error.URLError as e:
        print(f"note  cannot reach GitHub ({e.reason}); issue and PR checks are INCOMPLETE, not clean",
              file=sys.stderr)
        return None


_GH_AUTHED = None


def _gh_authed(gh):
    global _GH_AUTHED
    if _GH_AUTHED is None:
        _GH_AUTHED = subprocess.run([gh, "auth", "status"], capture_output=True,
                                    text=True, timeout=30).returncode == 0
    return _GH_AUTHED


def open_issues():
    """[(number, author, title, body)] for every OPEN kit issue. Pull requests are not issues here."""
    out = []
    for page in (1, 2):
        data = _get(f"{API}/repos/{KIT_REPO}/issues?state=open&per_page=100&page={page}")
        if not data:
            return None
        out += [(i["number"], i["user"]["login"], i["title"], i.get("body") or "")
                for i in data if "pull_request" not in i]
        if len(data) < 100:
            break
    return out


def open_pr_files():
    """{number: (author, title, set(addresses-in-changed-filenames))} for open decomp pull requests."""
    out = {}
    prs = _get(f"{API}/repos/{DECOMP_REPO}/pulls?state=open&per_page=100")
    if not prs:
        return None
    for pr in prs:
        files = _get(f"{API}/repos/{DECOMP_REPO}/pulls/{pr['number']}/files?per_page=100") or []
        addrs = set()
        for f in files:
            addrs |= {norm(a) for a in ADDR8.findall(f["filename"])}
        out[pr["number"]] = (pr["user"]["login"], pr["title"], addrs)
    return out


def _configs():
    """[(mod, dir)] for every module the decomp config carries."""
    main = f"{_kp.REPO}/config/usa/arm9"
    out = [("main", main)]
    ov = f"{main}/overlays"
    if os.path.isdir(ov):
        out += [(d[2:], f"{ov}/{d}") for d in sorted(os.listdir(ov)) if re.fullmatch(r"ov\d+", d)]
    return out


_FUNCS = None
_DELINKS = {}


def funcs():
    """{address: (module, name)} for every function the decomp's symbols.txt declares.

    Also the filter that keeps a commit SHA from being read as an address: a wave list and a git
    reference are both runs of hex, and only one of them is a function.
    """
    global _FUNCS
    if _FUNCS is None:
        _FUNCS = {}
        for mod, cfg in _configs():
            try:
                sym = open(f"{cfg}/symbols.txt", encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for name, addr in re.findall(
                    r"(?m)^(\S+)\s+kind:function\([a-z]+,size=0x[0-9a-fA-F]+\)\s+addr:0x0*([0-9a-fA-F]+)",
                    sym, re.I):
                _FUNCS[norm(addr)] = (mod, name)
    return _FUNCS


def delinked():
    """{module: [(lo, hi)]} -- the ranges already landed from each module's delinks.txt."""
    if not _DELINKS:
        for mod, cfg in _configs():
            try:
                dl = open(f"{cfg}/delinks.txt", encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            _DELINKS[mod] = [(int(a, 16), int(b, 16)) for a, b in re.findall(
                r"(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+)\s*$", dl)]
    return _DELINKS


def local_status(addr):
    """-> (matched, module, name) from the decomp's own config: has this address been delinked?"""
    mod, name = funcs().get(norm(addr), (None, None))
    if mod is None:
        return False, None, None
    lo = int(addr, 16)
    return any(a <= lo < b for a, b in delinked().get(mod, [])), mod, name


def local_claims(addr):
    """Addresses this state directory has already handed out, which nobody else can see."""
    root = f"{_kp.SP}/claims"
    held = []
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if d.lower() == addr and os.path.isdir(f"{root}/{d}"):
            held.append(d)
    return held


def held_by(audits, pr_files, wanted, name=None):
    """[(who, why)] -- everything holding `wanted`, from the issue list and the open pull requests."""
    holders = []
    for number, author, title, body in audits or []:
        if name is not None and f"[{name}]" not in title:
            continue
        if wanted in {norm(a) for a in ADDR8.findall(body)}:
            holders.append((f"issue #{number} [{author}] {title}", "open kit issue"))
    for number, (author, title, addrs) in (pr_files or {}).items():
        if wanted in addrs:
            holders.append((f"PR #{number} [{author}] {title}", "open decomp pull request"))
    return holders


def addresses_in(text):
    """The 8-hex-digit tokens in `text` that are FUNCTIONS in the ROM's own table.

    A wave list and a commit SHA are both runs of hex digits, and reading one as the other is how an
    audit ends up reporting a reservation on `35d323d1`. The decomp's symbols.txt is the only thing
    that can tell them apart, and it is local and free.
    """
    return sorted({norm(a) for a in ADDR8.findall(text) if norm(a) in funcs()})


def upstream_position():
    """(head, commits-behind-upstream) for the decomp checkout, or None when that cannot be read.

    Per-address freeness is check 1, which reads the checkout. This says whether the checkout is
    even current enough to be evidence: an address another swarm merged five minutes ago reads FREE
    here until the fetch happens, and that is the one way check 1 lies.
    """
    r = subprocess.run(["git", "-C", _kp.REPO, "fetch", "-q", "zevya", BRANCH],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    head = subprocess.run(["git", "-C", _kp.REPO, "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    behind = subprocess.run(["git", "-C", _kp.REPO, "rev-list", "--count", f"HEAD..zevya/{BRANCH}"],
                            capture_output=True, text=True).stdout.strip() or "0"
    return head, behind


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("addrs", nargs="*", help="8 hex digits, no 0x")
    ap.add_argument("--file", help="read addresses from a file (one per line, or in backticks)")
    ap.add_argument("--mine", metavar="NAME", help="every address in issues stamped [NAME]")
    ap.add_argument("--open", action="store_true", help="every address ANY open kit issue claims")
    ap.add_argument("--quiet", action="store_true", help="only print failures")
    args = ap.parse_args()

    wanted = {norm(a) for a in args.addrs}
    if args.file:
        wanted |= addresses_in(open(args.file, encoding="utf-8", errors="ignore").read())
    audits = open_issues()
    pr_files = open_pr_files()

    if args.mine:
        mine = [(n, u, t, b) for n, u, t, b in (audits or []) if f"[{args.mine}]" in t]
        wanted |= set().union(*[set(addresses_in(b)) for _, _, _, b in mine]) if mine else set()
        print(f"{len(mine)} open issue(s) stamped [{args.mine}]: "
              + ", ".join(f"#{n} {t[:50]}" for n, _, t, _ in mine))
    if args.open:
        allheld = {}
        for n, u, t, b in audits or []:
            for a in addresses_in(b):
                allheld.setdefault(a, []).append(f"#{n} [{u}] {t}")
        for a in sorted(allheld):
            print(f"{a}  {'; '.join(allheld[a])}")
        return 0
    if not wanted:
        ap.exit(2, "nothing to audit: give addresses, --file, --mine NAME or --open")

    bad = 0
    for addr in sorted(wanted):
        why = []
        matched, mod, name = local_status(addr)
        if mod is None:
            why.append("not a function in this decomp checkout's symbols.txt -- wrong address, or a "
                       "checkout that is behind the reservation")
        if matched:
            why.append(f"already delinked in the decomp checkout ({name or 'unnamed'}, {mod})")
        claimed = local_claims(addr)
        if claimed:
            why.append(f"claimed in this state directory: claims/{claimed[0]}")
        if addr in claim.blocked_addrs():
            why.append("blocked with no new lever since (blocker.py)")
        for who, why_kind in held_by(audits, pr_files, addr):
            why.append(f"{why_kind}: {who}")
        if why:
            bad += 1
            print(f"HELD  {addr}  ({name or '?'}{' in ' + mod if mod else ''})")
            for w in why:
                print(f"        {w}")
        elif not args.quiet:
            print(f"FREE  {addr}  ({name or '?'}{' in ' + mod if mod else ''})")

    pos = upstream_position()
    if pos is None:
        print("note  upstream decomp-matching could NOT be fetched; this checkout may be stale, and a "
              "stale checkout reads an address another swarm just merged as FREE")
    elif pos[1] != "0":
        print(f"note  decomp checkout {pos[0]} is {pos[1]} commit(s) behind upstream decomp-matching; "
              f"fast-forward before trusting a FREE")
    print(f"\n{len(wanted) - bad} free, {bad} held")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    import claim  # noqa: E402  (needs $SP on disk; only used for the blocked-with-no-lever check)
    main()