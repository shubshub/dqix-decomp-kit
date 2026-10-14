"""Re-gate every parked candidate on the project compiler, repairing the two
faults that park correct work.

Most parked sources are not wrong codegen. They fail for mechanical reasons the
tools now know how to fix:

  * UNDEF-SYM -- a callee declared under a name that was never committed.
    fixundef.py resolves it from the address and re-gates.
  * `.init total=0x0` -- a function that lives in .init compiled without
    `#pragma define_section initcode`, so it emits no code in the right section.

For each candidate: try wgate as-is, then apply whichever repair the verdict
calls for, and report what ends up MATCHing. Writes a report the caller can act
on; makes no change to the repository.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import os, re, shutil, subprocess, sys, glob, hashlib

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
# SHARDING. A full pass is ~36 s per candidate and the pool is >1200, so one process needs half a
# day and the machine sits at one core. REPAIR_SHARD="i/n" takes every n-th job starting at i (1
# based), so n processes cover the pool with no overlap. Every path this run writes gets the shard
# suffix, or shards clobber each other's work copies and reports.
_sh = os.environ.get("REPAIR_SHARD", "")
SHARD, NSHARD = (int(_sh.split("/")[0]), int(_sh.split("/")[1])) if "/" in _sh else (1, 1)
TAG = "" if NSHARD == 1 else f"_{SHARD}of{NSHARD}"
WORK = f"{SP}/repair_work{TAG}"
os.makedirs(WORK, exist_ok=True)

sym = {}
for p in [f"{REPO}/config/usa/arm9/symbols.txt"] + \
         sorted(glob.glob(f"{REPO}/config/usa/arm9/overlays/*/symbols.txt")):
    mod = "main" if "overlays" not in p else re.search(r'ov(\d+)', p).group(1)
    for m in re.finditer(r'^\S+ kind:function\(\w+,size=0x[0-9a-fA-F]+\) addr:0x([0-9a-fA-F]{8})',
                         open(p, encoding="utf-8", errors="ignore").read(), re.M):
        sym.setdefault(m.group(1).lower(), mod)

done = set()
ranges = []
for p in [f"{REPO}/config/usa/arm9/delinks.txt"] + \
         sorted(glob.glob(f"{REPO}/config/usa/arm9/overlays/*/delinks.txt")):
    txt = open(p, encoding="utf-8").read()
    for a, b in re.findall(r'(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+)\s*$', txt):
        done.add(a.lower())
        ranges.append((int(a, 16), int(b, 16)))
# A delinked entry covers a RANGE, and one .cpp routinely holds several functions.
# Matching only range STARTS re-offers work that is already committed: it gates
# byte-exact and then fails to link under a name that no longer exists, which reads
# like a mysterious failure rather than "this is finished".
for a in list(sym):
    v = int(a, 16)
    if any(lo <= v < hi for lo, hi in ranges):
        done.add(a)

# A skiplisted address is a decided question -- hand asm the placement policy refuses, or a measured
# dead end. Re-gating it produces a HIT the integrator then discards, which reads as a free match
# and is not one.
for p in (f"{KIT}/skiplist_main.txt", f"{KIT}/skiplist_ov.txt"):
    if os.path.exists(p):
        for line in open(p, encoding="utf-8", errors="ignore"):
            m = re.match(r'\s*([0-9a-fA-F]{8})\b', line)
            if m:
                done.add(m.group(1).lower())

# gated/* is wgate's own MATCH archive; wave_*/w*/fin* are per-wave work directories. Neither was
# ever re-gated, so 23 candidate sources and every gated file sat outside the sweep. The job filter
# below keeps only <8hex>.cpp basenames, so experiment scratch in those directories is ignored.
# clsbest/ carries each address's best sub-MATCH artifact forward, so a later pass searches from it
# instead of re-deriving it: without it every pass restarts at the same base and depth can never
# compound past COLORSWEEP_DEPTH no matter how many passes run.
BEST = f"{SP}/clsbest"
os.makedirs(BEST, exist_ok=True)
pools = sorted(set(glob.glob(f"{SP}/hold_*") + glob.glob(f"{SP}/*_stage") +
                   glob.glob(f"{SP}/staging/*") + glob.glob(f"{SP}/*_reclaim") +
                   glob.glob(f"{SP}/gated/*") + glob.glob(f"{SP}/wave_*") +
                   glob.glob(f"{SP}/wmain") + glob.glob(f"{SP}/fin*") +
                   glob.glob(f"{SP}/clswork_*") + glob.glob(f"{SP}/aswork_*") +
                   [f"{SP}/quarantine", f"{SP}/handwork", f"{SP}/attempts", BEST]))
# clswork_<pid>/c.cpp are colorsweep work copies orphaned by dead sessions, and four of them were
# the BEST surviving attempt at an unlanded address (031:0223a374 at BYTEDIFF 5). They were outside
# every sweep twice over: the directory was not a pool name, and the file is called `c.cpp`, which
# the 8-hex basename filter below drops. Hence the tag fallback.
jobs, seen = [], set()
for d in pools:
    for f in sorted(glob.glob(f"{d}/*.cpp")):
        m = re.search(r'([0-9a-fA-F]{8})\.cpp$', os.path.basename(f))
        if not m:
            try:
                m = re.search(r'// USA: func_(?:ov\d+_)?([0-9a-fA-F]{8})',
                              open(f, encoding="utf-8", errors="ignore").read())
            except OSError:
                m = None
        if not m:
            continue
        addr = m.group(1).lower()
        if addr in done or addr not in sym:
            continue
        h = hashlib.md5(open(f, 'rb').read()).hexdigest()
        if (addr, h) in seen:
            continue
        seen.add((addr, h))
        jobs.append((sym[addr], addr, f))
_total = len(jobs)
# SHARD BY ADDRESS, NOT BY POSITION. The pool holds several copies of the same address -- a stage
# file, a quarantine file, a handwork attempt -- and round-robin by position handed those copies to
# DIFFERENT shards, so all four processes swept the same function at once. On 2026-09-06 every shard
# spent 20-38 minutes on the four parked copies of main:02061c04, the largest file in the pool.
# HASH the address; do not take it modulo. Every ARM address is word-aligned, so `addr % 4` is 0 for
# all of them and shard 1 got all 248 candidates while shards 2-4 got none.
_only = {a.lower() for a in os.environ.get("REPAIR_ONLY", "").replace(",", " ").split() if a}
if _only:
    NSHARD, SHARD = 1, 1
    jobs = [j for j in jobs if j[1] in _only]
jobs = [j for j in jobs
        if int(hashlib.md5(j[1].encode()).hexdigest(), 16) % NSHARD == SHARD - 1]
# Addresses nothing mechanical can close. 02061c04 is parked at BYTEDIFF 27 on two case bodies that
# survived ~1000 source forms; at 10204 bytes its sweep alone costs hours of the pass.
_skip = f"{SP}/sweep_skip.txt"
if os.path.exists(_skip):
    _no = {l.split("#")[0].strip().lower() for l in open(_skip, encoding="utf-8") if l.strip()}
    jobs = [j for j in jobs if j[1] not in _no]
print(f"{len(jobs)} unique parked candidates"
      + (f" (shard {SHARD}/{NSHARD} of {_total})" if NSHARD > 1 else ""), flush=True)


def gate(mod, addr, path, flags=""):
    env = dict(os.environ)
    if flags:
        env["WGATE_FLAGS"] = flags
    env.pop("WGATE_SESSION", None)
    r = subprocess.run([sys.executable, f"{KIT}/wgate.py", mod, addr, path],
                       capture_output=True, text=True, cwd=REPO, stdin=subprocess.DEVNULL, env=env)
    return ((r.stdout or "") + (r.stderr or "")).strip()


# THE FLAG SET IS A LEVER AND IT WAS NEVER TRIED. The build can swap the mwccarm BUILD per file but
# never the FLAGS, so every residue in this project was measured on one configuration.
# main:020b7ba0 gates REGPERM 14 on the default and MATCHES under -O4.
FLAG_SETS = [s for s in os.environ.get("SWEEP_FLAG_SETS", "-O3|-O4|-opt speed|-inline on").split("|") if s]


def try_flags(mod, addr, work):
    """-> None always. Records a flag match as a LEAD; deliberately does NOT land it.

    A flag match is a DIAGNOSIS, not a result. The original build almost certainly used ONE flag set,
    so a function that matches only under -O4 is telling you our source carries something -O4 deletes
    and the ROM's source never had -- the C is wrong in a specific, findable way. Landing it behind an
    override gates green and looks finished while hiding that error, which is the same trap as a
    committed pragma. Registering an override is a deliberate act for a human with a reason, never a
    sweep's default.
    """
    for flags in FLAG_SETS:
        if gate(mod, addr, work, flags).splitlines()[-1:] == ["MATCH"]:
            with open(f"{SP}/wlog/flag_leads.txt", "a", encoding="utf-8", newline="\n") as fh:
                fh.write("%s\t%s\t%s\n" % (mod, addr, flags))
            print(f"  FLAG-LEAD {mod} {addr} matches under {flags} -- our source carries something "
                  f"{flags} removes. NOT landed; fix the source.", flush=True)
            return None
            _t = open(work, encoding="utf-8", errors="ignore").read()
            _d = re.search(r'(?:extern\s+"C"\s+)?(?:ARM|THUMB)\b[^\n(;{]*?\b(\w+)\s*\(', _t)
            name = (_d.group(1) if _d else f"func_{addr}") + ".cpp"
            src = f"src/Combat/{'Main' if mod == 'main' else 'Overlay_' + str(int(mod))}/{name}"
            tbl = f"{REPO}/tools/cc_flag_overrides.txt"
            have = ""
            try:
                have = open(tbl, encoding="utf-8").read()
            except IOError:
                pass
            if src not in have:
                with open(tbl, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write("%s %s\n" % (src, flags))
            print(f"  FLAG-MATCH {mod} {addr} under {flags} (override registered)", flush=True)
            return flags
    return None


def add_initcode(path):
    """Give the definition after the // USA: tag the initcode declspec."""
    s = open(path, encoding="utf-8").read()
    if "initcode" in s or "// USA:" not in s:
        return False
    s = s.replace("#include <globaldefs.h>\n",
                  '#include <globaldefs.h>\n\n#pragma define_section initcode ".init" RX\n', 1)
    i = s.index("// USA:")
    head, tail = s[:i], s[i:]
    tail, n = re.subn(r'(?m)^(extern "C" )?(ARM|THUMB)\b',
                      lambda m: 'extern "C" __declspec(initcode) ' + m.group(2), tail, count=1)
    if not n:
        return False
    open(path, "w", encoding="utf-8", newline="\n").write(head + tail)
    return True


# Colour-sweep limits. Small diffs are the ones that are purely a register choice; a file that is
# 100 bytes off is a different program, not a different colouring, and sweeping it just burns time.
COLORSWEEP_MAX_BYTES = int(os.environ.get("COLORSWEEP_MAX_BYTES", "24"))
COLORSWEEP_BUDGET_MAX = int(os.environ.get("COLORSWEEP_BUDGET_MAX", "400"))
# 40 was below the cost of a real crack and silently capped every one of them. The measured
# two-step path on 0209ed0c (comparison operand order, then function-scope declaration order) took
# 63 compiles at depth 3; at depth 2/budget 40 the sweep gives up long before reaching it, which is
# why a full pass over 1254 parked candidates closed exactly zero. Budget is compiles, not seconds,
# and only near-misses ever reach the sweep.
COLORSWEEP_BUDGET = int(os.environ.get("COLORSWEEP_BUDGET", "150"))
COLORSWEEP_DEPTH = int(os.environ.get("COLORSWEEP_DEPTH", "3"))

def rank(verdict):
    """Order two verdicts on the same address. Right-length beats wrong-length, as colorsweep
    scores, so a size-exact BYTEDIFF 28 outranks an OVERGEN 4 that never reached the slot."""
    if verdict.startswith("MATCH"):
        return (0, 0)
    m = re.search(r"(\d+) bytes differ", verdict)
    if m:
        return (1, int(m.group(1)))
    m = re.search(r"total=0x([0-9a-fA-F]+)\s+slot=0x([0-9a-fA-F]+)", verdict)
    if m:
        return (2, abs(int(m.group(1), 16) - int(m.group(2), 16)))
    return (3, 1 << 30)


def asmparked(mods):
    """Addresses ov_recover already parked as hand-asm off the allowlist, per module.

    ov_recover writes wlog/asmpark_<sub>.txt when it moves those sources to asm_park_<sub>/. The
    files stay in gated/ afterwards, so anything that promotes out of gated/ by MATCH verdict alone
    resurrects them: a MATCH is not permission to land, because hand-asm is byte-exact by
    construction and is then refused by place() on asm policy. main 0200c0e4, 0200c14c and 020ca650
    went round this loop every wave -- rescued here, refused by the wave, re-parked by ov_recover,
    rescued again -- costing a full rebuild each time for 0 commits.
    """
    out = {}
    for sub in mods:
        p = f"{SP}/wlog/asmpark_{sub}.txt"
        try:
            out[sub] = {a.strip().lower() for a in open(p, encoding='utf-8').read().split() if a.strip()}
        except OSError:
            out[sub] = set()
    return out


def rescue_gated():
    """Copy proven matches out of gated/ into staging/ so an integration can SEE them.

    wgate archives every MATCH under gated/<mod>/<addr>.cpp, and pull_worker separately copies the
    match into staging/<mod>/ -- but that copy reads the file out of src/, and a wave for ANOTHER
    module quarantines other modules' untracked .cpp while it runs, so the file is often gone by
    then and the `cp ... 2>/dev/null` fails silently. pull_all picks what to integrate by counting
    staging/, so the match becomes invisible: ov000:0215858c ($19.23) and ov024:021ea85c both had to
    be staged by hand. gated/ is proof, so it is safe to promote from.

    An address ov_recover already parked as hand-asm is NOT promoted: see asmparked().
    """
    n = 0
    subs = {m if m == "main" else "ov" + m for m in sym.values()}
    parked = asmparked(subs)
    for f in glob.glob(f"{SP}/gated/*/*.cpp"):
        m = re.search(r"([0-9a-fA-F]{8})\.cpp$", os.path.basename(f))
        if not m:
            continue
        addr = m.group(1).lower()
        if addr in done or addr not in sym:
            continue
        mod = sym[addr]
        if addr in parked.get(mod if mod == "main" else "ov" + mod, ()):
            continue
        if gate(mod, addr, f).splitlines()[-1:] != ["MATCH"]:
            continue
        stage = f"{SP}/staging/" + ("main" if mod == "main" else "ov" + mod)
        os.makedirs(stage, exist_ok=True)
        dst = f"{stage}/{addr}.cpp"
        if not os.path.exists(dst):
            shutil.copy(f, dst)
            n += 1
            print(f"  RESCUED {mod} {addr} from gated/ into staging", flush=True)
    return n


hits, stuck, verdicts = [], {}, []
vanished = 0
for i, (mod, addr, f) in enumerate(jobs):
    work = f"{WORK}/{mod}_{addr}.cpp"
    # a concurrent wave can move a parked file between the glob and this read
    try:
        src = open(f, encoding="utf-8").read()
    except OSError:
        vanished += 1
        continue
    # A clsbest artifact keeps its `// USA:` tag defused so ov_recover.gather() cannot mistake an
    # unmatched variant for a wave candidate; the sweep is the only thing meant to read it.
    src = src.replace("// SCRATCH-USA: func_", "// USA: func_")
    open(work, "w", encoding="utf-8", newline="\n").write(src)
    verdict = gate(mod, addr, work)
    verdict0 = verdict
    if verdict.startswith("UNDEF-SYM"):
        subprocess.run([sys.executable, f"{KIT}/fixundef.py", mod, addr, work],
                       capture_output=True, text=True, cwd=REPO, stdin=subprocess.DEVNULL)
        verdict = gate(mod, addr, work)
        # fixundef can only resolve a name that CARRIES its address. The other UNDEF-SYM cause is a
        # correctly-named callee declared extern "C" when the ROM symbol is mangled -- no address to
        # key on, and autorepair is the only thing that fixes it. This sweep never called autorepair
        # at all, so every parked file with that fault was invisible to it.
        if verdict.startswith("UNDEF-SYM"):
            subprocess.run([sys.executable, f"{KIT}/autorepair.py", mod, addr, work],
                           capture_output=True, text=True, cwd=REPO, stdin=subprocess.DEVNULL)
            verdict = gate(mod, addr, work)
    if "total=0x0" in verdict and ".init" in verdict and add_initcode(work):
        verdict = gate(mod, addr, work)
        if verdict.startswith("UNDEF-SYM"):
            subprocess.run([sys.executable, f"{KIT}/fixundef.py", mod, addr, work],
                           capture_output=True, text=True, cwd=REPO, stdin=subprocess.DEVNULL)
            verdict = gate(mod, addr, work)
    # SIZE/OVERGEN is the BIGGEST stuck bucket (40 of 60 in the last full sweep, vs 15 BYTEDIFF),
    # and a function that is a few bytes too long is usually carrying one redundant register copy
    # -- exactly what these rewrites remove. colorsweep scores wrong-length candidates behind
    # right-length ones, so it can climb out of the oversized space instead of refusing to start.
    # SCALE THE "near" THRESHOLD WITH THE FUNCTION, for the same reason max_bytes below scales: a
    # flat 16 bytes admitted 35 of 129 parked SIZE candidates, and a 4KB function 40 bytes out is
    # proportionally closer than a 256B function 16 bytes out.
    _sz = re.search(r"total=0x([0-9a-fA-F]+)\s+slot=0x([0-9a-fA-F]+)", verdict)
    _near_size = bool(_sz) and (abs(int(_sz.group(1), 16) - int(_sz.group(2), 16))
                                <= max(16, int(_sz.group(2), 16) // 32))
    if verdict.startswith("BYTEDIFF") or verdict.startswith("DIFF") or _near_size:
        # COLOUR SWEEP. A parked file that is a handful of bytes off is usually not wrong, it
        # just put a value in the wrong register. colorsweep rewrites it in meaning-preserving
        # ways (operand order, compound-assign side, declaration order, post-increment
        # placement) and keeps whatever compiles smaller -- strcpy and strcat both closed this
        # way in under 10 compiles. Capped, because a big function generates many candidates
        # and this runs over the whole parked pool.
        # SCALE THE CUTOFF AND THE BUDGET WITH THE FUNCTION. A flat 24-byte cutoff means a
        # 4000-byte function is only swept when it is already within 24 bytes -- practically a
        # match -- while its ordinary register residue is hundreds. A big function also has more
        # candidate sites, so a flat budget buys proportionally fewer of them. Both now scale off
        # the slot size, which every verdict line carries.
        _slot = re.search(r"slot=0x([0-9a-fA-F]+)", verdict)
        fsize = int(_slot.group(1), 16) if _slot else 0
        max_bytes = max(COLORSWEEP_MAX_BYTES, fsize // 16)
        # CEILING. Unbounded, a 10KB function asks for 1355 compiles of a 10KB function -- hours,
        # for one candidate, inside a pass that has 280 of them.
        budget = min(COLORSWEEP_BUDGET + fsize // 8, COLORSWEEP_BUDGET_MAX)
        nbytes = re.search(r"(\d+) bytes", verdict)
        if _near_size or (nbytes and int(nbytes.group(1)) <= max_bytes):
            subprocess.run([sys.executable, f"{KIT}/colorsweep.py", mod, addr, work,
                            "--depth", str(COLORSWEEP_DEPTH),
                            "--budget", str(budget), "--apply"],
                           capture_output=True, text=True, cwd=REPO, stdin=subprocess.DEVNULL)
            verdict = gate(mod, addr, work)
            if verdict.startswith("MATCH"):
                print(f"  colorsweep closed {mod} {addr}", flush=True)
    # Last resort before parking it: the same source under a different flag set. Cheap (one compile
    # per set) and it converts residues that no source rewrite reaches.
    if not verdict.startswith("MATCH") and try_flags(mod, addr, work):
        verdict = gate(mod, addr, work)
    verdicts.append((mod, addr, verdict.replace(chr(10), " ").strip()))
    if not verdict.startswith("MATCH"):
        keep = f"{BEST}/{addr}.cpp"
        # Sidecar rank, not a re-gate: reading the kept file's quality must not cost a compile per
        # improved address on every pass.
        prior = (1 << 30, 1 << 30)
        if os.path.exists(keep + ".rank"):
            try:
                prior = tuple(int(x) for x in open(keep + ".rank").read().split())
            except ValueError:
                pass
        if rank(verdict)[0] < 3 and rank(verdict) < prior:
            # Shards and the hourly auto sweep can reach the same address; write whole files so a
            # reader never sees a half-written candidate.
            text = open(work, encoding="utf-8").read()
            with open(keep + f".tmp{TAG}", "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text.replace("// USA: func_", "// SCRATCH-USA: func_"))
            os.replace(keep + f".tmp{TAG}", keep)
            with open(keep + f".rank.tmp{TAG}", "w") as fh:
                fh.write("%d %d" % rank(verdict))
            os.replace(keep + f".rank.tmp{TAG}", keep + ".rank")
            print(f"  kept {mod} {addr} best {rank(verdict)} (was {rank(verdict0)} on entry)", flush=True)
    if verdict.startswith("MATCH"):
        print(f"HIT {mod} {addr} {os.path.basename(f)}", flush=True)
        hits.append((mod, addr, work, f))
        # Hand the repaired source to the normal wave path: finish_wave.sh picks up
        # staging/<module>/ on that module's next visit, so these land through the same
        # gate, cull and commit machinery as any worker output -- never around it.
        stage = f"{SP}/staging/" + ("main" if mod == "main" else "ov" + mod)
        os.makedirs(stage, exist_ok=True)
        shutil.copy(work, f"{stage}/{os.path.basename(f)}")
    else:
        stuck[verdict.split(":")[0]] = stuck.get(verdict.split(":")[0], 0) + 1
    if i % 25 == 24:
        print(f"  ...{i+1}/{len(jobs)}, {len(hits)} hits", flush=True)

_resc = rescue_gated()
if _resc:
    print(f"rescued {_resc} proven match(es) from gated/ into staging")
print(f"done: {len(hits)} hits; remaining verdicts: {sorted(stuck.items(), key=lambda kv: -kv[1])[:8]}")
# PER-SHARD filenames. Every shard used to write the same repair_hits.txt, so with four running the
# last one to finish erased the other three's hits.
with open(f"{SP}/repair_hits{TAG}.txt", "w", encoding="utf-8") as fh:
    for mod, addr, work, orig in hits:
        fh.write(f"{mod} {addr} {work} {orig}\n")
# KEEP THE PER-ADDRESS VERDICTS. Only the counts were ever printed, so a sweep that closed nothing
# told you 13 candidates were BYTEDIFF and not WHICH -- and the residues are the input to the next
# colorsweep rule. diffcluster.py reads this to group them by what actually differs.
with open(f"{SP}/wlog/repair_verdicts{TAG}.txt", "w", encoding="utf-8") as fh:
    for mod, addr, v in verdicts:
        fh.write(f"{mod} {addr} {v}\n")
