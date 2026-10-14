"""Build a per-FUNCTION worker doc containing only the recipes that function can actually need.

    python recipe_select.py <mod> <addr> [--stats]      -> writes doc_cache/<mod>_<addr>.md, prints path

WHY THIS EXISTS. The full doc is 1,734 lines / 118 KB, and a worker's Read of it is TRUNCATED:
measured from three worker transcripts on 2026-08-20, sections up to ~line 666 appeared in the Read
result and sections from ~line 1236 onward did not. Roughly the back half of the doc has never
reached a single session. Reordering only chooses which half gets cut. Splitting recipes behind an
on-demand fetch was already measured too, and was worse -- of 55 sessions under SPLIT_RECIPES, ZERO
opened the recipes file, and the large band went 0-for-5.

So the doc must be SMALL and it must already contain the right recipes -- neither truncated nor
fetched. Selection is mechanical: recipes are keyed by instruction SHAPES, and the target listing is
right there. Scan the listing, keep the sections whose shapes appear, drop the rest. Zero model
tokens, and the cost is one disassembly the worker was going to read anyway.
"""
import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import difflib
import glob
import json
import os
import re
import subprocess
import sys

SP = _kp.SP
KIT = _kp.KIT
REPO = _kp.REPO
DOC = f"{KIT}/worker_src/core.md"
CACHE = f"{SP}/doc_cache"
HANDOFF_MARK = "## HANDOFF FROM THE PREVIOUS SESSION ON THIS ADDRESS"
# An explicit end sentinel, NOT "up to the next `## `": a session's recon carries its own headings,
# and a heading-terminated block silently dropped 9 KB of struct layout the first time.
HANDOFF_END = "<!-- END HANDOFF -->"
HANDOFF_CAP = 16000
# The Read limit a worker actually hits is ~58KB; stay under it with room for the preamble, the
# index and the escape-hatch note that are written outside the budgeted sections.
DOC_MAX = int(os.environ.get("DOC_MAX", "55000"))
RESERVE = int(os.environ.get("DOC_RESERVE", "5000"))

# Always present: procedure, gate, verdict, and the advice that applies before any diff exists.
# A section whose value does not depend on the target's instruction mix belongs here.
RESIDENT = [
    "Paths", "CONTEXT BUDGET", "GATE", "Bans", "Loop per ADDR", "Relocs",
    "Same encoding", "Struct copy", "Reference kinds", "Signatures", "Final .cpp",
    "BYTEDIFF", "IF THAT FAILS", "VERDICT", "Output",
    "START FROM A SIBLING", "LARGE FUNCTION", "LARGE FUNCTIONS",
    "NEAR-MISS DISCIPLINE",
]

# Mnemonics so common that their presence says nothing -- every function has them. They still score,
# but weakly, so a section is never selected on `mov` alone.
COMMON = {"mov", "ldr", "str", "b", "bl", "cmp", "add", "sub", "push", "pop", "bx", "and", "orr"}

MNEMONIC = re.compile(
    r"\b(push|pop|ldrsh|ldrsb|ldrb|ldrh|ldr|strb|strh|str|ldmia|stmia|ldm|stm|mvn|movs|mov|"
    r"umull|smull|mla|mul|adds|add|subs|sub|rsb|bic|eor|orr|and|cmn|cmp|tst|teq|"
    r"lsls|lsl|lsrs|lsr|asrs|asr|ror|swp|mrs|msr|blx|bl|bx|b)\b")
# Shapes that are stronger evidence than a bare mnemonic.
SHAPES = [
    (re.compile(r"lsl\s*#0x1[0-9a-f]|lsr\s*#0x1[0-9a-f]"), "bitfield-shift"),
    (re.compile(r"\[pc,"), "pool-word"),
    (re.compile(r"#0xff\b"), "byte-mask"),
    (re.compile(r"\b(mov|add|sub|ldr|str)(eq|ne|gt|lt|ge|le|hi|ls|cs|cc|mi|pl)\b"), "predication"),
    (re.compile(r"\bumull|\bsmull|\bmla\b"), "long-multiply"),
    (re.compile(r"sp,\s*#"), "stack-frame"),
    (re.compile(r"\bswp\b|\bmrs\b|\bmsr\b"), "system-insn"),
]


def sections(text):
    parts = re.split(r"(?m)^(#{2,3} .*)$", text)
    out = [("__preamble__", parts[0])]
    for i in range(1, len(parts), 2):
        out.append((parts[i], parts[i] + parts[i + 1]))
    return out


def listing(mod, addr):
    try:
        r = subprocess.run([sys.executable, f"{KIT}/wlist.py", mod, addr],
                           cwd=REPO, capture_output=True, text=True, timeout=300)
        return r.stdout or ""
    except Exception:
        return ""


def features(text):
    """Mnemonics and shapes present, as a weighted vocabulary."""
    vocab = {}
    for m in MNEMONIC.finditer(text.lower()):
        t = m.group(1)
        vocab[t] = vocab.get(t, 0) + 1
    tags = {tag for rx, tag in SHAPES if rx.search(text.lower())}
    return vocab, tags


def show(term):
    """Print the section whose heading contains `term`. This is the escape hatch the generated doc
    advertises; it must stay cheap and forgiving about exact wording."""
    full = open(DOC, encoding="utf-8").read()
    hits = [(t, b) for t, b in sections(full)
            if t != "__preamble__" and term.lower() in t.lower()]
    if not hits:
        low = term.lower()
        hits = [(t, b) for t, b in sections(full) if t != "__preamble__" and low in b.lower()][:2]
    if not hits:
        print(f"no section matching {term!r}", file=sys.stderr)
        return 1
    for _t, b in hits[:3]:
        print(b)
    return 0


ATTEMPT_CAP = int(os.environ.get("ATTEMPT_CAP", "6000"))
DEADENDS = f"{KIT}/worker_src/deadends.md"


def dead_ends(addr):
    """The one row for THIS address, if any.

    These used to sit in core.md as a table covering five specific functions, so every session on
    every other function paid 1.9 KB for evidence that could not apply to it. Address-keyed data
    does not belong in a shared recipe file.
    """
    try:
        txt = open(DEADENDS, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""
    for line in txt.splitlines():
        if line[:8].lower() == addr.lower() and "\t" in line:
            return ("\n## ALREADY RULED OUT ON %s — do not pay to rediscover this\n\n%s\n"
                    % (addr, line.split("\t", 1)[1].strip()))
    return ""


def prior_attempts(mod, addr, best_path):
    """Every earlier attempt at this address, as a diff against the best one, labelled by residue.

    A session was handed exactly one artifact and no idea what had already been tried, so it re-ran
    experiments the previous sessions had already paid for -- and the main thread hit the same wall
    by hand on 02053634, 14 forms deep, when a shape that had already been gated was sitting in
    attempts/. Full sources would blow the doc budget and repeat what the best artifact already
    shows, so each other attempt is a unified diff against it: small, and exactly the compare and
    contrast that makes a second look cheap.
    """
    try:
        sys.path.insert(0, KIT)
        import nearmiss
    except Exception:
        return []
    base_abs = os.path.abspath(best_path) if best_path else None
    cand = []
    for p in sorted(set(glob.glob(f"{SP}/attempts/*{addr}*.cpp")
                        + glob.glob(f"{SP}/wip/*/*{addr}*.cpp")
                        + glob.glob(f"{SP}/handwork/*{addr}*.cpp"))):
        p = p.replace(chr(92), "/")
        if base_abs and os.path.abspath(p) == base_abs:
            continue
        cand.append(p)
    if not cand:
        return []
    cache = nearmiss._cache()
    scored = []
    for p in cand:
        g = nearmiss.verified_gap(mod, addr, p, cache)
        if g is not None and g >= 0:
            scored.append((g, p))
    try:
        json.dump(cache, open(f"{SP}/wlog/nearmiss_verify.json", "w", encoding="utf-8"))
    except OSError:
        pass
    if not scored:
        return []
    scored.sort()
    base_path = best_path or scored[0][1]
    base = open(base_path, encoding="utf-8", errors="ignore").read().splitlines(keepends=True)
    out = ["\n## EVERY EARLIER ATTEMPT AT THIS ADDRESS — what has already been tried\n\n"
           "Each block is a diff FROM the file above, with the residue that form gates at. A form\n"
           "already listed here has been measured: do not re-derive it, and do not re-test a form\n"
           "whose diff you are about to reproduce. A worse residue is still evidence -- it tells\n"
           "you which direction costs bytes.\n\n"]
    spent = 0
    for g, p in scored:
        if os.path.abspath(p) == os.path.abspath(base_path):
            continue
        other = open(p, encoding="utf-8", errors="ignore").read().splitlines(keepends=True)
        d = list(difflib.unified_diff(base, other, "best", os.path.basename(p), n=2))
        if not d:
            continue
        body = "".join(d)
        if len(body) > 2500:
            body = body[:2500] + "\n[diff truncated]\n"
        blk = ("\n### %s — gates %s\n\n```diff\n%s\n```\n"
               % (os.path.basename(p), "MATCH" if g == 0 else "%d bytes" % g, body.rstrip()))
        if spent + len(blk) > ATTEMPT_CAP:
            out.append("\n[%d further attempt(s) omitted for length]\n" % (len(scored) - len(out)))
            break
        out.append(blk)
        spent += len(blk)
    return out if len(out) > 1 else []


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--show":
        return show(" ".join(sys.argv[2:]))
    if len(sys.argv) < 3:
        print(__doc__.strip().splitlines()[2])
        return 2
    mod, addr = sys.argv[1], sys.argv[2].lower()
    stats = "--stats" in sys.argv
    lean = "--lean" in sys.argv

    lst = listing(mod, addr)
    if not lst.strip():
        print(f"recipe_select: no listing for {mod} {addr}; falling back to full doc",
              file=sys.stderr)
        return 3
    tvocab, ttags = features(lst)

    full = open(DOC, encoding="utf-8").read()
    kept, dropped = [], []
    for title, body in sections(full):
        if title == "__preamble__" or any(r in title for r in RESIDENT):
            kept.append((title, body, "resident"))
            continue
        if lean:
            dropped.append((title, body, 0.0))
            continue
        svocab, stags = features(body)
        score = 0.0
        for t in svocab:
            if t in tvocab:
                score += 0.25 if t in COMMON else 1.0
        score += 2.0 * len(stags & ttags)
        # DELIBERATELY GENEROUS. Sections are cheap to keep and expensive to lose -- the failure this
        # script exists to fix is knowledge NOT reaching the worker. A single shared uncommon
        # mnemonic (1.0), one shared shape (2.0), or a handful of common ones (0.25 each) all clear
        # the bar. Tighten this only against measured match-rate, never to save tokens.
        (kept if score >= 0.5 else dropped).append((title, body, round(score, 2)))

    # A BUDGET, BECAUSE DELIVERY IS THE WHOLE POINT. Selection alone still produced a 985-line doc,
    # and the measured Read cutoff sits between line 666 and 1236 -- so the tail, INCLUDING the index
    # of what was left out, would have been truncated away exactly like the levers were. Resident
    # sections are never budgeted; selected recipes are taken best-scoring first until the budget is
    # gone, and whatever does not fit is listed in the index rather than silently lost.
    # THE CAP IS MEASURED, NOT GUESSED. A worker transcript recorded the Read of this doc returning
    # `"numLines": 832, "totalLines": 1876, "truncatedByTokenCap": true` -- 58,479 characters
    # delivered out of 120 KB. Everything past that was silently dropped, which is exactly how 42
    # promoted levers reached zero sessions. Budget to 50 KB so the whole doc arrives with margin.
    TOTAL_BUDGET = 50000
    # CARRY THE PREVIOUS SESSION'S HANDOFF. This file is the ONLY thing a worker is told to read, so
    # it is where a session that ran out of context leaves what it learned -- and regenerating it on
    # the next claim used to delete that. 0204cd60 lost a struct layout, six callee signatures and
    # 20 of 33 tag handlers that had cost $3.44, which is why a function this size can never
    # converge: every pass starts from the scaffold again.
    handoff = ""
    prev = f"{CACHE}/{mod}_{addr}.md"
    if os.path.exists(prev):
        old = open(prev, encoding="utf-8", errors="ignore").read()
        i = old.find(HANDOFF_MARK)
        if i >= 0:
            j = old.find(HANDOFF_END, i)
            if j < 0:
                j = old.find("\n## ", i + len(HANDOFF_MARK))
                handoff = old[i:] if j < 0 else old[i:j + 1]
            else:
                handoff = old[i:j + len(HANDOFF_END)]
            if len(handoff) > HANDOFF_CAP:
                handoff = (handoff[:HANDOFF_CAP]
                           + "\n[handoff truncated to %d chars]\n" % HANDOFF_CAP + HANDOFF_END)
    resident = [(t, b, s) for t, b, s in kept if s == "resident"]
    # THE ATTEMPT BLOCKS COME OUT OF THE RECIPE BUDGET, not on top of it. They are written ABOVE the
    # recipes, and a worker's Read is truncated from the BACK -- so charging them nothing pushes the
    # recipes off the end of the doc, which is the failure that once left them reaching zero
    # sessions. Build them here so their real length is known before the budget is set.
    attempt_blocks = prior_attempts(mod, addr, f"{SP}/clsbest/{addr}.cpp"
                                    if os.path.exists(f"{SP}/clsbest/{addr}.cpp") else None)
    # A HARD CEILING ON THE WHOLE DOC. `max(8000, TOTAL_BUDGET - rbytes)` is a FLOOR, so a large
    # resident+handoff+attempts total still got 8000 more piled on it and nothing bounded the sum:
    # 11 of 164 cached docs were over the ~58KB Read limit, the largest 67KB, which means those
    # sessions never saw the selected recipes at all -- the exact failure this file exists to stop.
    # Squeeze in a fixed order when it does not fit: attempts first (regenerable from disk), then
    # the handoff, and never the resident procedure.
    rb = sum(len(b) for _t, b, _s in resident)
    avail = DOC_MAX - rb - RESERVE
    hcap = max(0, int(avail * 0.70))
    if len(handoff) > hcap:
        handoff = handoff[:hcap] + "\n[handoff truncated to fit the doc]\n" + HANDOFF_END
    dead = dead_ends(addr)
    # THE ATTEMPT DIFFS GO TO A SIDECAR FILE, NOT INTO THE BUDGET. Squeezing them against the ~58KB
    # Read limit meant dropping evidence that had already been paid for. Splitting recipes behind an
    # OPTIONAL fetch was measured and failed -- 0 of 55 sessions opened it -- so the split is safe
    # only in this direction: the recipes never move, the sidecar is pure addition, and the primary
    # doc keeps a compact index of it so a session that never opens it still knows what exists.
    sidecar = f"{CACHE}/{mod}_{addr}.attempts.md"
    index = ""
    if attempt_blocks:
        rows = re.findall(r"(?m)^### (\S+) — gates (.+)$", "".join(attempt_blocks))
        with open(sidecar, "w", encoding="utf-8", newline="\n") as sf:
            sf.write("# Earlier attempts at %s — diffs from the best artifact\n" % addr)
            for b in attempt_blocks:
                sf.write(b)
        index = ("\n## EARLIER ATTEMPTS AT THIS ADDRESS — READ `%s` NEXT\n\n"
                 "%d earlier form(s) have been gated on this address. Each is a diff from the best\n"
                 "artifact, with the residue it produced. Read that file BEFORE writing anything:\n"
                 "reproducing one of these costs a compile to learn what is already written down.\n\n"
                 % (sidecar.replace(chr(92), "/"), len(rows)))
        for name, gate in rows:
            index += "  - %s -> %s\n" % (name, gate)
        index += "\n"
    attempt_blocks = []
    rbytes = rb + len(handoff) + len(dead) + len(index)
    budget = max(2000, min(TOTAL_BUDGET, DOC_MAX - RESERVE - rbytes))
    scored = sorted([(t, b, s) for t, b, s in kept if s != "resident"],
                    key=lambda x: -x[2])
    selected, spent = [], 0
    for t, b, s in scored:
        if spent + len(b) > budget:
            dropped.append((t, b, s))
            continue
        selected.append((t, b, s))
        spent += len(b)

    os.makedirs(CACHE, exist_ok=True)
    out = f"{CACHE}/{mod}_{addr}.md"
    with open(out, "w", encoding="utf-8") as fh:
        pre = next((b for t, b, _s in resident if t == "__preamble__"), "")
        fh.write(pre)
        # START FROM THE BEST ARTIFACT, NOT THE SCAFFOLD. The sweep keeps each address's closest
        # surviving source in clsbest/ with its gate rank beside it. A worker handed a cold scaffold
        # for a function already sitting at BYTEDIFF 4 pays the whole decompilation again to reach a
        # place the pipeline had already reached for free.
        _best = f"{SP}/clsbest/{addr}.cpp"
        if os.path.exists(_best):
            _rank = ""
            try:
                _t, _m = open(_best + ".rank").read().split()
                _rank = ("BYTEDIFF %s" % _m) if _t == "1" else ("%s bytes off on SIZE" % _m)
            except (OSError, ValueError):
                pass
            fh.write("\n## START HERE — an existing attempt is already close%s\n\n"
                     "    %s\n\n"
                     "Copy that file and work from it. It gates %s. Do NOT start from the\n"
                     "scaffold: the shape, the callee signatures and the struct layouts in it are\n"
                     "already right, and re-deriving them costs the whole session.\n"
                     % ((" (%s)" % _rank) if _rank else "", _best.replace(chr(92), "/"), _rank or "close to the target"))
            # A SYMBOL FAULT IS INVISIBLE UNTIL THE BYTES MATCH. wgate reports BYTEDIFF first and
            # never reaches the link, so a candidate parked a few bytes out can also carry a callee
            # that cannot resolve -- 33 of 113 kept artifacts do. ov015:0218ee38 had two, and they
            # only appeared once its codegen was byte-exact, costing two extra rounds.
            try:
                sys.path.insert(0, f"{KIT}/pad")
                import symaudit
                _faults = symaudit.audit(_best)
            except Exception:
                _faults = []
            if _faults:
                fh.write("\nFix these FIRST -- they cannot resolve at link time and the gate cannot\n"
                         "show them to you until the bytes already match:\n\n")
                for _name, _why in _faults[:8]:
                    fh.write("  - %s: %s\n" % (_name, _why))
                fh.write("\n")
        # A TRANSLITERATION IS SEMANTICS, NOT A CANDIDATE. Measured over the whole massive band: a
        # draft that is the right SIZE is still ~75% different byte-for-byte, so a worker told only
        # that it exists will try to make it match and lose the session to it.
        _tr = f"{SP}/lab/trans_{addr}.cpp"
        if os.path.exists(_tr):
            fh.write("\n## A FREE TRANSLITERATION OF THIS FUNCTION EXISTS — READ IT FOR SEMANTICS\n\n"
                     "    %s\n\n"
                     "Compiling C carrying the ROM's exact branch graph, every callee resolved to\n"
                     "its real name, and the stack frame modelled as one array. It tells you what\n"
                     "each block DOES — which callee, which field, which loop bound — so you never\n"
                     "decode that by hand.\n\n"
                     "It is NOT a candidate and it will not gate. It colours registers as a register\n"
                     "machine where the ROM is idiomatic. Read it, then write idiomatic C from a\n"
                     "sibling. Trying to make the transliteration itself match is the one way to\n"
                     "waste it.\n" % _tr.replace(chr(92), "/"))
        if dead:
            fh.write(dead)
        if index:
            fh.write(index)
        if handoff:
            fh.write("\n" + handoff.rstrip() + "\n")
        # THE INDEX GOES FIRST, NOT LAST. Naming a file and hoping was measured to fail -- 0 of 55
        # sessions opened the recipes file under SPLIT_RECIPES -- and anything at the BACK of a doc
        # this size may never be read at all. So the escape hatch leads.
        if dropped:
            fh.write("\n## RECIPES NOT INCLUDED FOR THIS FUNCTION — FETCH ANY OF THEM\n"
                     "This doc was filtered to the instruction shapes in THIS function's listing.\n"
                     "THE FILTER IS A HEURISTIC AND IT CAN BE WRONG. If your diff resembles one of\n"
                     "the titles below, read it — one command, no model tokens:\n\n"
                     f"    python {KIT}/recipe_select.py --show \"<part of the title>\"\n\n"
                     "Not included here (most likely first):\n")
            for t, _b, s in sorted(dropped, key=lambda x: -x[2]):
                fh.write(f"  - {t.lstrip('# ').rstrip()}\n")
            fh.write("\n")
        for t, b, _s in resident:
            if t != "__preamble__":
                fh.write(b)
        for _t, b, _s in selected:
            fh.write(b)
    size = os.path.getsize(out)
    if size > DOC_MAX and selected and not os.environ.get("DOC_RESERVE_RETRY"):
        env = dict(os.environ, DOC_RESERVE=str(RESERVE + size - DOC_MAX + 1000), DOC_RESERVE_RETRY="1")
        return subprocess.run([sys.executable] + sys.argv, env=env).returncode
    # Forward slashes: pull_worker.sh tests this path with `[ -f ]` under msys bash, where a
    # backslash path is not a path at all -- it silently failed the test and every claim fell back
    # to the truncated static doc.
    print(out.replace("\\", "/"))
    if stats:
        print(f"  kept {len(kept)} sections ({size} B, ~{size//4} tok), "
              f"dropped {len(dropped)} of {len(kept)+len(dropped)}", file=sys.stderr)
        print(f"  target shapes: {sorted(ttags)}", file=sys.stderr)
        for t, _b, s in sorted(dropped, key=lambda x: -x[2] if isinstance(x[2], float) else 0)[:5]:
            print(f"  dropped {s}: {t[:64]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
