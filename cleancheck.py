import re
import sys
from collections import Counter

import plausible

VOL = re.compile(r"\bvolatile\b")
USAGE = """python cleancheck.py <before> <after> <line,line,...>

Accept a rewrite that removes only invented constructs. The listed lines of <before> carry them.
Every other line of <before> that says volatile must still appear verbatim in <after>; <after> must
report no plausible.py kind <before> did not, no more findings, and either fewer blocking findings or
fewer volatile. Exit 0 accepted, 1 refused."""


def lines(path):
    return open(path, encoding="utf-8", errors="ignore").read().replace("\r\n", "\n").split("\n")


def check(before, after, private):
    keep = Counter(ln.strip() for n, ln in enumerate(before, 1) if VOL.search(ln) and n not in private)
    have = Counter(ln.strip() for ln in after)
    lost = [ln for ln, k in keep.items() if have[ln] < k]
    total = lambda text: sum(len(VOL.findall(ln)) for ln in text)
    return lost, total(before), total(after)


def kinds(text):
    return Counter(kind for _, kind, _ in plausible.findings("\n".join(text)))


def accepted(before, after, private):
    lost, was, now = check(before, after, private)
    kb, ka = kinds(before), kinds(after)
    new = sorted(set(ka) - set(kb))
    blocking = lambda counts: sum(n for kind, n in counts.items() if kind not in plausible.ADVISORY)
    fewer = blocking(ka) < blocking(kb) or now < was
    ok = not lost and not new and sum(ka.values()) <= sum(kb.values()) and fewer
    return ok, lost, new, (was, now), (sum(kb.values()), sum(ka.values()))


def main():
    if len(sys.argv) != 4:
        sys.exit(USAGE)
    private = {int(n) for n in sys.argv[3].split(",") if n.strip()}
    ok, lost, new, vol, found = accepted(lines(sys.argv[1]), lines(sys.argv[2]), private)
    for ln in lost:
        print(f"LOST {ln}")
    for kind in new:
        print(f"NEW {kind}")
    print(f"volatile {vol[0]} -> {vol[1]}, findings {found[0]} -> {found[1]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
