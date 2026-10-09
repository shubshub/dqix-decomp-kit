"""Assert the pipeline invariants whose breakage silently discards finished work.

Every check here corresponds to a fault that actually happened and cost matches:
the tools looked healthy, waves ran green, and correct sources were parked or
rewritten wrongly with no diagnostic. They are cheap and static, so run them
each cycle -- a regression should be loud, not something found by hand weeks
later.

Exit status is non-zero if any invariant fails, so run_all can surface it.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import os, re, sys, glob

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO


def read(p):
    try:
        return open(p, encoding="utf-8", errors="ignore").read()
    except IOError:
        return ""


CHECKS = []


def _regress():
    """regress.py, run in-process so it cannot be forgotten."""
    import importlib.util as il
    spec = il.spec_from_file_location("_regress", f"{KIT}/regress.py")
    m = il.module_from_spec(spec)
    spec.loader.exec_module(m)          # the checks run at import
    return m


def check(name, why):
    def deco(fn):
        CHECKS.append((name, why, fn))
        return fn
    return deco


@check("every Claude Code session in the kit updates it on start and on every stop",
       "agents updated the kit only when told to, so improvements reached them late or never")
def _kit_hooks():
    import json
    try:
        hooks = json.load(open(f"{KIT}/.claude/settings.json", encoding="utf-8")).get("hooks", {})
    except (OSError, ValueError) as e:
        return f".claude/settings.json unreadable: {e}"
    cmds = {k: " ".join(h.get("command", "") for m in v for h in m.get("hooks", [])) for k, v in hooks.items()}
    if "kit_update.py" not in cmds.get("SessionStart", ""):
        return "no SessionStart hook runs kit_update.py"
    if "kit_update.py" not in cmds.get("Stop", "") or "--hook" not in cmds.get("Stop", ""):
        return "no Stop hook runs kit_update.py --hook"
    if "_freshness()" not in read(f"{KIT}/kitpaths.py"):
        return "kitpaths.py no longer warns when the kit is behind"
    return None


@check("no Claude Code session opens a pull request from a stale kit or decomp",
       "pull requests built on stale checkouts reverted CI, re-added matched files and cited unlanded matches")
def _pr_hook():
    import json
    try:
        hooks = json.load(open(f"{KIT}/.claude/settings.json", encoding="utf-8")).get("hooks", {})
    except (OSError, ValueError) as e:
        return f".claude/settings.json unreadable: {e}"
    cmds = " ".join(h.get("command", "") for m in hooks.get("PreToolUse", []) for h in m.get("hooks", []))
    if "prready.py" not in cmds or "--hook" not in cmds:
        return "no PreToolUse hook runs prready.py --hook before gh pr create"
    return None


@check("every landing ports what it matched to the other regions",
       "matches landed in config/usa only, so EUR fell 577 files behind and JPN never moved")
def _regionsync():
    missing = [s for s in ("finish_wave.sh", "integrate_fast.sh") if "regionsync.py" not in read(f"{KIT}/{s}")]
    return f"{', '.join(missing)} never runs regionsync.py" if missing else None


@check("gates accept THUMB",
       "an ARM-only keep-raw regex rejected every thumb function in main as NO-DEF")
def _thumb():
    if "(?:ARM|THUMB)" not in read(f"{KIT}/integrate.py"):
        return "integrate.py does not accept THUMB definitions"
    return None


@check("gates are section-aware",
       "a .text-only scan reports size 0 for .init functions and rejects byte-exact sources")
def _sections():
    for f in ("wdiff.py", "wgate.py"):
        if "kind:code" not in read(f"{KIT}/{f}"):
            return f"{f} does not resolve the section from the delinks table"
    if "section_for(" not in read(f"{KIT}/integrate.py"):
        return "integrate.py does not use the address's own section"
    return None


@check("integrators self-repair",
       "mechanical faults (callee names, .init pragma) park finished matches until found by hand")
def _autorepair():
    if not os.path.isfile(f"{KIT}/autorepair.py"):
        return "autorepair.py is missing"
    if "_autorepair.repair" not in read(f"{KIT}/integrate.py"):
        return "integrate.py does not call autorepair"
    return None


@check("a multi-function translation unit can land, and a function inside a range counts as landed",
       "ov025:021de124 was byte-exact but had to share one object with 021dcf14 and 021de110; one "
       "function per file could not express it, and start-only delink checks re-landed or re-staged it")
def _tu():
    s = read(f"{KIT}/integrate.py")
    if "for f, addrs in sorted(TU.items()):" not in s or "extra_texts=" not in s:
        return "integrate.py has no multi-function TU path"
    if "_spans" not in s:
        return "integrate.py counts only range STARTS as delinked"
    if "delinked.py" not in read(f"{KIT}/integrate_fast.sh"):
        return "integrate_fast.sh clears staging by range start, so a TU's later functions stay staged"
    return None


@check("KEEP-NAME suppresses renaming",
       "keep-raw would rename a mangled C++ ROM symbol to func_<addr> and break the match")
def _keepname():
    s = read(f"{KIT}/integrate.py")
    if "keepname" not in s or "not keepname and" not in s:
        return "integrate.py does not honour KEEP-NAME"
    return None


@check("per-file compiler overrides are honoured",
       "a function needing a later mwccarm gates on the wrong compiler and reads as a mismatch")
def _ccovr():
    for f in ("wgate.py", "classify.py", "integrate.py"):
        if "cc_for(" not in read(f"{KIT}/{f}"):
            return f"{f} ignores tools/cc_overrides.txt"
    if not os.path.isfile(f"{REPO}/tools/cc_overrides.txt"):
        return "tools/cc_overrides.txt is missing"
    cfg = read(f"{REPO}/tools/configure.py")
    if "$cc_exe" not in cfg:
        return "configure.py no longer takes the compiler as a ninja variable"
    # Both local deltas live in add_mwcc_builds, which is where upstream edits land too, so an
    # upstream merge conflicts on them every time and can drop one by resolving in theirs' favour.
    if "get_asm_files" not in cfg:
        return "configure.py no longer emits mwasm builds for .s sources"
    return None


@check("integrator output is kept",
       "swallowing it turns every rejection into a bare 'wired-0 -> defer' with no reason")
def _logged():
    s = read(f"{KIT}/ov_recover.py")
    if "integ_{SUF}" not in s:
        return "ov_recover.py does not record the integrator's output"
    return None


@check("the repair sweep runs on its own",
       "parked sources accumulate; 111 of 369 turned out to be finished matches")
def _sweep():
    # pull_all.sh replaced run_all.sh on 2026-08-20; this kept asserting against the dead file, so
    # the invariant went red for a reason nobody could act on while the real gap -- no dispatcher
    # ran the sweep at all -- stayed invisible behind it.
    if "repairsweep.py" not in read(f"{KIT}/pull_all.sh"):
        return "pull_all.sh never runs the repair sweep"
    if "staging/" not in read(f"{KIT}/repairsweep.py"):
        return "repairsweep.py does not stage hits into the gated wave path"
    return None


@check("the behaviour regressions still pass",
       "selfcheck is static: it reads scripts and cannot tell whether they still DO the right "
       "thing. regress.py pins behaviour that was fixed by hand, and a suite nobody is forced to "
       "run is a suite that gets skipped")
def _behaviour():
    m = _regress()
    if m.FAILED:
        return "%d behaviour test(s) failing: %s" % (len(m.FAILED),
                                                     ", ".join(n for n, _w, _b in m.FAILED))
    return None


@check("the end-to-end crack tests were run since the sweep last changed",
       "a rewrite rule that enlarges colorsweep's neighbourhood can push a winning path outside "
       "the budget, and the only symptom is a function quietly going back to unmatched. The unit "
       "tests cannot see that -- only running the real sweep can, so refuse to go green until "
       "someone has")
def _functional_fresh():
    m = _regress()
    want = m.sweep_fingerprint()
    have = read(m.STAMP).strip()
    if not have:
        return "never run: `python regress.py --slow`"
    if have != want:
        return "colorsweep.py/wdiff.py/wgate.py or the case list changed since the last run; " \
               "re-run `python regress.py --slow`"
    return None


@check("every hand-worked address is recorded in OPEN_WORK.md",
       "closing an idiom in the doc is only half of it -- the other half is writing it down WHILE "
       "you work, because a session that dies mid-crack leaves candidate files nobody can interpret. "
       "Any .cpp under pad/ or retry_work/ is something a human or the main thread is hand-working; "
       "if its address is not in the handoff, the next session sees an orphan file and re-derives "
       "from scratch. This fires the moment the work starts, not after it lands.")
def _hand_work_recorded():
    import glob as _g
    txt = read(f"{SP}/OPEN_WORK.md").lower()
    if not txt.strip():
        return "OPEN_WORK.md is missing -- nothing can be recorded in it"
    import bisect
    ranges = []
    for d in [f"{REPO}/config/usa/arm9/delinks.txt"] + \
             sorted(_g.glob(f"{REPO}/config/usa/arm9/overlays/*/delinks.txt")):
        for x, y in re.findall(r"(?m)^\s*\.(?:text|init) start:0x([0-9a-fA-F]+) end:0x([0-9a-fA-F]+)\s*$",
                               read(d)):
            ranges.append((int(x, 16), int(y, 16)))
    ranges.sort()

    def landed(v):
        i = bisect.bisect_right(ranges, (v, 1 << 60)) - 1
        return i >= 0 and ranges[i][0] <= v < ranges[i][1]

    seen = {}
    for f in _g.glob(f"{SP}/pad/*.cpp") + _g.glob(f"{SP}/retry_work/*.cpp"):
        body = read(f)
        # a generated scaffold is not hand-work, and a committed address needs no handoff entry
        if "AUTO-SCAFFOLD" in body:
            continue
        m = re.search(r"// USA: func_(?:ov\d+_)?([0-9a-fA-F]{8})", body)
        if not m or landed(int(m.group(1), 16)):
            continue
        # Already STAGED is not undocumented: the file is queued to land through finish_wave, which
        # is the documented path. Only work that is neither recorded, nor staged, nor committed is
        # the orphan this check exists to catch.
        if _g.glob(f"{SP}/staging/*/*{m.group(1).lower()}*.cpp"):
            continue
        seen.setdefault(m.group(1).lower(), os.path.basename(f))
    missing = sorted(a for a in seen if a not in txt)
    if missing:
        return ("hand-worked but absent from the handoff: "
                + " ".join(f"{a} ({seen[a]})" for a in missing[:5])
                + " -- add the address, the symptom and what you have already ruled out")
    return None


@check("no parked source still names a callee an upstream merge renamed",
       "the GameState merge renamed 148 functions and 243 parked sources kept the old names, so each "
       "gated NO-COMPILE or UNDEF-SYM and no free sweep could ever land one")
def _repooled():
    if "repool.py" not in read(f"{KIT}/merge_human.sh"):
        return "merge_human.sh no longer runs pad/repool.py after a merge"
    import importlib.util as il
    spec = il.spec_from_file_location("_repool", f"{KIT}/pad/repool.py")
    R = il.module_from_spec(spec)
    spec.loader.exec_module(R)
    rev = read(f"{SP}/wlog/last_merge_base.txt").strip() or "a70058a0"
    spell, protos, gone, moved, defs = R.build(rev)
    stale = []
    for path, text in R.pool_files([]):
        new, _what = R.rewrite(text, path.endswith(".cpp"), spell, protos, gone, moved, defs)
        if new is not None and new != text:
            stale.append(path)
    if stale:
        return "%d parked sources still name renamed callees (first: %s); run pad/repool.py --apply --rev %s" % (
            len(stale), os.path.relpath(stale[0], SP), rev)
    return None


@check("the skills carry procedure, not state",
       "every session was hand-editing a `State as of <date>` block into dqix-plan / dqix-continue "
       "-- coverage, HEAD, what landed -- and it was stale the moment a wave committed. That is what "
       "STATE.md is for: progress.py regenerates it from disk and both skills point at it. A "
       "coverage percentage or a commit hash typed into a skill file is the failure coming back")
def _skills_have_no_state():
    import glob as _g
    bad = []
    for p in _g.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".claude", "skills", "dqix-*", "SKILL.md")):
        txt = open(p, encoding="utf-8", errors="ignore").read()
        for pat, what in ((r"\b\d\d\.\d\d%", "a coverage percentage"),
                          (r"\b1[01]\d{3}\s*/\s*14\d{3}\b", "a matched/total count"),
                          (r"HEAD `[0-9a-f]{8}`", "a HEAD commit hash")):
            if re.search(pat, txt):
                bad.append("%s has %s" % (os.path.basename(os.path.dirname(p)), what))
    return "; ".join(bad) + " -- move it to STATE.md (generated) or OPEN_WORK.md" if bad else None


@check("the dispatcher refuses to claim while a lever is unpromoted",
       "invariant 21 checks the OUTCOME (is the lever in core.md) and leverwatch reports it, but "
       "both are advisory and were ignored seven times in one run while the fleet kept claiming. "
       "The control is that pull_all calls levercheck.py and holds. This invariant guards the "
       "control itself, so removing the gate fails the build rather than silently restoring the "
       "state where a lever costs one worker to find and reaches no other")
def _lever_gate_is_wired():
    src = open(f"{KIT}/pull_all.sh", encoding="utf-8", errors="replace").read()
    if "levercheck.py" not in src:
        return "pull_all.sh no longer calls levercheck.py -- the lever gate is gone"
    # It has to gate the CLAIM. Calling it and logging the result is the advisory version that
    # already failed; the `continue` is what makes it a control.
    lines = src.split("\n")
    # It must be the CONDITION, not merely mentioned. Disabling the gate to `if false; then` while
    # leaving the --verbose logging call behind passed an earlier version of this check.
    at = next((i for i, l in enumerate(lines)
               if "levercheck.py" in l and re.search(r"^\s*if\s+!", l)), None)
    if at is None:
        return "levercheck.py is not the condition of an `if !` -- the gate is disabled"
    # Brace-matching in a regex got this wrong: a non-greedy scan to the first `fi` stopped at the
    # nested logging `if` and reported the live gate as advisory. Look for the skip in the block.
    #
    # THE SKIP IS `hold_claims`, NOT `continue`. This used to demand a `continue`, which restarts the
    # loop and skips integration too -- so the invariant was pinning the bug that stranded three
    # matches for six hours. What has to hold is the OUTCOME: no slot is refilled, and claim.py
    # itself refuses, because a live slot claims its next function without asking the dispatcher.
    if not any("hold_claims" in l for l in lines[at:at + 15]):
        return "levercheck.py is called but does not suppress claiming -- advisory, not a gate"
    if not re.search(r"for \(\(s=1;.*\n\s*\[ -n \"\$hold_claims\" \] && break", src):
        return "slot refill does not break on hold_claims"
    if "levercheck.py" not in read(f"{KIT}/claim.py"):
        return "claim.py does not check the gate, so a live slot claims straight through a hold"
    return None


@check("no live script runs ninja without a target",
       "ninja has no default target in this project, so a bare `ninja` builds every root output: "
       "one decomp.me context per source (~12k gcc invocations, and gcc is not on every box), the "
       "ROM, and the SHA-1 that needs the ARM7 BIOS. The gate wants `ninja check`; `ninja min` is "
       "rom+check and `ninja ctx` is the contexts, for when those are what is actually wanted")
def _ninja_has_a_target():
    import glob as _g
    # A quote on either side means this is an argv element -- sh("ninja", "check") -- not the end
    # of a shell command, so the target is the NEXT element and only an empty argv is bare.
    bare_sh = re.compile(r"""(?<![\w./"'-])ninja(?:\s+-\S+)*\s*(?:$|[;&|)>])""", re.M)
    bare_py = re.compile(r"""[\[(]\s*["']ninja["']\s*[\])]""")
    bad = []
    for f in _g.glob(f"{KIT}/*.py") + _g.glob(f"{KIT}/*.sh") + _g.glob(f"{SP}/rebuild/*.sh") \
            + _g.glob(f"{SP}/rebuild/*.py"):
        if os.path.basename(f).startswith("_"):
            continue
        code = "\n".join(re.sub(r"(^|\s)#.*$", "", l) for l in read(f).split("\n"))
        if bare_sh.search(code) or bare_py.search(code):
            bad.append(os.path.basename(f))
    return f"bare ninja in {sorted(set(bad))}" if bad else None


@check("a worker's failed attempt is copied somewhere gathered",
       "the miss branch of pull_worker.sh was a bare `:`, so a near-miss survived only by sitting "
       "untracked in src/ until some later wave happened to sweep it into hold_<mod>. Five of one "
       "night's attempts did not survive that -- 02093b90 at BYTEDIFF 14 and 02069fec at 63 among "
       "them, both within reach of a sweep. Paid work that leaves no file is paid twice")
def _attempt_kept():
    src = open(os.path.join(KIT, "pull_worker.sh"), encoding="utf-8").read()
    i = src.find("v=miss")
    if i < 0:
        return "pull_worker.sh no longer has a miss branch this check can find"
    lines = src[i:].split("\n")
    end = next((n for n, l in enumerate(lines) if l.rstrip() == "  fi"), len(lines))
    branch = "\n".join(lines[:end])
    if "$SP/attempts" not in branch:
        return "the miss branch does not copy the attempt into a gathered pool"
    return None


@check("no source is stranded outside a gathered directory",
       "matched sources in a directory nothing reads from are invisible work")
def _stranded():
    import glob
    gathered = ("staging", "hold_", "_stage", "_reclaim", "quarantine", "repair_work",
                "gated", "wave_", "wmain", "fin0",
                # added 2026-08-25 when repairsweep started reading them: clswork_* held the best
                # surviving attempt at four addresses, and handwork is where hand-cracked near
                # misses live between sessions -- both were invisible work by this check's own
                # definition. attempts/ is what skipsweep watches.
                # aswork_<pid> is the same failure one directory over: it held the only surviving
                # attempt at 020e1780 after that worker missed, and nothing swept it.
                # clsbest holds the best sub-MATCH artifact per address; the invariant below asserts
                # repairsweep still gathers it, so listing it here cannot strand anything.
                "clswork_", "aswork_", "handwork", "attempts", "clsbest")
    # Genuinely not candidate pools: `pad` holds compiler probes (no `// USA:` tag, not a function)
    # and `c04work` is the documented single-function workspace for the parked main:02061c04.
    # ...and these, each checked by hand 2026-08-25: `scaffold` holds generated stubs that gate at
    # total=0x2, `scratch`/`skipwork` a duplicate of the 02220af0 attempt already parked in
    # hold_ov031, `lab`/`v4b` untagged experiments, `skiplisted_hold` the two SDK hand-asm functions
    # poolsweep deliberately parks. Not one is a candidate; re-check before adding to this list.
    # `asm_park_<mod>` is where ov_recover puts a candidate that reached the bytes only by containing
    # hand-written asm. Not gathering it is the point: the no-hand-asm policy means landing it is
    # never allowed, so re-offering it would burn a wave to re-reject the same file.
    gathered = gathered + ("pad", "c04work", "scaffold", "scratch", "skipwork", "lab", "v4b",
                           "skiplisted_hold", "asm_park_", "regress_fixtures")
    loose = [d for d in glob.glob(f"{SP}/*/")
             if glob.glob(d + "*.cpp") and not any(g in d for g in gathered)]
    # glob returns backslash separators here, so rstrip('/') left the trailing separator on and every
    # basename came back empty -- the message named no directory at all for as long as it has existed.
    names = [os.path.basename(d.replace("\\", "/").rstrip("/")) for d in loose]
    return f"{len(loose)} pool(s) not swept: {names[:6]}" if loose else None


@check("every script is documented in INVENTORY.md",
       "339 scripts accumulated because nothing ever forced an accounting; an undocumented script "
       "is one nobody can decide to trust, fix or delete")
def _inventoried():
    import glob as _g
    inv = read(f"{KIT}/INVENTORY.md")
    if not inv:
        return "INVENTORY.md is missing"
    undocumented = [os.path.basename(f) for f in _g.glob(f"{KIT}/*.py") + _g.glob(f"{KIT}/*.sh")
                    if not os.path.basename(f).startswith("_")
                    and os.path.basename(f) not in inv]
    return f"{len(undocumented)} undocumented: {sorted(undocumented)[:6]}" if undocumented else None


@check("no live script builds a 'done' set from delink range STARTS",
       "main has legacy multi-function files, so every non-first function in them reads as "
       "unmatched: run_all's ranking counted ~580 already-matched functions as work to do")
def _no_range_starts():
    import glob as _g
    bad = []
    for f in _g.glob(f"{KIT}/*.py") + _g.glob(f"{KIT}/*.sh"):
        base = os.path.basename(f)
        if base in ("selfcheck.py",) or base.startswith("_"):
            continue
        code = "\n".join(re.sub(r"(^|\s)#.*$", "", l) for l in read(f).split("\n"))
        # `start:0x(...) end:` with nothing capturing the END address = starts-only membership.
        if re.search(r"start:0x\(\[0-9a-fA-F\]\+\)\s+end:(?!0x\()", code):
            bad.append(base)
    return f"range-start membership in {bad}" if bad else None


@check("no live script points at a session scratchpad or a machine path",
       "a session scratchpad is wiped by temp cleanup and a machine path exists on one machine, so "
       "the tool appears to work while touching nothing the pipeline uses")
def _no_stale_session():
    import glob as _g
    bad = []
    for f in _g.glob(f"{KIT}/*.py") + _g.glob(f"{KIT}/*.sh"):
        if os.path.basename(f).startswith("_"):
            continue
        if re.search(r"Temp[/\\]claude[/\\]|[A-Za-z]:[/\\]Users[/\\]", read(f)):
            bad.append(os.path.basename(f))
    return f"machine-specific path in {sorted(set(bad))}" if bad else None


@check("every live script actually parses",
       "selfcheck reported 17/17 while ov_recover.py had an unterminated string literal in it -- "
       "every check was a text grep, so nothing noticed the pipeline's biggest engine could not "
       "even be imported. A wave would have died on its first integrate with a SyntaxError")
def _all_parse():
    import ast
    import glob as _g
    import subprocess
    bad = []
    for f in _g.glob(f"{KIT}/*.py"):
        if os.path.basename(f).startswith("_"):
            continue
        try:
            ast.parse(read(f))
        except SyntaxError as e:
            bad.append(f"{os.path.basename(f)}:{e.lineno}")
    # `bash` on this box resolves to WSL's bash for a native-Windows Python, and WSL cannot see a
    # C:/ path -- every shell script then reports "does not parse". Use Git Bash explicitly, and if
    # no usable bash exists, check only the Python files rather than emit 18 false failures.
    shell = None
    for cand in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe", "bash"):
        try:
            probe = subprocess.run([cand, "-n", f"{KIT}/selfcheck.py"], capture_output=True, text=True)
            if "No such file or directory" not in (probe.stderr or ""):
                shell = cand
                break
        except OSError:
            continue
    if shell:
        for f in _g.glob(f"{KIT}/*.sh"):
            if os.path.basename(f).startswith("_"):
                continue
            r = subprocess.run([shell, "-n", f], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(os.path.basename(f))
    return f"{len(bad)} script(s) do not parse: {bad[:5]}" if bad else None


@check("every script a live script INVOKES actually exists",
       "consolidating files broke selfcheck itself (it asserted things about integrate_main.py "
       "after that was archived) and would have broken run_all the same way -- a dangling "
       "invocation surfaces mid-wave, hours later, as a failure with no obvious cause")
def _no_dangling_invocations():
    import glob as _g
    live = [f for f in _g.glob(f"{KIT}/*.py") + _g.glob(f"{KIT}/*.sh")
            if not os.path.basename(f).startswith("_")]
    # Only `$KIT/x.py` / `{KIT}/x.sh` references: those unambiguously name a kit script. A
    # looser match flagged docstring examples ("python $KIT/x.py") and tools/configure.py, which
    # lives in the repo -- and a check that cries wolf is a check nobody reads.
    call = re.compile(r"[{$]KIT[}]?/([A-Za-z0-9_]+\.(?:sh|py))")
    missing = []
    for f in live:
        # strip comments so prose about an archived file is not read as a call
        txt = "\n".join(re.sub(r"(^|\s)#.*$", "", l) for l in read(f).split("\n"))
        for m in call.finditer(txt):
            dep = m.group(1)
            if not os.path.isfile(f"{KIT}/{dep}"):
                missing.append(f"{os.path.basename(f)} -> {dep}")
    return f"dangling invocation(s): {sorted(set(missing))[:5]}" if missing else None


@check("autorepair verifies the SYMBOL it produces, not just that it compiles",
       "it renamed a definition to the mangled ROM symbol without extern \"C\", so the file built "
       "cleanly and emitted _Z49_Z23... -- a symbol in no ROM -- which then went into symbols.txt "
       "and failed `dsd check symbols` for the whole build")
def _autorepair_symbol():
    s = read(f"{KIT}/autorepair.py")
    if "_emits_symbol" not in s:
        return "autorepair.py does not check the emitted symbol name"
    if "_text_emits" not in s:
        return "autorepair.py renames without first asking whether the definition is already correct"
    return None


@check("no live script keys symbol lookups on the func_ NAME",
       "a curated ROM name (strcmp, AutoloadCallback, any mangled _Z) is then invisible: the "
       "ranking undercounts, the finisher mismeasures, and the work is never served")
def _no_name_keyed():
    import glob as _g
    bad = []
    for f in _g.glob(f"{KIT}/*.py") + _g.glob(f"{KIT}/*.sh"):
        base = os.path.basename(f)
        if base in ("selfcheck.py", "auditlint.py") or base.startswith("_"):
            continue          # these two legitimately contain the pattern as a search string
        # STRIP COMMENTS FIRST. The comment explaining this very bug quotes the bad pattern, and a
        # check that cannot tell code from prose reports it forever -- which is how a real alarm
        # ends up ignored among false ones.
        code = "\n".join(re.sub(r"(^|\s)#.*$", "", l) for l in read(f).split("\n"))
        if re.search(r"(?m)^.*func_[^\n]{0,60}kind:function.*$", code):
            bad.append(base)
    return f"name-keyed symbol lookup in {bad}" if bad else None


@check("every captured lever has reached the doc workers actually read",
       "levers.tsv is write-only -- nothing reads it back -- and recipe_select only ever offers "
       "core.md sections, so an unpromoted lever is unreachable rather than merely low-ranked; "
       "0 of 10 had ever been promoted, including the one that closed the first >1KB match")
def _levers_promoted():
    import subprocess as _sp
    r = _sp.run([sys.executable, f"{KIT}/levercheck.py"], capture_output=True, text=True)
    if r.returncode == 0:
        return None
    bad = [l.strip().split()[0] for l in r.stdout.split("\n") if l.startswith("  ")]
    return f"{len(bad)} lever(s) never promoted into core.md: {bad[:6]}"


@check("the gate records its own history and can say STOP",
       "the only documented stop rule presupposed a compiling, size-exact artifact, so a session "
       "that never reached one had no rule in scope and stopped on model judgment alone -- 45 turns "
       "in one case and 193 in another under identical configuration. wgate writing every verdict "
       "to wlog/gates/ is what lets it count gates since the last improvement and print STOP")
def _gate_history():
    src = read(f"{KIT}/wgate.py")
    if "gatelog.record(" not in src:
        return "wgate.py no longer records gate history -- the STOP banner cannot be computed"
    if "gatelog.banner(" not in src:
        return "wgate.py records history but never prints the banner, so no worker ever sees STOP"
    if "STOP:" not in read(f"{KIT}/gatelog.py"):
        return "gatelog.py no longer emits a STOP directive"
    return None


@check("the worker verdict is a measured CLASS, not prose",
       "SKIP was free text, so 'regalloc' covered at least five different cracks -- pure renumber, "
       "push-list composition, umull pair swap, scratch scheduling and if-conversion -- and nothing "
       "could rank which was worth cracking. The class now comes from the gate, not the worker")
def _typed_verdict():
    src = read(f"{KIT}/pull_worker.sh")
    if "BLOCKED $ADDR" not in src:
        return "pull_worker.sh no longer asks for a BLOCKED <CLASS> verdict"
    if re.search(r"SKIP \$ADDR <reason>", src):
        return "pull_worker.sh still offers the free-text SKIP verdict"
    if "blocker.py" not in src:
        return "pull_worker.sh never records a measured blocker, so blockers.tsv stays empty"
    return None


@check("the dispatcher refuses to claim while a blocker class is over threshold",
       "the miss side had no counterpart to the lever gate: a residue class that recurs was paid "
       "for once per function, forever, because nothing held the fleet until one member was cracked")
def _blocker_gate_is_wired():
    src = read(f"{KIT}/pull_all.sh")
    if "blockercheck.py" not in src:
        return "pull_all.sh no longer calls blockercheck.py -- the blocker gate is gone"
    lines = src.split("\n")
    at = next((i for i, l in enumerate(lines)
               if "blockercheck.py" in l and re.search(r"^\s*if\s+!", l)), None)
    if at is None:
        return "blockercheck.py is not the condition of an `if !` -- the gate is disabled"
    if not any("hold_claims" in l for l in lines[at:at + 12]):
        return "the blocker gate does not suppress claiming -- it reports instead of holding"
    if "blockercheck.py" not in read(f"{KIT}/claim.py"):
        return "claim.py does not check the gate, so a live slot claims straight through a hold"
    return None


@check("a blocked address is not re-served until something changed",
       "the same addresses were served cold again and again -- 0200c670, 0202e9a4, 020b33f0, "
       "0204bc74 and 0204fbf8 each appear twice in one verdict log with different diagnoses -- so "
       "the second session paid full price to reproduce a verdict already on disk")
def _no_cold_reserve():
    src = read(f"{KIT}/claim.py")
    if "blocked_addrs" not in src:
        return "claim.py no longer filters recently-blocked addresses"
    if "blockers.tsv" not in src:
        return "claim.py's filter no longer reads the measured blocker log"
    if "getmtime" not in src:
        return "the filter is not tied to a crack timestamp, so it can never clear itself"
    return None


@check("the sweep's kept artifacts are a pool it also gathers from",
       "matched and improved source written into a directory nothing re-reads is the biggest token "
       "waste this project has measured -- hold/ and quarantine/ stranded work for weeks -- and a "
       "kept artifact outside the pool list would make every pass re-derive the same best again")
def _clsbest_is_swept():
    src = read(f"{KIT}/repairsweep.py")
    if "clsbest" not in src:
        return "repairsweep no longer keeps the best sub-MATCH artifact"
    at = src.find("pools = sorted(")
    end = src.find("\n\n", at)
    if at < 0 or "BEST" not in src[at:end]:
        return "clsbest is written but not in the pool list -- nothing ever gathers it"
    if "SCRATCH-USA" not in src:
        return "kept artifacts keep a live // USA: tag -- ov_recover would take one as a candidate"
    return None


@check("no live script hard-resets to a remote branch",
       "run_module's RED recovery reset to origin/decomp-matching on every module. On sp2p2-base "
       "that discarded the entire rebase -- 946 commits -- and only the reflog got them back. The "
       "current branch's HEAD is the last green state, whatever the branch is called")
def _no_remote_reset():
    for p in sorted(glob.glob(f"{KIT}/*.sh")) + sorted(glob.glob(f"{KIT}/*.py")):
        for i, line in enumerate(read(p).splitlines()):
            if line.lstrip().startswith("#"):
                continue
            if re.search(r'reset\s+--hard\s+(origin|upstream|eakeys)/', line):
                return f"{os.path.basename(p)}:{i + 1} hard-resets to a remote branch"
    return None


@check("a rejected candidate's edits to a committed source are reverted",
       "integrate treats a committed file with an unwired // USA: tag as a candidate and autorepair "
       "rewrites it. When the verdict is OVERGEN the edit stays, the clobber guard sees a dirty "
       "tracked file and defers the whole pass -- main held six such files and could land nothing")
def _rejected_edits_reverted():
    src = read(f"{KIT}/ov_recover.py")
    if "REVERT-REJECTED" not in src:
        return "ov_recover does not revert tracked files whose candidate was rejected"
    at = src.find("REVERT-REJECTED")
    if "_wired" not in src[max(0, at - 900):at]:
        return "the revert is not conditioned on whether the file's addresses were wired"
    return None


@check("a worker's MATCH survives its own staging sweep",
       "the end-of-session sweep keeps one file per address and moves the rest to attempts/. It "
       "compared the staged copy against the path the worker WROTE ($WIP/<addr>.cpp), which never "
       "matches $STAGE/<addr>.cpp, so every MATCH was swept out of staging -- and integration is "
       "triggered by counting staging/*/*.cpp, so no worker match could land at all")
def _match_survives_sweep():
    src = read(f"{KIT}/pull_worker.sh")
    at = src.find("swept ${_swept} non-final file(s)")
    if at < 0:
        return "pull_worker.sh no longer sweeps staging -- this invariant needs rewriting"
    block = src[max(0, at - 1200):at]
    if '"$_v" = "$f"' in block:
        return "the sweep still compares the staged file against the worker's own path"
    if "_vb" not in block or "basename" not in block:
        return "the sweep's keep-guard no longer compares basenames"
    if "restaged" not in src[at:at + 1200]:
        return "nothing restages a MATCH that the sweep removed anyway"
    return None


@check("a worker is shown every earlier attempt at its address",
       "a session got one artifact and no idea what had already been tried, so it re-ran forms "
       "previous sessions had paid for -- the main thread did the same by hand on 02053634, 14 "
       "forms deep, with an already-gated shape sitting in attempts/. The blocks are charged to "
       "the recipe budget because they are written ABOVE the recipes and a worker's Read is "
       "truncated from the back, which is how the recipes once reached zero sessions")
def _prior_attempts_shown():
    src = read(f"{KIT}/recipe_select.py")
    if "prior_attempts" not in src:
        return "recipe_select.py no longer shows earlier attempts"
    if "attempt_blocks" not in src or "rbytes" not in src:
        return "the attempt blocks are not built before the budget is computed"
    if "sidecar" not in src:
        return "the attempt diffs are not split into a sidecar file"
    at = src.find("rbytes = ")
    if "len(index)" not in src[at:at + 260]:
        return "the sidecar index is not charged against the recipe budget"
    if "EARLIER ATTEMPTS" not in read(f"{KIT}/pull_worker.sh"):
        return "the prompt does not tell the worker to read the earlier-attempts file"
    if "deadends.md" not in src:
        return "per-address dead ends are not injected for the address being worked"
    core = read(f"{KIT}/worker_src/core.md")
    if "MEASURED DEAD ENDS" in core:
        return "address-keyed dead ends are back in the shared recipe doc"
    big = [(p, os.path.getsize(p)) for p in glob.glob(f"{SP}/doc_cache/*.md")
           if os.path.getsize(p) > 58000]
    if big:
        p, n = max(big, key=lambda t: t[1])
        return f"{os.path.basename(p)} is {n} bytes -- past the ~58KB Read truncation limit"
    return None


@check("a near-miss session is capped by its residue, not the function's size",
       "cap_for sizes a session to the work of DECOMPILING a function; a queued near-miss is "
       "already decompiled and needs one insight, so the size cap buys re-derivation instead -- "
       "opus missed 021eb5d0, four bytes short of 8896, for $21.49 under the $25 cap. The first "
       "cut looked the address up in priority_<mod>.txt and could never fire, because claim.py "
       "strikes an address out of that file the moment it serves it")
def _nearmiss_cap():
    src = read(f"{KIT}/pull_worker.sh")
    if "CAP_NEARMISS" not in src:
        return "pull_worker.sh no longer caps a near-miss session"
    at = src.find("CAP_NEARMISS")
    block = src[max(0, at - 700):at]
    if "priority_" in block and "--residue" not in block:
        return "the cap is keyed on the priority queue, which is emptied before the cap is computed"
    if "--residue" not in src:
        return "pull_worker.sh does not ask nearmiss.py for the measured residue"
    if "--residue" not in read(f"{KIT}/nearmiss.py"):
        return "nearmiss.py has no --residue mode for the dispatcher to ask"
    return None


@check("a near-miss is never re-offered unless something improved since we paid for it",
       "claim.py strikes an address out of the queue when it serves it, but nearmiss_watch rebuilds "
       "from blockers.tsv every 15 minutes and a still-unmatched near-miss goes straight back to the "
       "top -- so the queue re-sold 0209a218 and 020b25c4 to opus within the hour, at $5 a session, "
       "on exactly the evidence that had just failed. Two weaker rules were tried and both leaked: "
       "artifact mtime lets a rewritten-but-not-better file through, and comparing against the "
       "newest ledger row lets a REGRESSING session re-open its own address")
def _nearmiss_reserve():
    src = read(f"{KIT}/nearmiss.py")
    if "pull_*_" not in src or "_last_paid" not in src:
        return "nearmiss.py no longer looks at whether an address was already paid for"
    for p in glob.glob(f"{SP}/wlog/priority_*.txt"):
        for line in read(p).splitlines():
            if "nearmiss" not in line:
                continue
            addr = line.split()[0].lower()
            paid = [os.path.getmtime(j) for j in glob.glob(f"{SP}/wlog/pull_*_{addr}.json")
                    if os.path.getsize(j) > 0]
            if not paid:
                continue
            art = [os.path.getmtime(a) for a in
                   glob.glob(f"{SP}/clsbest/{addr}.cpp") + glob.glob(f"{SP}/attempts/*{addr}*.cpp")]
            if art and max(art) <= max(paid):
                return (f"{os.path.basename(p)} re-offers {addr}, already paid for, with no "
                        f"artifact newer than that session")
    return None


@check("a declined residue class never reaches the near-miss queue",
       "wgate reports OVERGEN/UNDERGEN as a LENGTH delta -- our code is N bytes longer or shorter "
       "than the slot -- and it cannot report a bytediff at all when the lengths differ. The queue "
       "read that N as a distance from byte-exact: 0204e038 ranked top as a 4-byte near-miss while "
       "its own artifact differed over 222 bytes, and the $6 near-miss cap priced three opus "
       "sessions as one-insight jobs, 0 of 3 for $16.90. blockers_declined.txt already held the "
       "evidence for both classes and nothing in the queue path read it")
def _nearmiss_declined():
    src = read(f"{KIT}/nearmiss.py")
    if "blockers_declined" not in src:
        return "nearmiss.py does not read blockers_declined.txt"
    gone = set()
    for line in read(f"{SP}/wlog/blockers_declined.txt").splitlines():
        w = line.split()
        if w and re.fullmatch(r"[A-Z][A-Z0-9-]+", w[0]):
            gone.add(w[0])
    if not gone:
        return None
    for p in glob.glob(f"{SP}/wlog/priority_*.txt"):
        for line in read(p).splitlines():
            cls = line.rsplit(",", 1)[-1].strip()
            if cls in gone:
                return (f"{os.path.basename(p)} offers {line.split()[0]} as a {cls} near-miss, "
                        f"a class declined in blockers_declined.txt")
    return None


@check("the near-miss queue regenerates and never re-offers reserved work",
       "the queue is built from blockers.tsv, which grows on every verdict, so one built by hand is "
       "correct for a moment and then decays -- new near-misses go unqueued and landed ones stay "
       "queued. It also has two consumers and one costs money: without a reservation the "
       "regenerating watcher kept re-offering the four register permutations colorsweep was "
       "already sweeping for free")
def _nearmiss_queue_live():
    src = read(f"{KIT}/nearmiss.py")
    if "blockers.tsv" not in src:
        return "nearmiss.py no longer reads the measured ledger"
    if "priority_" not in src:
        return "nearmiss.py does not write the file the dispatcher serves"
    if "reserved()" not in src:
        return "nearmiss.py does not honour wlog/nearmiss_reserved.txt"
    if "staged()" not in src:
        return "nearmiss.py re-offers a staged match -- it is unmatched in the ledger until it commits"
    if "verified_gap" not in src:
        return "nearmiss.py trusts the ledger's number without gating the file behind it"
    if "best[addr][0]" not in src:
        return "nearmiss.py takes the LATEST verdict, so a regressing session deletes a near-miss"
    # EVERY writer into attempts/ needs the guard, not just the one that was fixed first. Guarding
    # the keep-copy alone left the staging sweep destroying 02079cf8's 3-byte artifact minutes later.
    worker = read(f"{KIT}/pull_worker.sh")
    writes = [i for i, ln in enumerate(worker.splitlines())
              if re.search(r'(cp|mv) "[^"]+" "\$SP/attempts/', ln)
              or re.search(r'(cp|mv) "\$[a-z_]+" "\$_(dst|sd)"', ln)]
    if not writes:
        return "pull_worker.sh no longer preserves the session artifact"
    lines = worker.splitlines()
    for i in writes:
        if not any("cmp -s" in l for l in lines[max(0, i - 8):i]):
            return f"pull_worker.sh:{i + 1} writes attempts/ without the no-overwrite guard"
    watch = f"{KIT}/nearmiss_watch.sh"
    if not os.path.exists(watch) and "nearmiss.py" not in read(f"{KIT}/pull_all.sh"):
        return "nothing regenerates the queue -- it decays from the moment it is built"
    res = f"{SP}/wlog/nearmiss_reserved.txt"
    if os.path.exists(res):
        held = {m.group(1).lower() for m in
                re.finditer(r"(?m)^\s*([0-9a-fA-F]{8})\b", read(res))}
        for p in glob.glob(f"{SP}/wlog/priority_*.txt"):
            for line in read(p).splitlines():
                a = re.match(r"\s*([0-9a-fA-F]{8})\b", line)
                if a and a.group(1).lower() in held:
                    return f"{os.path.basename(p)} offers {a.group(1)}, reserved for the free sweep"
    return None


@check("a hold stops claiming, not landing",
       "the lever and blocker gates ended in `sleep 30; continue`, which restarts the dispatcher "
       "loop and skips the integration block below them. A hold on 2026-09-08 left three proven "
       "matches uncommitted for six hours while the live slots, which claim through claim.py "
       "themselves, spent ~$18 more -- the hold stopped the free half and not the paid half")
def _hold_spares_integration():
    src = read(f"{KIT}/pull_all.sh")
    for gate in ("levercheck.py", "blockercheck.py"):
        at = src.find(gate)
        if at < 0:
            return f"pull_all.sh no longer runs {gate} -- this invariant needs rewriting"
        block = src[at:at + 700]
        cut = block.find("for ((s=")
        if "continue" in (block[:cut] if cut > 0 else block):
            return f"the {gate} hold still `continue`s the loop, skipping integration"
    if "hold_claims" not in src:
        return "pull_all.sh no longer suppresses slot refill on a hold"
    claim = read(f"{KIT}/claim.py")
    if "levercheck.py" not in claim or "blockercheck.py" not in claim:
        return "claim.py does not check the gates, so a live slot claims straight through a hold"
    return None


@check("a delink range is read from a LIVE line, never a commented one",
       "delinks.txt carries disabled blocks as `//    .text start:... end:...`. An unanchored regex "
       "counts those as landed, so every address inside one is treated as already matched and "
       "skipped: synth's sweep reported `0 shapes recognised` while sitting on two functions it "
       "could prove, both inside one 1920-byte commented block. Eleven scripts shared the pattern. "
       "This check itself only matched ONE SPELLING of the query -- the literal [0-9a-fA-F] class -- "
       "so ov_recover, which builds it from an f-string variable, stayed invisible: both its readers "
       "were unanchored, _already() counted nine commented blocks as landed, and 75 main functions "
       "(19,548 bytes) could never be gathered while this invariant reported green")
def _delink_regex_anchored():
    trigger = re.compile(r"start:0x\([^)]*\)\s*end")
    bad = []
    for p in sorted(glob.glob(f"{KIT}/*.py")):
        if os.path.basename(p) == "selfcheck.py":
            continue
        lines = read(p).splitlines()
        for i, ln in enumerate(lines):
            if not trigger.search(ln) or "kind:code" in ln:
                continue
            if "(?m)^" not in "".join(lines[max(0, i - 1):i + 1]):
                bad.append(f"{os.path.basename(p)}:{i + 1}")
    if bad:
        return "unanchored delink-range regex in " + ", ".join(bad[:4])
    return None


@check("the free rewrites run before the paid session, not after it",
       "colorsweep only ran inside repairsweep, on an hourly timer over a 475-candidate pool walked "
       "in order, so it had usually not touched the address about to be claimed -- and the prompt "
       "asked the WORKER to run it, spending tokens on a gated, meaning-preserving rewrite that "
       "costs only CPU. A session should never be the first thing to try a mechanical rule")
def _presweep_before_claim():
    if not os.path.exists(f"{KIT}/presweep.py"):
        return "presweep.py is gone -- nothing runs the rules before a claim"
    w = read(f"{KIT}/pull_worker.sh")
    if "presweep.py" not in w:
        return "pull_worker.sh does not pre-sweep the address it just claimed"
    ci = w.find("ADDR=$(python")
    pi = w.find("presweep.py")
    di = w.find("recipe_select.py")
    if not (ci < pi < di):
        return "the pre-sweep does not sit between the claim and the doc build"
    if "presweep MATCHED" not in w:
        return "a free MATCH from the pre-sweep still spawns a paid session"
    # THE SESSION'S OWN OUTPUT IS THE ARTIFACT THE RULES HAVE NEVER SEEN, and it is the closest
    # anyone has been to the address. Without this it waited for the hourly repairsweep to reach it
    # in a 475-candidate queue.
    if "postsweep" not in w:
        return "the session's own artifact is never swept -- it waits for the hourly pass"
    if w.find("postsweep") < w.find("presweep MATCHED"):
        return "the post-session sweep runs before the session, not after it"
    if not os.path.exists(f"{KIT}/presweep_watch.sh"):
        return "nothing sweeps the queue ahead of a claim, so every claim waits for the rules"
    if "--force" not in w:
        return "the post-session sweep honours the already-swept marker and will skip its own new file"
    # THE DEEP SWEEP BELONGS TO THE PASS NOBODY WAITS ON. At claim time a three-candidate sweep ate
    # the whole 240s budget on main:02079cf8, was killed by the timeout, and the partial work was
    # thrown away while a slot idled five minutes.
    if "--deep" not in read(f"{KIT}/presweep_watch.sh"):
        return "the advance sweeper does not ask for the multi-candidate pass"
    if "--deep" in w:
        return "the claim path asks for the deep sweep and will block a slot on it"
    # EXISTING IS NOT RUNNING. Every check above asks whether a FILE says the right thing; none asks
    # whether anything starts the advance sweeper. It died with the 2026-09-08 session, no dispatcher
    # relaunches it, and on 2026-09-09 two paid workers hand-derived r11 and r35 -- rules colorsweep
    # already had and would have applied for the price of a compile.
    if "presweep_watch" not in read(f"{KIT}/pull_all.sh"):
        return "no dispatcher starts presweep_watch.sh, so it runs only while someone remembers to"
    return None


@check("only one dispatcher and one watcher of each kind can run",
       "supervise.sh starts pull_all whenever the pid file is absent or stale, so an operator "
       "restart that removes it while the old loop lives gives TWO dispatchers, each refilling its "
       "own slot -- double spend with PULL_SLOTS=1 still reading 1. Two ran from 16:59 on "
       "2026-09-08, and three presweep_watch loops stacked the same way")
def _single_instance():
    for f in ("pull_all.sh", "presweep_watch.sh", "nearmiss_watch.sh"):
        s = read(f"{KIT}/{f}")
        if not s:
            continue
        if "kill -0" not in s or ".pid" not in s:
            return f"{f} does not refuse to start when one is already running"
    return None


@check("a held-but-alive dispatcher raises an alert",
       "every other health check measures activity a HELD fleet still has -- the driver process is "
       "alive, the last commit is recent, the logs were touched -- so pull_all holding on an "
       "unpromoted lever with a full queue and zero workers was watched by nothing. It idled from "
       "13:26 to 14:59 on 2026-09-08 and was found by hand. The hold is correct; the silence was not")
def _held_fleet_alerts():
    src = read(f"{KIT}/health.sh")
    if "ZERO workers" not in src:
        return "health.sh has no zero-worker check -- a held dispatcher is invisible"
    if "HELD_MIN" not in src:
        return "the zero-worker check has no threshold knob"
    at = src.find("ZERO workers")
    if "HOLDING" not in src[max(0, at - 900):at + 200]:
        return "the alert does not report WHY the dispatcher is serving nobody"
    return None


@check("an uncracked idiom class reaches the main thread",
       "blockercheck holds the dispatcher when one ADDRESS repeats, which is a hard function, not "
       "an idiom. A class where many DIFFERENT functions each fail once is exactly the thing a "
       "recipe would unlock and it tripped nothing at all -- SCHED reached 8 members, two of them "
       "hand-cracked and never written up as a colorsweep rule, while the ledger read 0 pending")
def _crack_signal_routed():
    src = read(f"{KIT}/blockercheck.py")
    if "CRACK:" not in src:
        return "blockercheck no longer emits a CRACK line for a class of distinct blocked functions"
    if "if len(v) > 1" in src and "openn" not in src:
        return "blockercheck counts repeats only, so a class of single failures is invisible"
    if "CRACK" not in read(f"{KIT}/health.sh"):
        return "nothing surfaces blockercheck's CRACK lines -- the signal reaches no one"
    return None


@check("CLAIM_FOCUS serves its band on every claim but the variety turn",
       "the focus/variety split is what keeps a one-worker run on the band that converts. Nothing "
       "downstream notices if it silently stops: PULL_BAND, a band flag or a CLAIM_MAX_SIZE below "
       "the focus range each empty the focus set, the fallback serves the whole pool instead, and "
       "the run drifts back onto the 0-for-16 tail with no error anywhere")
def _focus_ratio():
    import claim
    focus = claim.focus_band()
    if not focus:
        return None
    every = claim.variety_every()
    if not every:
        return "CLAIM_FOCUS is set but PULL_VARIETY is not a count >= 2"
    lo, hi = focus
    sizes = {}
    for p in glob.glob(f"{REPO}/config/usa/arm9/**/symbols.txt", recursive=True):
        for m in re.finditer(r"kind:function\((?:arm|thumb),size=0x([0-9a-fA-F]+)\)"
                             r"\s+addr:0x([0-9a-fA-F]+)", read(p)):
            sizes[m.group(2).lower()] = int(m.group(1), 16)
    pools = claim.pools()
    if not pools:
        return f"CLAIM_FOCUS {lo}-{hi} is set and every module reports a drained pool"
    mod = None
    for cand, _n in pools:
        pool = claim.unmatched(cand) or []
        if any(lo <= sizes.get(a, 0) <= hi for a in pool) and \
           any(not (lo <= sizes.get(a, 0) <= hi) for a in pool):
            mod = cand
            break
    if mod is None:
        return None
    base = claim.band_cursor(mod)
    inband = 0
    outband = 0
    for i in range(every):
        q = claim.unmatched(mod, cursor=base + i)
        if not q:
            return f"{mod} serves nothing at cursor {base + i}"
        s = sizes.get(q[0], 0)
        if lo <= s <= hi:
            inband += 1
        else:
            outband += 1
    if outband != 1:
        return f"{mod} served {outband} off-band of {every} claims, expected exactly 1"
    if inband != every - 1:
        return f"{mod} served {inband} in {lo}-{hi} of {every} claims, expected {every - 1}"
    return None


@check("leverwatch and levercheck call the same levers unpromoted",
       "they are two readers of one ledger and they drifted: levercheck learned to count a lever "
       "cited in deadends.md as promoted and leverwatch did not, so the alerting monitor fired on "
       "every already-promoted lever while the dispatcher's own hold read zero. A monitor that "
       "cries wolf on 40 handled levers is a monitor nobody reads the 41st line of")
def _lever_readers_agree():
    watch = read(f"{KIT}/leverwatch.sh")
    if re.search(r'levercheck\.py"? --keys', watch):
        return None
    chk = read(f"{KIT}/levercheck.py")
    for doc in ("core.md", "deadends.md"):
        if doc in chk and doc not in watch:
            return f"levercheck.py consults {doc} and leverwatch.sh does not"
        if doc in watch and doc not in chk:
            return f"leverwatch.sh consults {doc} and levercheck.py does not"
    if "levers_declined" in chk and "levers_declined" not in watch:
        return "levercheck.py honours levers_declined.txt and leverwatch.sh does not"
    return None


@check("fullstop sees a monitor's tail/grep children",
       "a Monitor's `cd` runs in its wrapper bash, so the tail/grep it spawns carry no path of "
       "their own. fullstop tested the CHILD's command line for the scratchpad path, matched "
       "nothing, and printed 'none running' while two watches stayed live for six hours -- a "
       "kill-all that reports success and leaves the watchers up is worse than no kill-all")
def _fullstop_sees_watchers():
    import subprocess, time
    log = f"{SP}/wlog/.selfcheck_decoy.log"
    open(log, "a").close()
    decoy = subprocess.Popen(
        ["tail", "-f", "-n", "0", "wlog/.selfcheck_decoy.log"], cwd=SP,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)
        # a bare "bash" resolves to WSL's, which cannot see the Windows filesystem paths we pass
        sh = "C:/Program Files/Git/bin/bash.exe"
        if not os.path.exists(sh):
            sh = "bash"
        out = subprocess.run([sh, f"{KIT}/fullstop.sh", "--dry"],
                             capture_output=True, text=True, timeout=180).stdout
        tier3 = out.split("TIER 3")[-1]
        if "none running" in tier3 or "watcher process(es)" not in tier3:
            return "fullstop --dry reports no watchers while a decoy tail on wlog/ is live"
    except subprocess.TimeoutExpired:
        return "fullstop --dry did not finish in 180s"
    finally:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(decoy.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            decoy.kill()
            decoy.wait()
        try:
            os.remove(log)
        except OSError:
            pass
    return None


@check("every path that copies staging into src/ drops already-landed files first",
       "finish_wave dropped a staged file whose address was already committed; integrate_fast did "
       "not, and copied it in regardless. The linker then saw the symbol twice and aborted the "
       "COMBINED build, rolling the whole pass back to per-module -- twice in one evening. Same "
       "question, two implementations, which is this pipeline's most expensive recurring shape")
def _staging_purged_before_copy():
    for name in ("integrate_fast.sh", "finish_wave.sh"):
        src = read(f"{KIT}/{name}")
        if not src:
            return f"{name} is missing"
        lines = src.splitlines()
        copy_at = next((i for i, l in enumerate(lines)
                        if not l.lstrip().startswith("#")
                        and re.search(r'cp\s+"?\$(d|STAGE)"?[^|]*\$(dst|SRCDIR)', l)), None)
        if copy_at is None:
            continue
        before = "\n".join(lines[:copy_at])
        if "stagepurge.py" not in before and ".done_$OV" not in before:
            return f"{name} copies staging into src/ with no already-landed check above it"
    return None


@check("a long-running job announces its own end on every exit path",
       "integrate_fast.sh has nine exits and printed a distinctive line on only some of them, so a "
       "watcher had to grep for success words and went silent on the rest -- 36 minutes were spent "
       "waiting on a job that had already finished. A monitor cannot be made reliable downstream of "
       "a producer that can end quietly")
def _job_announces_end():
    for name in ("integrate_fast.sh",):
        src = read(f"{KIT}/{name}")
        if not src:
            return f"{name} is missing"
        if not re.search(r"(?m)^\s*trap\s+\S+\s+EXIT", src):
            return f"{name} has no EXIT trap, so some exit paths end silently"
        if "-END" not in src:
            return f"{name}'s EXIT trap prints no terminal marker"
    return None


@check("wgate refuses a source that carries a codegen pragma",
       "a pragma-matched source lands a residue the developers never wrote; 423 committed ones did")
def _wgate_refuses_pragma():
    import subprocess
    import tempfile
    fd, probe = tempfile.mkstemp(suffix=".cpp")
    with os.fdopen(fd, "w") as fh:
        fh.write("#pragma opt_propagation off\n")
    env = {k: v for k, v in os.environ.items()
           if k not in ("WGATE_ALLOW_COMMITTED", "WGATE_ALLOW_PRAGMA")}
    try:
        r = subprocess.run([sys.executable, f"{KIT}/wgate.py", "main", "02065990", probe],
                           capture_output=True, text=True, env=env)
    finally:
        os.remove(probe)
    if not r.stdout.startswith("RESIDUE PRAGMA"):
        return "wgate answered %r" % (r.stdout.splitlines() or ["nothing"])[0]
    return None


@check("the committed codegen-pragma count only falls",
       "every pragma in src/ is a residue papered over; wlog/pragma_ceiling.txt ratchets down with it")
def _pragma_ratchet():
    import subprocess
    import buildcfg
    res = subprocess.run(["git", "-C", REPO, "grep", "-hE", r"^\s*#\s*pragma", "HEAD", "--", "src"],
                         capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout:
        return f"git grep failed (rc={res.returncode}): {res.stderr.strip()[:120]}"
    n = len(buildcfg.CODEGEN_PRAGMA.findall(res.stdout))
    path = f"{SP}/wlog/pragma_ceiling.txt"
    try:
        ceiling = int(open(path).read().split()[0])
    except (OSError, ValueError, IndexError):
        ceiling = None
    if ceiling is not None and n > ceiling:
        return f"{n} codegen pragma lines committed, ceiling {ceiling}"
    if ceiling is None or n < ceiling:
        with open(path, "w") as fh:
            fh.write(f"{n}\n")
    return None


@check("landed functions are counted by objdiff, not only matched in the ROM",
       "config that disagrees with a byte-exact source (absolute pool word, Thumb padding in a "
       "symbol size) leaves the function below 100% in the report")
def _countfix_holds():
    for name in ("integrate_fast.sh", "finish_wave.sh"):
        if "countfix.py" not in (read(f"{KIT}/{name}") or ""):
            return f"{name} does not run countfix.py"
    import countfix
    try:
        units = countfix.report_units()
    except OSError:
        return None
    relocs, sizes = countfix.plan(units)
    if relocs or sizes:
        return f"{len(relocs)} pool words and {len(sizes)} Thumb sizes uncorrected: run countfix.py --report"
    return None


@check("the evolve workflow is capped and runs no random-explore children by default",
       "relaunched evolve runs that stopped improving were ~75% of massive-function spend, and "
       "random-explore children bought 1 of ~15 improvements")
def _evolve_capped():
    wf = read(f"{KIT}/.claude/workflows/dqix-evolve.js") or ""
    if not wf:
        return ".claude/workflows/dqix-evolve.js is missing"
    if "evocap.py" not in wf:
        return "dqix-evolve.js never runs evocap.py"
    if not re.search(r"const EXPLORE = A\.explore === undefined \? 0 :", wf):
        return "dqix-evolve.js defaults explore to a nonzero width"
    if not os.path.exists(f"{KIT}/evocap.py"):
        return "evocap.py is missing"
    return None


fails = 0
for name, why, fn in CHECKS:
    try:
        bad = fn()
    except Exception as e:
        bad = f"check raised {e!r}"
    if bad:
        fails += 1
        print(f"FAIL  {name}: {bad}")
        print(f"      why it matters: {why}")
    else:
        print(f"ok    {name}")
print(f"{len(CHECKS) - fails}/{len(CHECKS)} invariants hold")
sys.exit(1 if fails else 0)
