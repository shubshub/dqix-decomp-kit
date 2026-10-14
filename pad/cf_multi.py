import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.dirname(_kpos.path.abspath(__file__))))
import kitpaths as _kp
import json
import os
import sys

sys.path.insert(0, (_kp.KIT + "/frida"))
import colorforce as cf  # noqa: E402

src, mod, addr, size, pool_from = sys.argv[1], _kp.module_label(sys.argv[2]), int(sys.argv[3], 16), int(sys.argv[4], 0), int(sys.argv[5], 16)
cfg = json.loads(sys.argv[6])
cfg.setdefault("call", -1)
cfg.setdefault("kind", "multi")
R = cf.forcereal.rom(mod, addr, size)
out = os.path.splitext(src)[0] + ".cfm"
os.makedirs(out, exist_ok=True)
obj = out + "/multi.o"
ev = cf.run(src, cfg, obj)
bad, err = cf.score(obj, R, size)
calls = [e for e in ev if e.get("ev") == "color"]
with open(out + "/trace.json", "w") as fh:
    json.dump(calls, fh)
last = calls[-1]
print("err", err, "real", None if bad is None else len([b for b in bad if b < pool_from]),
      None if bad is None else ["0x%x" % b for b in bad if b < pool_from])
if "--order" in sys.argv:
    for ci, c in enumerate(calls[:-1]):
        print("call", ci, "spilled", [n[0] for n in c["nodes"] if n[1] == -1])
    for pos, n in enumerate(last["nodes"][:16]):
        print(pos, "idx", n[0], "reg", n[1], "nc", n[2])
