#!/usr/bin/env python
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
# Hardened module integrator: sanitizes names (strips 0x-hex tokens that break dsd delinking),
# never clobbers tracked files, restores HEAD cleanly, bisects to isolate link-poison funcs,
# checks consistency before every commit. Usage: python ov_recover.py <OV|main> [staging_dirs...]
#
# ONE CODE PATH, PARAMETERIZED. arm9 `main` is NOT a fork of this script: every overlay-vs-main
# difference is a value in the MOD block below. Forking is what produced five copies whose ARM-vs-THUMB
# assumptions drifted apart and silently dropped 28 of 31 gate-verified matches. Never fork this.
import re, subprocess, os, sys, glob, time, shutil, hashlib
import srcdir
import culprits
import buildcfg
# The scratchpad is wherever THIS file lives; the old absolute %TEMP% path was deleted by Windows
# cleanup on 2026-08-24 and took the whole pipeline with it.
SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
REGION = buildcfg.REGION
TAG = REGION.upper()
os.chdir(REPO)
MOD = sys.argv[1]              # "000".."035" (an overlay) or "main" (the arm9 module)
MAIN = (MOD == "main")
OV = MOD                       # name kept so every ov-shaped message/filename below reads unchanged
if MAIN:
    SUF     = "main"                       # suffix for wlog/hold/stage filenames
    TAGPRE  = "func_"                      # `// USA: func_<addr>` — no `ov` infix
    HEXC    = "[0-9a-fA-F]"                # main's delinks.txt mixes UPPER and lowercase hex
    CFG     = buildcfg.config_dir(MOD)
    SRCDIR  = srcdir.for_module("main")
    INT     = f"{KIT}/integrate.py"         # one integrator for every module
    INTARGS = ["main"]
    # main's address space (0x02000000-0x021536e0) is disjoint from every overlay's, so sharing a
    # skiplist would be safe — but overlay addrs collide with EACH OTHER, so the files stay split.
    SKIP    = f"{KIT}/skiplist_main.txt"
    MSG     = "Match {n} arm9 main functions"
    DEFSTAGE = ["main_stage"]
else:
    DEC     = str(int(OV))
    SUF     = f"ov{OV}"
    TAGPRE  = f"func_ov{OV}_"
    HEXC    = "[0-9a-f]"
    CFG     = buildcfg.config_dir(MOD)
    SRCDIR  = srcdir.for_module(OV)
    INT     = f"{KIT}/integrate.py"         # one integrator for every module
    INTARGS = [OV]
    SKIP    = f"{KIT}/skiplist_ov.txt"
    MSG     = f"Match {{n}} ov{OV} overlay functions"
    DEFSTAGE = ["ov000_w5rescue", "ov000_w4w5", "ov000_entangled", "ov000_stage"]
# The `// USA:` tag matcher. The overlay pattern is byte-identical to the old hardcoded one; main only
# adds \b so `func_<8hex>` can never swallow a 9th hex digit. The two can never cross-match: after
# `// USA: func_` an overlay tag continues `ov...`, and `o` is not a hex digit.
TAGRE = rf'// {TAG}: {TAGPRE}({HEXC}{{8}})' + (r'\b' if MAIN else '')
def tag_addr(txt):
    m = re.search(TAGRE, txt)
    return m.group(1).lower() if m else None       # .lower() is a no-op on the overlay class


_BOUND = None


def bound_addrs():
    global _BOUND
    if _BOUND is None:
        _BOUND = {}
        try:
            for line in open(f"{REPO}/{CFG}/symbols.txt", encoding="utf-8", errors="ignore"):
                m = re.match(r"(\S+)\s+kind:function\([^\n]*?addr:0x([0-9a-f]{8})", line)
                if m:
                    _BOUND.setdefault(m.group(1), m.group(2))
        except OSError:
            pass
    return _BOUND


def retag(txt, fp):
    m = re.search(rf"^// {TAG}: (\S+)[^\n]*\n([^\n]*)", txt, re.M)
    if not m:
        return txt
    a = bound_addrs().get(m.group(1))
    if not a:
        print(f"NO-TAG {os.path.basename(fp)}: '// {TAG}: {m.group(1)}' names no function of this module")
        return txt
    keep = "" if 'extern "C"' in m.group(2) else " // KEEP-NAME"
    print(f"RETAG {os.path.basename(fp)}: {m.group(1)} -> {TAGPRE}{a}")
    return txt[:m.start()] + f"// {TAG}: {TAGPRE}{a}{keep}" + txt[m.end(1):]
# VERIFICATION MODE (default OFF — production behaviour is completely unchanged when unset).
# NOCOMMIT=1 runs the entire pipeline for real — gather, classify, integrate, full `ninja check` gate —
# but makes no git commit, and therefore also skips the final clean() so the gated tree survives for
# external inspection (`ninja rom && ninja sha1`). Restore afterwards with the printed command.
NOCOMMIT = bool(os.environ.get("NOCOMMIT"))
# USE THE AMBIENT GIT IDENTITY. This used to force `-c user.name=... -c user.email=...` plus an
# explicit --author on the commit, which OVERRIDES ~/.gitconfig completely -- so every wave kept
# committing under an old username long after it had been changed globally, and no amount of
# `git config --global user.name` had any effect. Whatever the user's git is configured to be is
# what the pipeline should commit as.
AUTH = []
STAGING = sys.argv[2:] or DEFSTAGE
# ALWAYS re-consider the hold dir LAST. hold_<mod> accumulates every worker .cpp ever produced for this
# module; a func deferred by a red gate / gate-cap in an earlier wave still lives there fully matched.
# Re-integrating it costs ZERO worker tokens. Without this the ONLY way a deferred func ever lands is a
# worker re-decompiling it from scratch — which is exactly where 40-80% of worker tokens were going.
# Listed last so fresh src output wins on addr collision (gather() keeps the first hit per addr).
if f"hold_{SUF}" not in STAGING: STAGING = list(STAGING) + [f"hold_{SUF}"]
# Same for the quarantine dir. finish_wave's preflight moves untracked .cpp there to keep the tree
# clean, and a wave's fresh worker output can end up in it (measured: 118 files, ~95 of one ov031 wave's
# matches, never reconsidered). Quarantine PRESERVES rather than deletes, so re-gathering recovers them
# for zero worker tokens — and makes this whole class of silent loss impossible regardless of which
# preflight rule moved a file. gather() filters by THIS module's `// USA:` tag, so another module's
# files in the shared dir are ignored.
if "quarantine" not in STAGING: STAGING = list(STAGING) + ["quarantine"]
# And gated/<mod>, which wgate writes on MATCH and only on MATCH — the one directory in the pipeline
# where every file is proven against the ROM, and until now the one no dispatcher read. It held 801
# files historically, 112 of which were never committed; worker output survived only because a wave
# preserves untracked src/ into hold_<mod>. Listed last for the same reason hold_ is.
if f"gated/{SUF}" not in STAGING: STAGING = list(STAGING) + [f"gated/{SUF}"]

_TRACKED = None
_INDEX_MUTATORS = frozenset(("add", "reset", "checkout", "commit", "rm", "mv",
                             "read-tree", "update-index"))

def sh(*a):
    global _TRACKED
    # Invalidate before attempting a write: even failed Git commands may change the index.
    if len(a) > 1 and a[0] == "git" and a[1] in _INDEX_MUTATORS:
        _TRACKED = None
    argv = list(a)
    if argv and argv[0] == "python":
        argv[0] = sys.executable  # reuse this interpreter and its installed dependencies
    return subprocess.run(argv, capture_output=True, text=True)

def repo_relative_path(path):
    root = os.path.normcase(os.path.abspath(REPO))
    try:
        path = os.fspath(path).replace("\\", "/")
        full = os.path.normcase(os.path.abspath(path if os.path.isabs(path)
                                              else os.path.join(root, path)))
        if os.path.commonpath((root, full)) != root:
            return None
        return os.path.relpath(full, root).replace("\\", "/")
    except (TypeError, ValueError, OSError):
        return None

def read_tracked_paths():
    r = subprocess.run(["git", "ls-files", "--cached", "-z"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("FATAL: cannot read tracked paths; no cleanup permitted: "
                           + r.stderr.decode("utf-8", "replace")[:300])
    if r.stdout and not r.stdout.endswith(b"\0"):
        raise RuntimeError("FATAL: truncated tracked-path snapshot; no cleanup permitted")
    paths = set()
    for raw in r.stdout.split(b"\0"):
        if not raw:
            continue
        normalized = repo_relative_path(raw.decode("utf-8", "surrogateescape"))
        if normalized is None:
            raise RuntimeError("FATAL: unsafe tracked path; no cleanup permitted")
        paths.add(normalized)
    return paths

def tracked(path):
    global _TRACKED
    if _TRACKED is None:
        # Publish only a successful complete snapshot. Errors leave the cache invalid.
        _TRACKED = read_tracked_paths()
    if isinstance(path, str) and path.replace("\\", "/") in _TRACKED:
        return True
    normalized = repo_relative_path(path)
    return normalized is None or normalized in _TRACKED

def clean():
    # restore ALL committed config + src + include + headers to HEAD (a worker may edit ANY
    # committed file — src/Combat/Main, include/, config), then delete untracked .cpp in this
    # module's dir. Reverting include/ too closes the header-edit-persists gap.
    # For main, CFG is config/usa/arm9 — the PARENT of overlays/ — so this reverts every module's
    # config to HEAD. That is the intended superset (`git checkout HEAD -- src/` was already
    # project-wide); it only ever discards UNCOMMITTED config edits, which a wave must not carry.
    sh("git", "checkout", "HEAD", "--", CFG + "/", "src/", "include/")
    # Tolerate a file vanishing between the glob and the remove. The `git checkout` above
    # can delete it, and so can a second wave running concurrently -- and an unguarded
    # os.remove raises FileNotFoundError, which aborts the ENTIRE recovery pass and makes
    # the module report +0 for reasons that have nothing to do with the candidates.
    def _rm(path):
        try:
            os.remove(path)
        except OSError:
            pass
    # PRESERVE BEFORE DELETING. Every untracked .cpp here is a worker's attempt that did not land.
    # Deleting it throws away the only copy: func_ov016_0218e17c reached 4 bytes short of a 196-byte
    # function, the worker wrote a precise post-mortem in its log, and NOTHING survived on disk --
    # so the next worker to draw that address starts from zero. Held sources cost nothing and are
    # re-gated by every repair sweep, which now also runs colorsweep on 2-24 byte residues, so a
    # preserved near-miss converts later for no model tokens at all.
    _hold = f"{SP}/hold_{SUF}"
    for f in glob.glob(f"{SRCDIR}/*.cpp"):
        if tracked(f):
            continue
        try:
            _txt = open(f, encoding='utf-8', errors='ignore').read()
            _a = tag_addr(_txt)          # module-correct tag regex; None for another module's file
            if _a:
                os.makedirs(_hold, exist_ok=True)
                _dst = f"{_hold}/{_a}.cpp"
                if not os.path.exists(_dst):
                    shutil.copy2(f, _dst)
                elif open(_dst, encoding='utf-8', errors='ignore').read() != _txt:
                    # keep BOTH attempts; the sweep dedupes by (addr, content-hash) anyway
                    shutil.copy2(f, f"{_hold}/{_a}_{hashlib.md5(_txt.encode()).hexdigest()[:6]}.cpp")
        except OSError:
            pass
        _rm(f)
    for o in glob.glob(f"{SRCDIR}/**/*.o", recursive=True): _rm(o)
    for o in glob.glob(f"{SRCDIR}/*.o"): _rm(o)

def sweep_foreign():
    """Move other modules' untracked .cpp out of the tree, preserving them in quarantine.

    finish_wave's preflight already does this once, but a worker on ANOTHER module keeps writing
    while this wave runs and simply recreates the file -- and configure.py globs all of src/, so one
    half-written source (a `goto` whose label is not typed yet) fails the build for EVERY module:
    `ProcessCombatTurn_0215d63c.cpp:726: undefined label 'L_0da4'` reddened a main gate and deferred
    a match that had nothing to do with it. Re-sweeping immediately before the build shrinks the race
    from the whole wave to the build's own start. Quarantine is a swept pool, so nothing is lost.
    """
    q = f"{SP}/quarantine"
    os.makedirs(q, exist_ok=True)
    moved = 0
    # ALL of src/, not src/Combat/ alone: the update-compiler base put ARM9 main's sources in
    # src/World, src/Util, src/System and six more, and configure.py globs every one of them.
    for f in glob.glob("src/**/*.cpp", recursive=True):
        p = f.replace(chr(92), "/")
        if p.startswith(SRCDIR + "/") or tracked(p):
            continue
        try:
            shutil.move(p, f"{q}/{os.path.basename(p)}")
            moved += 1
        except (OSError, shutil.Error):
            pass
    if moved:
        print(f"  quarantined {moved} foreign untracked .cpp before the build")


def gate():
    for p in [f"build/{REGION}/arm9.o"] + glob.glob(f"build/{REGION}/build/*.bin"):
        try: os.remove(p)
        except OSError: pass
    sweep_foreign()
    configure_args = ["python", "tools/configure.py", REGION, "--no-extract"]
    if os.environ.get("DQIX_PREINSTALLED_COMPILER"):
        configure_args += ["--compiler", os.environ["DQIX_PREINSTALLED_COMPILER"]]
    if os.environ.get("DQIX_WINE"):
        configure_args += ["-w", os.environ["DQIX_WINE"]]
    cf = sh(*configure_args)
    # MAIN-ONLY PREFLIGHT (~free: `ninja check` depends on `ninja delink` anyway, so this only pulls
    # that step forward). A new main delink entry re-splits one of dsd's gap modules, and a module
    # whose symbol sizes sum past its section size makes mwldarm abort with a message naming neither
    # the symbol nor the cause. modsize_check names the module and the culprit symbols. FAIL-SOFT: a
    # crashed/absent checker never reds the gate — only an explicit rc==1 verdict does, and then we
    # skip the ~6-min full check entirely. See SP/inv/mainfix_FINDINGS.md.
    if MAIN and os.path.exists(f"{KIT}/modsize_check.py"):
        dl = sh("ninja", "delink")
        ms = sh("python", f"{KIT}/modsize_check.py")
        if dl.returncode == 0 and ms.returncode == 1 and "VIOLATION" in ms.stdout:
            try:
                open(f"{SP}/wlog/gate_{SUF}.txt", "w", encoding='utf-8', errors='ignore').write(
                    f"--- configure ---\n{cf.stdout}\n{cf.stderr}\n--- modsize_check (PREFLIGHT RED) ---\n"
                    f"{ms.stdout}\n{ms.stderr}")
            except OSError: pass
            print("  modsize preflight RED (link would abort) — skipping ninja check")
            return False
    r = sh("ninja", "check")
    out = r.stdout + r.stderr
    ok = r.returncode == 0 and "abort" not in out and "FAILED" not in out
    if not ok:   # keep the real reason; a silent red used to be indistinguishable from a bad func
        # lcfcheck turns a red gate into a one-line diagnosis when the cause is link LAYOUT: it proves
        # from the generated lcf + the built objects that every `<obj>.o(<sec>)` line resolves to
        # exactly one object that really has that section, and that each segment's placed bytes sum to
        # its declared size. Anything it reports IS the drift. See SP/inv/drift_FINDINGS.md.
        lc = sh("python", f"{KIT}/inv/drift/lcfcheck.py")
        try:
            open(f"{SP}/wlog/gate_{SUF}.txt", "w", encoding='utf-8', errors='ignore').write(
                f"--- configure ---\n{cf.stdout}\n{cf.stderr}\n--- lcfcheck ---\n{lc.stdout}\n"
                f"--- ninja check (rc={r.returncode}) ---\n{out}")
        except OSError: pass
    return ok

def sanitize(text, addr):
    # NAME THE FILE AFTER ITS OWN DEFINITION, not after the first `ARM` token in the file.
    # The old plain first-`ARM`-in-file search matched a forward DECLARATION whenever the source
    # declares an ARM callee above the definition (very common), so the file was written under the
    # CALLEE's name. When that name equalled some other src file's basename, two objects shared a
    # basename, dsd's lcf line `<basename>.o(.text)` became ambiguous, mwldarm left one object
    # UNPLACED, and the overlay came up short by that function's size -> every later symbol drifted.
    # That is the whole of link-layout drift M1. See SP/inv/drift_FINDINGS.md.
    # Must accept THUMB as well as ARM. With ARM-only this returned None for every thumb function, so
    # place() skipped writing the file entirely and 28 of 31 gate-verified thumb matches silently
    # vanished before the integrator ever saw them.
    m = re.search(rf'// {TAG}: {TAGPRE}{addr}[^\n]*\n.*?\b(?:ARM|THUMB)\b[^\n;{{]*?\b([A-Za-z_]\w*)\s*\(',
                  text, re.S) or re.search(r'\b(?:ARM|THUMB)\b[^\n(]*?\b([A-Za-z_]\w*)\s*\(', text)
    if not m: return None, text
    name = m.group(1)
    if '0x' in name:
        nn = re.sub(r'0x[0-9a-fA-F]+', '', name).strip('_')
        if not nn or nn[0].isdigit(): nn = 'Fn' + nn
        nn = f"{nn}_{addr}"
        text = re.sub(rf'\b{re.escape(name)}\b', nn, text)
        name = nn
    return name, text

_DIRTY_BASE = set()


def dirty_tracked():
    return {l[3:] for l in sh("git", "status", "--porcelain", "src/").stdout.splitlines()
            if l[:2] in (' M', ' D', 'MM', 'AD', 'MD')}


def consistent():
    # no tracked src file modified/deleted BY THIS WAVE (clobber guard); every delinked cpp exists.
    # ONLY what this wave dirtied counts. The guard used to fail on any modified tracked file under
    # src/, and a worker editing a committed source while the wave ran -- routine, since several
    # slots work the module a wave is integrating -- was blamed on the candidate: main:020dd7ac, a
    # sweep-closed match, was deferred as CLOBBER by exactly that.
    for l in sorted(dirty_tracked() - _DIRTY_BASE):
        print("  CONSISTENCY FAIL: tracked file changed:", l); return False
    for p in re.findall(r'(?m)^\s*(src/[^:\s]+\.(?:cpp|c))\s*:', open(f"{CFG}/delinks.txt").read()):
        if not os.path.isfile(p):
            print("  CONSISTENCY FAIL: missing delinked file:", p); return False
    return True

def committed_addrs():
    # normalize to full 8-hex (candidate addrs are 8-hex); guard against leading-zero mismatch.
    # HEXC: main's delinks.txt mixes hex case (`.text start:0x0200FE68`). A lowercase-only class
    # silently truncates such an entry — see the BASE=512 trap in inv/mainsup_FINDINGS.md §6.2.
    # .init as well as .text: functions DO land in .init (recipe #21), integrate_ov writes a
    # `.init start:` entry for them, and a .text-only pattern cannot see it. The entry was being
    # written correctly and then reported as "wired-0 -> defer", so every .init function was
    # deferred forever while the gate went green.
    # ANCHOR TO A LIVE LINE. delinks.txt opens with nine DISABLED entries (`//    .text start:...`)
    # that carry no source file. Unanchored, they read as landed: 312 main functions fall inside one
    # and 75 of them are covered by no live range, so gather() dropped every one before it could
    # become a candidate and main committed 0 on every wave.
    return set(f"{int(x,16):08x}" for x in
               re.findall(rf'(?m)^\s*\.(?:text|init) start:0x({HEXC}+) end:',
                          open(f"{CFG}/delinks.txt").read()))

def delinked_ranges():
    # (start,end) of every PER-FILE code delink entry, .text AND .init. The section-table header
    # lines are not matched: they pad the name with several spaces before `start:`, and this
    # pattern requires exactly one.
    return [(int(s, 16), int(e, 16)) for s, e in
            re.findall(rf'(?m)^\s*\.(?:text|init) start:0x({HEXC}+) end:0x({HEXC}+)\s*$',
                       open(f"{CFG}/delinks.txt").read())]

def unstage_foreign():
    """Drop from the index any source this wave did not wire.

    `git add -A` stages the whole repository, and with several workers alive their in-flight files
    sit in src/ while a wave runs. An ov015 wave committed a main worker's unfinished 02021578 at
    BYTEDIFF 55 that way -- inert, because an unwired function is never compiled, but it is another
    module's live work landing in a commit that claims to be one overlay's matches. prune_unwired
    cannot catch it: that only looks in this module's own SRCDIR.
    """
    now = committed_addrs()
    staged = sh("git", "diff", "--cached", "--name-only").stdout.split()
    foreign = []
    for rel in staged:
        if not rel.endswith((".cpp", ".c")) or not rel.startswith("src/"):
            continue
        try:
            a = tag_addr(open(f"{REPO}/{rel}", encoding="utf-8", errors="ignore").read())
        except OSError:
            continue
        if a is None or a not in now:
            foreign.append(rel)
    if foreign:
        sh("git", "reset", "-q", "--", *foreign)
        print("  not this wave's work, left uncommitted: " + " ".join(os.path.basename(f)
                                                                      for f in foreign[:6]))


def commit(n):
    if not consistent():
        return 'inconsistent'   # a tracked file got clobbered — caller must ISOLATE, never blanket-reject
    if NOCOMMIT:                # verification mode: everything real except the git write
        print(f"  [NOCOMMIT] would commit: {MSG.format(n=n)}")
        return 'ok'
    before = sh("git", "rev-parse", "HEAD").stdout.strip()
    # RETRY ON GIT FAILURE. These two calls used to ignore git's exit code entirely. A transient
    # `.git/index.lock` collision (push_watch.sh pushes every 180s; run_overlay runs `git status`
    # around every wave) makes `git add -A` fail silently -> nothing staged -> commit is a no-op ->
    # caller reads 'nothing' as "all guard-rejected" and DEFERS THE WHOLE WAVE. Measured: ov001 wave 2
    # deferred 42 TRUSTED functions this way, and re-running the identical gate later committed 41/42.
    # An 8-hour, ~50-function loss from an unchecked return code. Retry the lock, then re-check.
    for _try in range(4):
        r1 = sh("git", "add", "-A")
        unstage_foreign()
        r2 = sh("git", *AUTH, "commit", "-q", "-m", MSG.format(n=n))
        if sh("git", "rev-parse", "HEAD").stdout.strip() != before: return 'ok'
        blob = (r1.stderr or '') + (r2.stderr or '') + (r2.stdout or '')
        if 'index.lock' not in blob and 'Unable to create' not in blob and 'cannot lock' not in blob:
            break                       # a real "nothing to commit", not a lock -> report honestly
        print(f"  git lock contention (try {_try+1}) -> retry"); time.sleep(5)
    after = sh("git", "rev-parse", "HEAD").stdout.strip()
    return 'ok' if after != before else 'nothing'  # 'nothing' = green but zero delinked

# gather candidates (dedup by addr)
NOSKIP = os.environ.get("NOSKIP")  # re-gate reclaim dirs: bypass skiplist (funcs tagged there from prior bundled fails)
skipset = set(l.split()[0] for l in open(SKIP) if l.strip()) if os.path.exists(SKIP) else set()
cands = {}
# HARDENED: exclude only addrs already DELINKED (in the build). Keying off delinks — not off any
# committed // USA tag — means a stray committed orphan can NEVER block its addr from being re-served
# and freshly matched. (Orphans are purged + can't re-form, but this makes the pipeline robust even
# if one appears.) place() renames on filename collision, so no path clash either.
_done_gather = committed_addrs()
# main has LEGACY MULTI-FUNCTION FILES: one delink entry can cover several raw func_ addrs, so 7 addrs
# already sit INSIDE a delinked range without being its start. Start-membership alone would let one
# through, and wiring it would emit an OVERLAPPING delink entry and red the gate. Overlays keep the
# exact start-membership test they have always used.
_RANGES = delinked_ranges() if MAIN else []
def _already(a):
    return a in _done_gather or any(s <= int(a, 16) < e for s, e in _RANGES)
variants = {}   # addr -> [source_text, ...] every distinct attempt we hold for this addr
vnames = {}     # addr -> [basename, ...] aligned with variants, for the per-file compiler override
def gather(files):
    for fp in files:
        # A candidate can vanish between the glob and the read: the staging step moves
        # files, a previous pass deletes them, or a stray file is cleaned up underneath us.
        # An unguarded read raises and takes the WHOLE wave down, which then reports a
        # checksum failure that has nothing to do with any candidate.
        try:
            txt = open(fp, encoding='utf-8', errors='ignore').read()
        except OSError:
            continue
        a = tag_addr(txt)
        if not a:
            txt = retag(txt, fp)
            a = tag_addr(txt)
        if not a: continue
        if _already(a) or (a in skipset and not NOSKIP): continue
        # keep EVERY distinct attempt, not just the first: the hold dir holds several tries per addr and
        # the first one found may be a stale/failed attempt while a later one is a perfect match.
        v = variants.setdefault(a, [])
        # KEEP THE FILENAME ALONGSIDE THE TEXT. classify() keys the per-file compiler override on the
        # basename, so a candidate passed as bare text is compiled with the project default and a
        # function that only matches on a later mwccarm is rejected by every wave -- which is what
        # happened to ov000:0215858c, 4816 bytes, MATCH under wgate and BYTEDIFF here.
        if txt not in v:
            v.append(txt)
            vnames.setdefault(a, []).append(os.path.basename(fp))
        cands.setdefault(a, txt)
for d in STAGING:
    if d == "src":  # steady-state: fresh untracked worker output in src
        gather(f for f in glob.glob(f"{SRCDIR}/*.cpp") if not tracked(f))
    else:
        gather(glob.glob(f"{SP}/{d}/*.cpp"))

# PRESERVE (IRONCLAD): snapshot EVERY untracked .cpp in SRCDIR to a hold dir BEFORE any git/clean
# touches src — INDEPENDENT of the candidate filter. clean() deletes ALL untracked .cpp; if we only
# held gathered candidates, files excluded by skiplist/committed/tag filters would be deleted and
# LOST. Holding everything loose first = a worker match can never be destroyed, no matter the filter.
HOLD = f"{SP}/hold_{SUF}"
os.makedirs(HOLD, exist_ok=True)
_held = 0
for _fp in glob.glob(f"{SRCDIR}/*.cpp"):
    if tracked(_fp): continue
    open(f"{HOLD}/{os.path.basename(_fp)}", 'w', encoding='utf-8', newline='\n').write(
        open(_fp, encoding='utf-8', errors='ignore').read()); _held += 1
print(f"[{SUF}] preserved {_held} untracked .cpp to {HOLD} (before any git touch)")

# `<stem>.o(.text)` by basename across the WHOLE objects list, so a stem that already exists in
# src/Combat/Main or another Overlay_N makes the line ambiguous -> one object unplaced -> drift.
# Uniqueness must therefore be global, not per-directory. (M1; SP/inv/drift_FINDINGS.md)
_FOREIGN_STEMS = set()
for _l in sh("git", "ls-files", "src/").stdout.split():
    _l = _l.replace('\\', '/')
    if _l.endswith(('.c', '.cpp')) and os.path.dirname(_l) != SRCDIR:
        _FOREIGN_STEMS.add(os.path.basename(_l).rsplit('.', 1)[0])

from pragmas import HAND_ASM as ASMPAT, asm_allow
# The ONLY functions allowed to land as hand-written assembly, by address. See asm_allow.txt for
# why each one is there. Everything else still falls under the no-hand-asm policy below.
ASM_ALLOW = asm_allow()


def place(addrs):
    # Filenames must be unique ACROSS the placed set, not just vs tracked files. Two candidates whose
    # workers chose the same function name used to write the same path — the second silently overwrote
    # the first, so that function contributed no bytes and the whole overlay shifted (a link-layout
    # failure that looks nothing like a bad match). Always disambiguate by addr on any collision.
    used = set()
    for a in addrs:
        name, txt = sanitize(cands[a], a)
        if not name:
            # NEVER DROP A TRUSTED CANDIDATE SILENTLY. sanitize() returns no name when it cannot find
            # an ARM/THUMB definition, and `continue` then removed the candidate from the wave without
            # a word -- so the same byte-verified functions deferred as `wired-0` every wave forever,
            # 7 of them on main.
            if ASMPAT.search(txt):
                # Hand asm is not a match we want to land; the goal is very little asm in the final
                # product. Say so and move on -- but it must stop occupying a TRUSTED slot and a
                # ~6 min gate on every wave forever.
                if a not in ASM_ALLOW:
                    print(f"  PLACE-SKIP-ASM {a}: hand-asm candidate, not landed by policy")
                    continue
                # ...except the handful in asm_allow.txt, which have no C form that reaches these
                # bytes (BIOS syscall stubs, NitroSDK routines the SDK itself ships as assembly).
                # Refusing them landed nothing while still counting them as unmatched, which
                # understated the decomp instead of keeping it honest.
                print(f"  PLACE-ASM-ALLOW {a}: on the asm allowlist, landing as assembly")
                name = f"{TAGPRE}{a}"
            else:
                # A plain C definition with no ARM/THUMB keyword is real work; name the file after the
                # address, which is what place() falls back to on any collision anyway.
                name = f"{TAGPRE}{a}"
                print(f"  PLACE-FALLBACK {a}: no ARM/THUMB keyword, naming file {name}.cpp")
        bn = f"{SRCDIR}/{name}.cpp"
        if tracked(bn) or bn in used or os.path.exists(bn) or name in _FOREIGN_STEMS:
            bn = f"{SRCDIR}/{name}_{a}.cpp"
        if bn.rsplit('/', 1)[-1][:-4] in _FOREIGN_STEMS or bn in used:   # still not unique -> force it
            bn = f"{SRCDIR}/{TAGPRE}{a}.cpp"
        if f"// {TAG}: {TAGPRE}{a}" not in txt:
            _defpat = ('(?m)^(?:extern "C"\\s+)?'
                       '(?:__declspec\\([^)]*\\)\\s*)?'
                       '(?:ARM|THUMB)[^' + chr(10) + ';{]*' + chr(92) + '(')
            _m = re.search(_defpat, txt)
            _tag = f"// {TAG}: {TAGPRE}{a}" + chr(10)
            txt = (txt[:_m.start()] + _tag + txt[_m.start():]) if _m else (_tag + txt)
        open(bn, 'w', encoding='utf-8').write(txt)
        carry_cc_override(a, bn)


def carry_cc_override(addr, placed):
    """Follow a per-file compiler override to the name the wave places the file under.

    A worker registers the override against ITS filename, and place() renames the file to the
    definition name -- so `tools/cc_overrides.txt`, which configure.py keys on the full relative
    path, stops matching and the build compiles the function with the project default. The gate then
    fails a function that is byte-exact under the compiler it was matched with. ov000:0215858c, the
    largest match this project has made, hit exactly this.
    """
    for path in (f"{REPO}/tools/cc_overrides.txt", f"{REPO}/tools/cc_flag_overrides.txt"):
        _carry_one(path, addr, placed)


def _carry_one(path, addr, placed):
    try:
        lines = [l for l in open(path, encoding="utf-8")]
    except IOError:
        return
    want = placed.replace(chr(92), "/")
    want = want[want.index("src/"):] if "src/" in want else want
    ver = None
    for l in lines:
        p = l.split("#")[0].split()
        if len(p) == 2:
            key = p[0].replace(chr(92), "/")
            if key == want:
                return                                   # already correct
            if addr in os.path.basename(key).lower():
                ver = p[1]
    if not ver:
        return
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("%s %s\n" % (want, ver))
    print(f"  CC-OVERRIDE carried to {want} ({ver})")


def try_set(addrs):
    clean()
    # Snapshot what is ALREADY dirty -- a live worker's edits to committed sources -- so the clobber
    # guard can tell them from anything this wave writes.
    global _DIRTY_BASE, _TRACKED
    _DIRTY_BASE = dirty_tracked()
    place(addrs)
    for o in glob.glob(f"{SRCDIR}/*.o"): os.remove(o)
    # KEEP THE INTEGRATOR'S OUTPUT. It names exactly why each candidate was rejected
    # (NON-TEXT, NO-DEF, DUP-ADDR, OVERGEN, REPAIR...), and swallowing it is why waves
    # reported a bare "wired-0 -> defer" for faults that took a hand investigation to
    # find. One file per module, truncated per pass so it always reflects this wave.
    _u = [f for f in sorted(glob.glob(f"{SRCDIR}/*.cpp")) if not tracked(f)]
    print(f"  DBG-PLACED untracked={len(_u)} {[os.path.basename(x) for x in _u[:3]]}")
    if _u:
        _tx = open(_u[0], encoding="utf-8", errors="ignore").read()
        print(f"  DBG-TAG {os.path.basename(_u[0])} has{TAG}=// {TAG}: "
              f"{TAGPRE} in _tx -> {('// ' + TAG + ': ' + TAGPRE) in _tx}")
    _r = sh("python", INT, *INTARGS)
    _TRACKED = None   # delegated integrator is a mutation boundary
    try:
        with open(f"{SP}/wlog/integ_{SUF}.txt", "w", encoding="utf-8") as _fh:
            _fh.write((_r.stdout or "") + (_r.stderr or ""))
    except Exception:
        pass
    if _r.returncode != 0:
        raise RuntimeError(f"FATAL: delegated integrator exited {_r.returncode}; "
                           f"gate not run; preserved candidates remain in {HOLD}; "
                           f"see {SP}/wlog/integ_{SUF}.txt")
    # A COMMITTED source carrying an unwired `// USA:` tag is a pending candidate by design, so
    # autorepair rewrites it -- and when the verdict is OVERGEN or BYTEDIFF the edit stays behind and
    # the clobber guard blames THIS wave's byte-exact matches for it. main had six such files and
    # could not land anything. Revert a tracked file only when NONE of its tags got wired.
    try:
        _DIRTY_BASE |= {l.strip() for l in open(f"{SP}/wlog/integrate_src_rewrites.txt", encoding="utf-8")
                        if l.strip()}
    except OSError:
        pass
    _wired = committed_addrs()
    for _rel in sorted(dirty_tracked() - _DIRTY_BASE):
        if not _rel.endswith((".cpp", ".c")):
            continue
        try:
            _txt = open(f"{REPO}/{_rel}", encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if not any(a.lower() in _wired
                   for a in re.findall(rf"// {TAG}: {TAGPRE}([0-9a-fA-F]{{8}})\b", _txt)):
            sh("git", "checkout", "--", _rel)
            print(f"  REVERT-REJECTED {_rel}")
    return gate()

committed = [0]; skipped = []
def prune_unwired():
    # remove untracked .cpp that integrate_ov did NOT wire (addr not in delinks) so git add -A can
    # never commit a DEAD file. Every candidate is snapshotted in HOLD, so removal loses nothing.
    now = committed_addrs()
    for fp in glob.glob(f"{SRCDIR}/*.cpp"):
        if tracked(fp): continue
        a = tag_addr(open(fp, encoding='utf-8', errors='ignore').read())
        if (a is None) or (a not in now): os.remove(fp)

# BISECTION COST CAP: each gate() = a full ninja check (~6 min). On a hard-frontier wave dominated by
# reloc-false-matches (pass the masked per-func gate, FAIL the overlay checksum), the full set gates red
# and naive bisection does O(N) gates = HOURS. Cap the gates/wave; beyond it, defer the remainder WITHOUT
# striking (they weren't fairly isolated). Genuine matches beyond the cap just wait for the next wave.
GATES=[0]; MAXGATES=int(os.environ.get("MAXGATES","16")); faildefer=[]; drifted=[]; named_bad=[]; RETRIED=[0]
# When set, a single-func red gate defers WITHOUT recording a strike. Used for the drift pass, where the
# candidates are already known byte-exact and reloc-verified — a red there is a link interaction, not a
# bad match, and striking it would permanently write off good work.
NO_STRIKE=[False]

def gate_culprits(addrs):
    """Name the funcs that broke a red gate, straight from `dsd check symbols` output — no gates spent.

    A func can be byte-exact with every reloc verified and STILL break the link: delinking it shifts
    the overlay (e.g. an unclaimed gap between its end and the next data symbol is lost), so every
    symbol after it resolves to the wrong address. dsd reports each such symbol as
    'expected to be at 0xA but is at 0xB'. Sort those by expected address: the offset (A-B) is constant
    within a run of symbols and CHANGES exactly where a culprit sits. So each delta-change boundary
    identifies one culprit = the placed func immediately at/below that boundary.

    This replaces blind bisection for this failure mode. Isolating k culprits among N funcs by bisection
    costs O(k log N) full ROM rebuilds (~6 min each) — for the measured k=6, N=103 that is ~40 gates =
    4 hours, so the gate cap fired and the ENTIRE wave deferred with 0 commits, every wave. Parsing the
    log costs nothing and finds the same funcs (verified: it independently re-derives 02249650, the one
    func the old bisection did manage to isolate before hitting its cap).
    """
    p = f"{SP}/wlog/gate_{SUF}.txt"
    if not os.path.exists(p): return []
    try: txt = open(p, encoding='utf-8', errors='ignore').read()
    except OSError: return []
    pairs = sorted((int(e,16), int(g,16)) for e,g in
                   re.findall(r'expected to be at 0x([0-9a-f]+) but is at 0x([0-9a-f]+)', txt))
    if not pairs: return []
    starts = sorted(int(a,16) for a in addrs)
    if not starts: return []
    # OUTRIGHT SIGNAL FIRST. Drift is always "one object's section was never placed": mwld parks the
    # unplaced object near 0, so its own symbol reports `expected 0xADDR but is at 0x<tiny>` and ADDR
    # IS the culprit — no heuristic, no innocents. The delta-boundary scan below cannot tell a culprit
    # from the function that merely happens to sit under a *secondary* boundary (the .rodata/.data
    # realignment, or an 8-byte interworking veneer the linker inserts for the dropped symbol), so it
    # parked good functions: of 10 measured parked "culprits" only 4 were real. Verified against every
    # gate_ov*.txt on record: this names exactly the objects that were genuinely unplaced.
    direct = sorted({e for e, g in pairs if g < min(starts) & 0xFF000000} & set(starts))
    if direct: return [f"{a:08x}" for a in direct]
    import bisect
    bad, prev = [], None
    for e, g in pairs:
        d = e - g
        if d == prev: continue
        prev = d
        i = bisect.bisect_right(starts, e) - 1
        if i >= 0: bad.append(starts[i])
    return sorted({f"{a:08x}" for a in bad})

def recurse(addrs):
    if not addrs: return
    done = committed_addrs()
    addrs = [a for a in addrs if a not in done]
    if not addrs: return
    if GATES[0] >= MAXGATES:
        skipped.extend(addrs); print(f"  gate-cap({MAXGATES}) hit -> defer {len(addrs)} untested (no strike)")
        return
    GATES[0] += 1
    if try_set(addrs):                       # gate green
        _now = committed_addrs(); _real = [a for a in addrs if a in _now]
        if not _real:                        # integrate_ov wired NOTHING (crash / all-fail) -> NEVER commit dead .cpp
            skipped.extend(addrs); clean(); print(f"  wired-0 {len(addrs)} -> defer (no dead commit)")
            return
        prune_unwired()                      # drop dead untracked .cpp before commit; keep only delinked
        st = commit(f"batch of {len(addrs)}")
        if st == 'ok':
            _now = committed_addrs(); _real = [a for a in addrs if a in _now]
            committed[0] += len(_real); print(f"  committed {len(_real)}/{len(addrs)} delinked ({sorted(_real)[:3]}...)")
        elif st == 'nothing':                 # green but zero delinked = all guard-rejected this pass
            # SECOND LAYER over the lock retry in commit(). Reaching here with a non-empty _real is a
            # CONTRADICTION: integrate_ov wired those addrs into delinks.txt (a tracked file), so the
            # tree cannot be clean and 'nothing' cannot be honest. Deferring on it threw away a whole
            # gate's worth of green, byte-exact work. Re-commit once; only defer if it still says no.
            if _real:
                st2 = commit(f"batch of {len(addrs)}")
                if st2 == 'ok':
                    _now = committed_addrs(); _real = [a for a in addrs if a in _now]
                    committed[0] += len(_real)
                    print(f"  committed {len(_real)}/{len(addrs)} delinked after commit-retry ({sorted(_real)[:3]}...)")
                    return
                print(f"  ANOMALY: {len(_real)} addrs wired but git says nothing to commit (st2={st2})")
            skipped.extend(addrs); clean(); print(f"  guard-reject {len(addrs)} -> defer")
        else:                                 # 'inconsistent' — ONE file clobbered a tracked file.
            clean()                           # NEVER blanket-reject: bisect to isolate the culprit.
            if len(addrs) == 1:
                skipped.append(addrs[0]); print(f"  CLOBBER {addrs[0]} -> defer")
            else:
                mid = len(addrs) // 2; recurse(addrs[:mid]); recurse(addrs[mid:])
        return
    try:
        _log = open(f"{SP}/wlog/gate_{SUF}.txt", encoding='utf-8', errors='ignore').read()
    except OSError:
        _log = ""
    if culprits.transient(_log) and RETRIED[0] < 2:
        RETRIED[0] += 1; clean()
        print(f"  transient tool failure -> re-gate {len(addrs)} unchanged")
        recurse(addrs)
        return
    if len(addrs) == 1:                       # gate red on a SINGLE func = definitively bad bytes when linked
        skipped.append(addrs[0]); clean()
        if NO_STRIKE[0]:
            drifted.append(addrs[0]); print(f"  drift-single {addrs[0]} -> defer (no strike)")
        else:
            faildefer.append(addrs[0]); print(f"  fail(defer) {addrs[0]}")
        return
    # CULL-AND-RETRY before bisecting: the gate log usually names the culprits outright (see
    # gate_culprits). Dropping them and re-gating costs 1 more build and saves the whole bisection.
    # The set strictly shrinks each time, so this cannot loop. Falls back to bisection if the log
    # names nothing (or blames everything), so no failure mode loses its old handling.
    blamed = {a: w for m, a, _p, w in culprits.name(_log) if m == ("main" if MAIN else OV) and a in addrs}
    named = sorted(blamed)
    if named:
        keep = [a for a in addrs if a not in named]
        skipped.extend(named); clean()
        for a in named:
            if blamed[a] == "unplaced" or (len(named) > 1 and blamed[a] in ("link", "symbol")):
                drifted.append(a)
            else:
                (named_bad if NO_STRIKE[0] else faildefer).append(a)
        print(f"  named-cull {len(named)} {named[:4]} -> re-gate {len(keep)}")
        recurse(keep)
        return
    cul = gate_culprits(addrs)
    if cul and len(cul) < len(addrs):
        keep = [a for a in addrs if a not in cul]
        skipped.extend(cul); drifted.extend(cul); clean()
        print(f"  drift-cull {len(cul)} {cul[:4]} -> re-gate {len(keep)}")
        recurse(keep)
        return
    clean()
    mid = len(addrs) // 2
    recurse(addrs[:mid]); recurse(addrs[mid:])

print(f"[{SUF}] {len(cands)} candidates")

# ===== PRE-GATE CLASSIFICATION (the 0-commit-loop fix) =====
# Each gate() is a ~6 min full-ROM ninja check. A single reloc-false-match (byte-exact but calling the
# WRONG address — invisible to the masked per-func compare) makes the whole-wave gate RED, and isolating
# it costs O(k log N) gates, so MAXGATES fires and the ENTIRE wave defers with 0 commits. That loop was
# burning every wave on the drained overlays. Fix: classify locally FIRST (~0.3s/func, no gate), then
# gate only the fully-verified TRUSTED set — which is green on the first build and commits in one shot.
# BAD verdicts never reach a gate at all; RISKY (unconfirmable reloc) is quarantined to its own bisect.
sys.path.insert(0, KIT)
from classify import classify
_cls = {}
for _a in sorted(variants):
    _best, _bv = None, None
    for _i, _txt in enumerate(variants[_a]):      # try each held attempt; first TRUSTED wins
        _nm = (vnames.get(_a) or [None] * (_i + 1))[_i] if _i < len(vnames.get(_a, [])) else None
        _v = classify(MOD, {_a: _txt}, names={_a: _nm} if _nm else None).get(_a, 'COMPILE')
        if _bv is None or _v == 'TRUSTED':
            _best, _bv = _txt, _v
        if _v == 'TRUSTED': break
    _cls[_a] = _bv
    cands[_a] = _best
# DRIFT PARKING: funcs already proven to break the link layout (not the bytes). Re-placing them every
# wave costs an extra red gate each time and nothing else, so park them for a few waves. Not permanent —
# a neighbour landing can change the delink layout and make them fine, so they get retried periodically.
_DRIFTF = f"{SP}/wlog/drift_{SUF}.txt"
_park = {}
if os.path.exists(_DRIFTF):
    for _l in open(_DRIFTF):
        _p = _l.split()
        if len(_p) == 2 and _p[1].isdigit(): _park[_p[0]] = int(_p[1])
_parked = {a for a, n in _park.items() if n > 0}
for _a in list(_park): _park[_a] = max(0, _park[_a] - 1)   # age one wave
if _parked: print(f"[{SUF}] drift-parked (skipped this wave): {len(_parked)}")
TRUSTED = [a for a in sorted(cands) if _cls.get(a) == 'TRUSTED' and a not in _parked]
RISKY   = [a for a in sorted(cands) if _cls.get(a) == 'RISKY' and a not in _parked]
# SECTION is deliberately NOT bad: those funcs live outside .text (.init), which the integrator cannot
# express as a delink yet. Their source may be a perfect match, so they are never struck and their held
# copies are never deleted — just kept out of the gate and out of worker waves until that is fixed.
SECTION = sorted(a for a, v in _cls.items() if v == 'SECTION')
BAD     = {a: v for a, v in _cls.items() if v not in ('TRUSTED', 'RISKY', 'SECTION')}
if SECTION:
    open(f"{SP}/wlog/section_{SUF}.txt", "w").write('\n'.join(SECTION))
    print(f"[{SUF}] outside-.text (parked, not struck): {len(SECTION)}")
from collections import Counter as _C
print(f"[{SUF}] classify: TRUSTED {len(TRUSTED)} RISKY {len(RISKY)} BAD {dict(_C(BAD.values()))}")
# BAD never gets placed (it cannot match) and its held copies are purged so they stop being reclassified
# every wave. Verdicts are appended in integ_ format so run_overlay's existing 2-strike logic sees them
# and eventually skiplists the addr, stopping workers from re-attempting a func that provably won't match.
os.makedirs(f"{SP}/wlog", exist_ok=True)
with open(f"{SP}/wlog/integ_{SUF}.txt", "a") as _f:
    for _a, _v in sorted(BAD.items()): _f.write(f"{_v} {_a}\n")
for _fp in glob.glob(f"{HOLD}/*.cpp"):
    _m = tag_addr(open(_fp, encoding='utf-8', errors='ignore').read())
    if _m and _m in BAD:
        try: os.remove(_fp)
        except OSError: pass
for _a in BAD: cands.pop(_a, None)
# HAND-ASM THAT IS NOT ON THE ALLOWLIST MUST LEAVE THE WAVE, NOT JUST FAIL INSIDE IT. classify
# judges BYTES, and hand asm is byte-exact by construction, so these came back TRUSTED, took a full
# ~12 min gate, were refused by place()'s policy check, and deferred -- the same six addrs on main
# every wave, 24 refusals for 0 commits. Dropping them from trusted_ is deliberate: a func with no
# landable source is unmatched work, so genwave should serve it to a worker for a C form.
ASMPARK = [a for a in TRUSTED + RISKY if a not in ASM_ALLOW and ASMPAT.search(cands.get(a, ''))]
if ASMPARK:
    _parkdir = f"{SP}/asm_park_{SUF}"
    os.makedirs(_parkdir, exist_ok=True)
    for _fp in glob.glob(f"{HOLD}/*.cpp"):
        if tag_addr(open(_fp, encoding='utf-8', errors='ignore').read()) in ASMPARK:
            shutil.move(_fp, f"{_parkdir}/{os.path.basename(_fp)}")
    for _a in ASMPARK: cands.pop(_a, None)
    TRUSTED = [a for a in TRUSTED if a not in ASMPARK]
    RISKY = [a for a in RISKY if a not in ASMPARK]
    open(f"{SP}/wlog/asmpark_{SUF}.txt", "w").write('\n'.join(ASMPARK))
    print(f"[{SUF}] hand-asm off the allowlist, parked to {_parkdir} (back to workers): {len(ASMPARK)}")
import plausible as _plausible
IMPLAUSIBLE = [a for a in TRUSTED + RISKY if _plausible.blocking(cands.get(a, ''))]
if IMPLAUSIBLE:
    for _fp in glob.glob(f"{HOLD}/*.cpp"):
        _a = tag_addr(open(_fp, encoding='utf-8', errors='ignore').read())
        if _a in IMPLAUSIBLE and _plausible.hold(_fp, MOD, _a):
            os.remove(_fp)
    for _a in IMPLAUSIBLE: cands.pop(_a, None)
    TRUSTED = [a for a in TRUSTED if a not in IMPLAUSIBLE]
    RISKY = [a for a in RISKY if a not in IMPLAUSIBLE]
    print(f"[{SUF}] implausible, held in hold_plausible/ for cleanwork: {len(IMPLAUSIBLE)}")

# TRUSTED+RISKY are already-matched source sitting on disk: they will land on a gate, no worker needed.
# genwave_direct reads this file and stops serving these addrs — that is the duplicate-work fix.
open(f"{SP}/wlog/trusted_{SUF}.txt", "w").write('\n'.join(TRUSTED + RISKY))

order = TRUSTED + RISKY
# ONE GATE PER WAVE (the big integration speedup). Each gate() = configure + full ninja check =
# a ~10.8k-object ROM rebuild (~5 min). Old CH=48 did ceil(N/48) rebuilds/wave (~7 for a 310 batch).
# integrate_ov ALREADY byte-gates every func individually (reloc-masked vs pristine + single-.text +
# exact-size + no-undef), so a whole-wave gate is green on the FIRST try in the normal case (universal
# keep-raw guarantees in-wave callers never break). => commit the entire wave in ONE rebuild. Bisect
# only fires on a RARE red, and the bugs that once caused a bisect storm (O(n*src) grep, symbol
# mangling) are FIXED. CH = all: ~7 rebuilds -> 1. If a red ever recurses deep, that's the rare cost.
# TWO-STAGE GATE. Stage 1: the TRUSTED set as ONE build. Every one of these is byte-exact AND has each
# reloc target independently confirmed against the pristine binary, so nothing in it can be a
# reloc-false-match — it is green on the first gate and commits the whole set for the cost of one build.
# Stage 2: RISKY (some reloc unconfirmable) bisects under the gate cap, isolated so it can never drag
# the TRUSTED set down with it. Previously both were gated together, so one bad func cost the whole wave.
# TRUSTED funcs are byte-exact with every reloc target confirmed, so a red gate on one CANNOT be a
# reloc-false-match — it is a link interaction. Suppress strikes for this set; only RISKY (relocs that
# could not be confirmed) still earns one, which is the case the strike was designed for.
NO_STRIKE[0] = True
recurse(TRUSTED)
NO_STRIKE[0] = False
recurse(RISKY)
# Stage 3: RE-GATE THE DRIFT CULPRITS AS THEIR OWN SET.
# Probe result: a culled culprit gates GREEN on its own against the committed tree — drift is an
# INTERACTION between culled members, not a defect in any one of them. They were being parked for 3
# waves, which wrote off real matches (~12% of a wave). Gating them separately lets the compatible
# majority land in the same wave, while still keeping them out of the main set so stage 1 stays a
# single green build. Bounded rounds + the shared MAXGATES budget keep the cost capped.
# Gate culprits ONE AT A TIME, not as a group. Probe-verified: a culprit gates GREEN on its own against
# the committed tree, so the conflict is strictly between culprits. Re-gating them as a set just repeats
# the same collapse (measured: 23 culprits -> 22 culled again, ~8 gates for ~1 landed). Individually each
# gate is near-certain to commit, so the same budget lands ~1 func per gate instead. Whatever the budget
# does not reach keeps its held source and is retried next wave — nothing is lost.
NO_STRIKE[0] = True      # a red here must never skiplist: these are byte-exact, reloc-verified matches
_d = [a for a in dict.fromkeys(drifted) if a not in committed_addrs()]
# HARD BUDGET. Whether a culprit links depends on what else is already COMMITTED — one measured func
# gated green alone against one HEAD and red alone against the next. So a culprit is worth an occasional
# cheap retry, never a whole wave: unbounded, this pass would spend every remaining gate (~60 min) to
# land nothing. Cap it, and park anything that fails so the next few waves do not re-test a known red.
_budget = max(0, min(MAXGATES - GATES[0], int(os.environ.get("DRIFT_GATES", "3"))))
_try, _rest = _d[:_budget], _d[_budget:]
if _d: print(f"  drift pass: {len(_d)} culprit(s), gating {len(_try)} individually (budget {_budget}), {len(_rest)} carried")
for _a in _try:
    _before = set(committed_addrs())
    recurse([_a])
    if _a not in committed_addrs(): _park[_a] = 4   # red alone right now -> skip a few waves, keep source
NO_STRIKE[0] = False
if NOCOMMIT:
    # Nothing was committed, so the usual restore-to-HEAD would erase the very tree we just gated.
    # Leave it in place for external verification and print the exact command that undoes it.
    print(f"  [NOCOMMIT] leaving the gated tree in place for inspection. Restore with:")
    print(f"    git checkout HEAD -- {CFG}/ src/ include/   (+ delete untracked .cpp in {SRCDIR}/)")
else:
    clean()
# NEVER persist integration failures to the skiplist. A func can wgate-MATCH (byte-exact alone) yet
# fail integration THIS pass because a callee isn't named yet (UNDEF-SYM) — it integrates once the
# callee lands in a later wave. Permanent skiplisting here is exactly what poisoned the frontier
# (workers re-matched, gather() filtered them out, 0 committed forever). Leave failures uncommitted;
# the next wave re-serves and re-attempts them. Only human-curated walls + genuine worker-SKIP strikes
# ever enter the skiplist.
done_final = committed_addrs()
rejects = sorted(a for a in cands if a not in done_final and a not in skipped)
# fail-defer-single addrs = funcs that gated RED alone = definitively bad when linked (reloc-false-match).
# run_overlay strikes these (2x -> skiplist). Exclude any that ended up committed elsewhere this wave.
open(f"{SP}/wlog/faildefer_{SUF}.txt","w").write('\n'.join(sorted(set(faildefer)-done_final)))
# persist drift culprits (park 3 waves). NOT struck: their bytes are correct, only the delink layout
# is wrong, so a worker re-decompiling them would produce the same source and change nothing.
# Park culprits for ONE wave (the drift pass above parks its own failures for longer). A culprit is not
# a bad match — it conflicts with whatever is currently committed — so it must keep its held source and
# get retried, just not on the very next main set where it would re-poison the same build.
for _a in set(drifted) - done_final: _park.setdefault(_a, 1)
for _a in set(named_bad) - done_final: _park[_a] = 4
open(_DRIFTF, "w").write('\n'.join(f"{a} {n}" for a, n in sorted(_park.items()) if n > 0))
if drifted: print(f"  drift culprits parked: {sorted(set(drifted)-done_final)}")
# Prune held copies of funcs that are now COMMITTED — git is the source of truth for those, and the hold
# dir is re-read (and its candidates recompiled) on every single wave. ov031's had grown to 1619 files.
_pruned = 0
for _fp in glob.glob(f"{HOLD}/*.cpp") + glob.glob(f"{SP}/quarantine/*.cpp"):
    _m = tag_addr(open(_fp, encoding='utf-8', errors='ignore').read())
    if _m and _m in done_final:
        try: os.remove(_fp); _pruned += 1
        except OSError: pass
if _pruned: print(f"  pruned {_pruned} held .cpp already committed")
print(f"DONE {SUF}: committed {committed[0]}, deferred(retry-next-wave) {len(set(skipped))+len(rejects)}, fail-defer(reloc-false) {len(set(faildefer)-done_final)}")
