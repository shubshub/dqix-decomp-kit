"""Score candidate sources for /dqix-evolve: one JSON line per file.

    python evo_score.py <mod> <addr> <file> [<file> ...]

fitness 0 = MATCH. For main:02061c04 each jump-table case body is compared on its own (casediff),
so a candidate with the wrong total length still gets a per-site score and a site signature; for any
other function the wgate verdict gives the fitness and wdiff's differing runs give the signature.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.dirname(_kpos.path.abspath(__file__))))
import hashlib
import kitpaths as _kp
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
sys.path.insert(0, KIT + "/pad")

MOD, ADDR, FILES = sys.argv[1], sys.argv[2].lower(), sys.argv[3:]
BANNED = re.compile(r"(?m)^[ \t]*#[ \t]*pragma[ \t]+(?!define_section|section)\w.*$|always_inline|__attribute__\s*\(\(\s*noinline")
CASES = ADDR == "02061c04" and MOD == "main"
if CASES:
    import casediff
    ROM = casediff.rom_text()
    RB = casediff.bodies(ROM)


def norm(ins, raw):
    if raw == b"\0\0\0\0":
        return "POOLWORD"
    t = "%s %s" % (ins.mnemonic, ins.op_str)
    if ins.mnemonic.startswith("b") and "#" in ins.op_str and "[" not in ins.op_str:
        return ins.mnemonic
    return re.sub(r"\[pc, #-?0x[0-9a-f]+\]", "[pc]", t)


def words(buf, off, n, base):
    out = []
    for i in range(0, n, 4):
        raw = bytes(buf[off + i:off + i + 4])
        ins = list(casediff.MD.disasm(raw, base + i))
        out.append((norm(ins[0], raw) if ins else "DATA %s" % raw.hex(), raw))
    return out


def body_status(ours):
    ob = casediff.bodies(ours)
    bad = {}
    for case, (ro, rl) in RB.items():
        if case not in ob:
            bad[case] = ("missing", 0)
            continue
        oo, ol = ob[case]
        if ol != rl:
            bad[case] = ("len", (ol - rl) // 4)
            continue
        a, b = words(ROM, ro, rl, ro), words(ours, oo, ol, ro)
        n = sum(1 for (x, xr), (y, yr) in zip(a, b)
                if x != y and y != "POOLWORD" and not x.startswith("DATA"))
        if n:
            bad[case] = ("rows", n)
    return bad


def gate(path):
    r = subprocess.run([sys.executable, KIT + "/wgate.py", MOD, ADDR, path], cwd=REPO,
                       capture_output=True, text=True)
    lines = [l for l in (r.stdout + r.stderr).splitlines() if l.strip() and not l.startswith("HINT")]
    return lines[0] if lines else "NO OUTPUT"


def wgate_fitness(verdict):
    if verdict.startswith("MATCH") or " MATCH" in verdict[:12]:
        return 0
    m = re.match(r"RESIDUE ([\w-]+) (\d+)", verdict)
    if not m:
        return 99999
    cls, n = m.group(1), int(m.group(2))
    return 200 + 10 * n if cls in ("UNDERGEN", "OVERGEN", "SIZE") else n


MAXRUNS = 4096


def sites(path):
    r = subprocess.run([sys.executable, KIT + "/wdiff.py", MOD, ADDR, path], cwd=REPO,
                       capture_output=True, text=True,
                       env=dict(os.environ, WDIFF_CTX="0", WDIFF_MAXRUNS=str(MAXRUNS)))
    return sig_from_wdiff(r.stdout)


def sig_from_wdiff(out):
    runs = []
    seen = []
    for m in re.finditer(r"(?m)^\s*\*0x([0-9a-fA-F]+)\s(.*)$", out):
        off = int(m.group(1), 16)
        seen.append("%x %s" % (off, " ".join(m.group(2).split())))
        if runs and off - runs[-1][1] <= 0x10:
            runs[-1][1] = off
        else:
            runs.append([off, off])
    labels = ["%x" % a if a == b else "%x-%x" % (a, b) for a, b in runs]
    sig = ",".join(labels[:6]) + ("+%d" % (len(labels) - 6) if len(labels) > 6 else "")
    if seen:
        sig += "~" + hashlib.sha1("\n".join(seen).encode()).hexdigest()[:8]
    m = re.search(r"BYTEDIFF (\d+) bytes", out)
    return sig, int(m.group(1)) if m else None


def score(path):
    path = path.replace(chr(92), "/")
    if not os.path.isfile(path):
        return {"file": path, "fitness": 99999, "verdict": "MISSING", "match": False, "sig": "missing"}
    text = open(path, encoding="utf-8", errors="replace").read()
    banned = [m.group(0).strip() for m in BANNED.finditer(text)]
    if banned:
        return {"file": path, "fitness": 99999, "verdict": "BANNED " + "; ".join(banned[:3]),
                "match": False, "sig": "BANNED"}
    verdict = gate(path)
    res = {"file": path, "verdict": verdict[:160], "match": wgate_fitness(verdict) == 0}
    if res["match"]:
        res.update(fitness=0, sig="MATCH")
        return res
    if not CASES:
        cls = verdict.split()[1] if verdict.startswith("RESIDUE") else "ERR"
        where, nbytes = sites(path) if cls != "ERR" else ("", None)
        fit = wgate_fitness(verdict)
        if nbytes is not None and fit < 99999:
            size = re.match(r"RESIDUE (?:UNDERGEN|OVERGEN|SIZE) (\d+)", verdict)
            fit = nbytes + (10 * int(size.group(1)) if size else 0)
        res.update(fitness=fit, sig=cls + ("@" + where if where else ""))
        return _implausible(res, text)
    try:
        bad = body_status(casediff.our_text(path))
    except BaseException as e:
        res.update(fitness=99999, sig="COMPILE-ERROR", error=str(e)[:200])
        return res
    fit = 0
    parts = []
    for case in sorted(bad):
        kind, n = bad[case]
        fit += 100 + 10 * abs(n) if kind in ("len", "missing") else 4 * n
        parts.append("%02x:%s%+d" % (case, kind, n) if kind == "len" else "%02x:%s%d" % (case, kind, n))
    if not bad:
        fit = max(1, wgate_fitness(verdict))
    res.update(fitness=fit, sig="|".join(parts) or "bodies-clean", bad_cases=len(bad))
    return _implausible(res, text)


IMPLAUSIBLE_COST = 200


def _implausible(res, text):
    if res.get("match") or res.get("fitness", 0) >= 99999:
        return res
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import plausible
    found = plausible.findings(text)
    if found:
        res["fitness"] += IMPLAUSIBLE_COST * len(found)
        res["implausible"] = ["%s %s" % (k, w) for _l, k, w in found][:6]
    return res


if __name__ == "__main__":
    with ThreadPoolExecutor(min(6, max(1, len(FILES)))) as ex:
        for r in ex.map(score, FILES):
            print(json.dumps(r))
