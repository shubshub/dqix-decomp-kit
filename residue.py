"""Classify a candidate's residue into one fixed class.

    python residue.py <main|NNN> <addr> <file.cpp>

Classes: MATCH NO-COMPILE OVERGEN UNDERGEN LOOP-SHAPE REGPERM SCHED OPERAND SHAPE NO-ARTIFACT
"""
import re

CLASSES = ("MATCH", "NO-COMPILE", "OVERGEN", "UNDERGEN", "LOOP-SHAPE", "REGPERM",
           "SCHED", "OPERAND", "SHAPE", "NO-ARTIFACT", "RELOC-WRONG", "UNKNOWN")

RESIDUE_LINE = re.compile(r"^RESIDUE\s+(\S+)\s+(-?\d+)\s*(.*)$", re.M)
HEADLINE = re.compile(r"^(?:MATCH|RESIDUE \S+ -?\d+)", re.M)


def parse_verdict(text):
    hit = RESIDUE_LINE.search(text)
    if hit:
        return hit.group(1), int(hit.group(2)), hit.group(3).strip()
    lines = [l for l in text.replace("\r", "").split("\n") if l.strip()]
    if lines and lines[-1].strip() == "MATCH":
        return "MATCH", 0, ""
    return "UNKNOWN", -1, (lines[-1][:160] if lines else "no output")


def headline(text):
    m = HEADLINE.search(text)
    return m.group(0) if m else "?"


_REG = re.compile(r"\b(?:r\d+|sb|sl|fp|ip|lr|sp|pc)\b")
_BRANCH = re.compile(r"^b(?:l|x|lx)?(?:eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?$")
_CALLEE_SAVED = re.compile(r"\b(?:r[4-9]|r1[01]|sl|fp)\b")
# blt/ble/bls/blo are B+condition; bleq/blls are BL+condition -- startswith("bl") eats back-edges
_LINK = re.compile(r"^blx?(?:eq|ne|cs|hs|cc|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al)?$")


def is_link(text):
    return bool(_LINK.match(text.split(" ")[0]))


def norm(text):
    return _REG.sub("R", text)


def is_branch(text):
    return bool(_BRANCH.match(text.split(" ")[0]))


def decode(buf, isa):
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
    step = 2 if isa == "thumb" else 4
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB if isa == "thumb" else CS_MODE_ARM)
    out = {}
    pos = 0
    while pos + step <= len(buf):
        advanced = False
        for ins in md.disasm(buf[pos:], pos):
            out[ins.address] = ("%s %s" % (ins.mnemonic, ins.op_str)).strip()
            pos = ins.address + ins.size
            advanced = True
        if not advanced:
            out[pos] = ".word 0x" + buf[pos:pos + step][::-1].hex()
            pos += step
    for off in range(0, len(buf) - step + 1, step):
        out.setdefault(off, ".word 0x" + buf[off:off + step][::-1].hex())
    return out


def _regmap(target, mine):
    pairs = []
    for off in sorted(target):
        tt, mm = target[off], mine.get(off, "")
        for a, b in zip(_REG.findall(tt), _REG.findall(mm)):
            if a != b and (a, b) not in pairs:
                pairs.append((a, b))
    return " ".join("%s->%s" % p for p in pairs[:6])


_TARGET = re.compile(r"#(-?0[xX][0-9a-fA-F]+|-?\d+)\s*$")


def _uniform_shift(target, mine, offsets):
    """True when every differing branch target moved by the SAME amount.

    One extra or missing instruction slides every later target by a constant. That is a length
    difference wearing a branch's clothes, and calling it a loop shape sends the reader looking for
    a restructured loop that is not there -- the real difference is whatever changed the length.
    """
    if len(offsets) < 2:
        return False
    deltas = set()
    for off in offsets:
        t, m = _TARGET.search(target[off]), _TARGET.search(mine.get(off, ""))
        if not t or not m:
            return False
        deltas.add(int(t.group(1), 0) - int(m.group(1), 0))
    return len(deltas) == 1 and 0 not in deltas


def classify(orig, mine, isa, reloc, slot, total=None):
    """-> (cls, metric, detail, hint)"""
    if total is not None and total != slot:
        ok = (slot, slot - 2) if isa == "thumb" else (slot,)
        if total not in ok:
            cls = "OVERGEN" if total > slot else "UNDERGEN"
            return cls, abs(total - slot), "0x%x vs slot 0x%x" % (total, slot), ""
    step = 2 if isa == "thumb" else 4
    diffs = [i for i in range(min(len(mine), len(orig)))
             if i not in reloc and mine[i] != orig[i]]
    if not diffs:
        return "MATCH", 0, "", ""
    target, ours = decode(orig, isa), decode(mine, isa)
    bad = sorted({(i // step) * step for i in diffs})
    tb = {o: target.get(o, "") for o in bad}
    mb = {o: ours.get(o, "") for o in bad}
    metric = len(diffs)

    branchy = [o for o in tb
               if is_branch(tb[o]) and not is_link(tb[o])
               and tb[o].split(" ")[0] == mb.get(o, "").split(" ")[0]
               and tb[o].split(" ", 1)[-1] != mb.get(o, "").split(" ", 1)[-1]]
    if branchy and not _uniform_shift(tb, mb, branchy):
        return ("LOOP-SHAPE", metric,
                "%d branch target(s) differ, first at 0x%x" % (len(branchy), branchy[0]), "")

    mnem_same = all(tb[o].split(" ")[0] == mb.get(o, "").split(" ")[0] for o in tb)
    shape_same = all(norm(tb[o]) == norm(mb.get(o, "")) for o in tb)
    if mnem_same and shape_same:
        callee_saved = any(_CALLEE_SAVED.search(tb[o]) for o in tb)
        hint = ("callee-saved: define the value LATER or hoist a DECLARATION (recipe #9)"
                if callee_saved else
                "scratch only: declaration order is inert, change WHEN it is first used (recipe #15)")
        return "REGPERM", metric, _regmap(tb, mb), hint

    if sorted(norm(v) for v in tb.values()) == sorted(norm(v) for v in mb.values()):
        return ("SCHED", metric,
                "same instructions, different order over %d site(s)" % len(bad), "")

    if not mnem_same:
        wrong = [(hex(o), tb[o], mb.get(o, "")) for o in bad
                 if tb[o].split(" ")[0] != mb.get(o, "").split(" ")[0]]
        return ("SHAPE", metric,
                "; ".join("%s %s | %s" % w for w in wrong[:3]), "")
    return "OPERAND", metric, "; ".join("%s %s | %s" % (hex(o), tb[o], mb.get(o, ""))
                                        for o in bad[:3]), ""


def _cli():
    import os
    import subprocess
    import sys
    sp = os.path.dirname(os.path.abspath(__file__))
    mod, addr, src = sys.argv[1], sys.argv[2], sys.argv[3]
    out = subprocess.run([sys.executable, f"{sp}/blocker.py", mod, addr, src, "--print"],
                         capture_output=True, text=True)
    sys.stdout.write(out.stdout or out.stderr)


if __name__ == "__main__":
    _cli()
