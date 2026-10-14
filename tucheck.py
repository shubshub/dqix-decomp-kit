"""Compile a multi-function translation unit and compare its whole .text against the ROM.

    python tucheck.py <main|NNN> <src.cpp> <start_hex> <end_hex>

Prints MATCH, or SIZE / BYTEDIFF with the differing offsets attributed to the function they fall in.
"""
import os
import re
import subprocess
import sys

from elftools.elf.elffile import ELFFile

import buildcfg

REPO = buildcfg.REPO
mod, src, lo, hi = sys.argv[1], os.path.abspath(sys.argv[2]), int(sys.argv[3], 16), int(sys.argv[4], 16)
cfg = "config/usa/arm9" + ("" if mod == "main" else f"/overlays/ov{mod}")
rom = f"{REPO}/extract/usa/arm9/arm9.bin" if mod == "main" else f"{REPO}/extract/usa/arm9_overlays/ov{mod}.bin"
delinks = open(f"{REPO}/{cfg}/delinks.txt", encoding="utf-8").read()
base = min(int(m.group(1), 16) for m in re.finditer(r"start:0x([0-9a-fA-F]+)\s+end:0x[0-9a-fA-F]+\s+kind:", delinks))
funcs = sorted((int(m.group(2), 16), m.group(1)) for m in re.finditer(
    r"(?m)^(\S+)\s+kind:function\([^)]*\)\s+addr:0x([0-9a-fA-F]+)",
    open(f"{REPO}/{cfg}/symbols.txt", encoding="utf-8").read()))

obj = f"{os.path.dirname(os.path.abspath(__file__))}/_tucheck_{os.getpid()}.o"
r = subprocess.run(buildcfg.tool_command(buildcfg.CC) + list(buildcfg.FLAGS) + ["-c", src, "-o", obj], cwd=REPO,
                   capture_output=True, text=True)
if r.returncode:
    sys.exit("COMPILE " + (r.stdout + r.stderr)[-1500:])
buildcfg.postcompile(obj)
with open(obj, "rb") as fh:
    elf = ELFFile(fh)
    secs = list(elf.iter_sections())
    texts = [i for i, s in enumerate(secs) if s.name == ".text"]
    data, masked = b"", set()
    for i in texts:
        for s in secs:
            if s.name in (".rel.text", ".rela.text") and s["sh_info"] == i:
                for rr in s.iter_relocations():
                    o = len(data) + (rr["r_offset"] & ~3)
                    masked.update(range(o, o + 4))
        data += secs[i].data()
    if len(data) != hi - lo:
        sys.exit(f"SIZE {[hex(secs[i]['sh_size']) for i in texts]} total 0x{len(data):x} want 0x{hi - lo:x}")
os.remove(obj)
orig = open(rom, "rb").read()[lo - base:hi - base]
diffs = [i for i in range(hi - lo) if i not in masked and data[i] != orig[i]]
if not diffs:
    print("MATCH")
    sys.exit(0)
per = {}
for d in diffs:
    name = max((f for f in funcs if f[0] <= lo + d), default=(0, "?"))
    per.setdefault(name[1], []).append(hex(lo + d - name[0]))
for n, offs in per.items():
    print(f"BYTEDIFF {n}: {len(offs)} bytes at {offs[:8]}")
sys.exit(1)
