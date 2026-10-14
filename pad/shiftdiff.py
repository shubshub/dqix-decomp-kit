"""Diff a function whose size is off by a few instructions: align ours to the ROM with an edit-distance
walk over instruction words (relocations masked) and print only the rows that do not match.

Usage: python shiftdiff.py <mod> <addr> <file.cpp>
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.dirname(_kpos.path.abspath(__file__))))
import kitpaths as _kp
import os
import re
import struct
import subprocess
import sys

import capstone
from elftools.elf.elffile import ELFFile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import buildcfg  # noqa: E402

REPO = _kp.REPO
mod, addr, src = sys.argv[1], int(sys.argv[2], 16), sys.argv[3]
mod = _kp.module_label(mod)
obj = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_shiftdiff_%d.o" % os.getpid())
r = subprocess.run(buildcfg.tool_command(buildcfg.cc_path(None)) + list(buildcfg.FLAGS) + ["-c", os.path.abspath(src), "-o", obj],
                   capture_output=True, text=True, cwd=REPO)
if r.returncode:
    print(r.stdout[-2000:] + r.stderr[-2000:])
    sys.exit(1)
with open(obj, "rb") as fh:
    e = ELFFile(fh)
    secs = [s for s in e.iter_sections() if s.name.startswith(".text") and s.data_size]
    ours = max(secs, key=lambda s: s.data_size).data()
os.remove(obj)
if mod == "main":
    cfg = f"{REPO}/config/usa/arm9/delinks.txt"
    blob = open(f"{REPO}/extract/usa/arm9/arm9.bin", "rb").read()
else:
    cfg = f"{REPO}/config/usa/arm9/overlays/{mod}/delinks.txt"
    blob = open(f"{REPO}/extract/usa/arm9_overlays/{mod}.bin", "rb").read()
base = min(int(x, 16) for x in re.findall(r"start:0x([0-9a-fA-F]+)", open(cfg).read()))
syms = open(cfg.replace("delinks.txt", "symbols.txt")).read()
m = re.search(r"addr:0x%08x\b.*" % addr, syms)
size = len(ours)
sm = re.search(r"kind:function\([^)]*size=0x([0-9a-f]+)[^)]*\) addr:0x%08x" % addr, syms)
if sm:
    size = int(sm.group(1), 16)
rom = blob[addr - base:addr - base + size]
md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)


def words(b):
    return list(struct.unpack("<%dI" % (len(b) // 4), b[:len(b) // 4 * 4]))


def key(w):
    if (w >> 24) & 0x0F in (0x0A, 0x0B):
        return w & 0xFF000000
    if (w & 0x0F7F0000) == 0x051F0000:
        return w & 0xFFFFF000
    return w


A, B = words(rom), words(ours)
n, m2 = len(A), len(B)
INF = 10 ** 9
dp = [[0] * (m2 + 1) for _ in range(n + 1)]
for i in range(n + 1):
    dp[i][m2] = n - i
for j in range(m2 + 1):
    dp[n][j] = m2 - j
for i in range(n - 1, -1, -1):
    for j in range(m2 - 1, -1, -1):
        dp[i][j] = min(dp[i + 1][j + 1] + (0 if key(A[i]) == key(B[j]) else 1), dp[i + 1][j] + 1, dp[i][j + 1] + 1)


def dis(w, at):
    x = list(md.disasm(struct.pack("<I", w), at))
    return ("%s %s" % (x[0].mnemonic, x[0].op_str)) if x else ".word 0x%08x" % w


i = j = 0
bad = 0
while i < n or j < m2:
    if i < n and j < m2 and dp[i][j] == dp[i + 1][j + 1] + (0 if key(A[i]) == key(B[j]) else 1):
        if key(A[i]) != key(B[j]):
            print(" *0x%04x  %-34s | 0x%04x %s" % (i * 4, dis(A[i], i * 4), j * 4, dis(B[j], j * 4)))
            bad += 1
        i += 1
        j += 1
    elif i < n and dp[i][j] == dp[i + 1][j] + 1:
        print(" -0x%04x  %-34s |" % (i * 4, dis(A[i], i * 4)))
        bad += 1
        i += 1
    else:
        print(" +        %-34s | 0x%04x %s" % ("", j * 4, dis(B[j], j * 4)))
        bad += 1
        j += 1
print("rows %d, rom 0x%x ours 0x%x" % (bad, len(rom), len(ours)))
