import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import cleancheck
import kitpaths
import plausible

KIT, SP, REPO = kitpaths.KIT, kitpaths.SP, kitpaths.REPO
QUEUE = f"{SP}/wlog/clean_queue.tsv"
LOG = f"{SP}/wlog/clean.log"
CLAIMS = f"{SP}/clean/claims"
MANIFEST = f"{SP}/clean/manifest.tsv"
WORK = f"{SP}/handwork/mechclean"
USAGE = """python mechclean.py [-j N]

Rewrites, without a worker, the plausible.py kinds that have one natural form, for every row of
$SP/wlog/clean_queue.tsv no worker has finished or claimed: NOOP-ADDR-CAST,
TERNARY-SELF, SELF-ASSIGN, INLINE-LOCAL-RETURN. An inline whose local the ROM needs gets the
compiled-out assert instead (`{ SDK_NULL_ASSERT(p); return e; }`: any statement before the return
makes mwcc expand the body where the call statement starts). A rewrite is kept only when the function
still gates MATCH and cleancheck.py accepts it; it goes to $SP/clean/manifest.tsv, a staging row's
to $SP/staging/<module>/, and clean.log records CLEAN."""

LVAL = r"[A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*|\[[^\]\[]*\])*"


def noop_addr_cast(line):
    return re.sub(r"\*\s*\(\s*[A-Za-z_][\w\s]*\*\s*\)\s*&\s*(%s)" % LVAL, r"\1", line)


def ternary_self(line):
    m = re.match(r"^(\s*)(%s)\s*=\s*(.+?)\s*\?\s*(.+?)\s*:\s*(.+?)\s*;\s*$" % LVAL, line)
    if not m:
        return line
    ind, lhs, cond, yes, no = m.groups()
    if no == lhs:
        return "%sif (%s) %s = %s;" % (ind, cond, lhs, yes)
    if yes == lhs:
        return "%sif (!(%s)) %s = %s;" % (ind, cond, lhs, no)
    return line


def self_assign(line):
    return None if re.match(r"^\s*(%s)\s*=\s*\1\s*;\s*$" % LVAL, line) else line


LINE_FIX = {"NOOP-ADDR-CAST": noop_addr_cast,
            "TERNARY-SELF": ternary_self, "SELF-ASSIGN": self_assign}


LOCAL_RETURN = re.compile(r"(?:\(([^()]*)\)(\s*const)?\s*)?\{\s*(?!static\b)(?:const\s+)?[A-Za-z_][\w\s\*]*?\b"
                          r"([A-Za-z_]\w*)\s*=\s*([^;{}]+);\s*return\s+\3\s*;\s*\}")


def null_assert(params, member):
    for param in params.split(","):
        m = re.search(r"[*&]\s*([A-Za-z_]\w*)\s*$", param)
        if m:
            return "SDK_NULL_ASSERT(%s); " % m.group(1)
    return "SDK_NULL_ASSERT(this); " if member else None


def inline_local_return(text, asserted=False):
    def fix(m):
        whole = m.group(0)
        head = whole[:whole.index("{")]
        stmt = null_assert(m.group(1) or "", bool(m.group(2))) if asserted else ""
        if stmt is None:
            return whole
        return "%s{ %sreturn %s; }" % (head, stmt, m.group(4))
    new = LOCAL_RETURN.sub(fix, text)
    if not plausible.gained(text, new):
        return new
    for m in reversed(list(LOCAL_RETURN.finditer(text))):
        trial = text[:m.start()] + fix(m) + text[m.end():]
        if not plausible.gained(text, trial):
            text = trial
    return text


def rewrite(text, kinds, asserted=False):
    lines = text.split("\n")
    out = []
    for ln in lines:
        new = ln
        for k in kinds:
            if k in LINE_FIX and new is not None:
                new = LINE_FIX[k](new)
        if new is not None:
            out.append(new)
    text = "\n".join(out)
    if "INLINE-LOCAL-RETURN" in kinds:
        text = inline_local_return(text, asserted)
    return text


def gate(mod, addr, path):
    r = subprocess.run([sys.executable, f"{KIT}/wgate.py", mod, addr, path], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=dict(os.environ, WGATE_ALLOW_COMMITTED="1"))
    return any(ln.startswith("MATCH") for ln in (r.stdout + r.stderr).splitlines())


def finished(addr):
    try:
        return re.search(r" (CLEAN|KEEP|BLOCKED|SKIP) %s " % addr, open(LOG, encoding="utf-8").read()) is not None
    except OSError:
        return False


def candidates(kinds):
    for subset in [kinds] + ([[k] for k in kinds] if len(kinds) > 1 else []):
        for asserted in (False, True) if "INLINE-LOCAL-RETURN" in subset else (False,):
            yield subset, asserted


def accepted(mod, addr, path, text, new, staged):
    return (gate(mod, addr, path) and cleancheck.accepted(text.split("\n"), new.split("\n"), set())[0]
            and not (staged and plausible.blocking(new)))


def job(row, force=False):
    mod, addr, rel = row[:3]
    staged = row[5:6] == ["staging"]
    if not force and finished(addr):
        return None
    try:
        os.mkdir(f"{CLAIMS}/{addr}")
    except OSError:
        return None
    try:
        if staged:
            base = ""
            text = open(rel, encoding="utf-8", errors="replace").read()
        else:
            base = subprocess.run(["git", "-C", REPO, "rev-parse", f"origin/decomp-matching:{rel}"],
                                  capture_output=True, text=True).stdout.strip()
            text = subprocess.run(["git", "-C", REPO, "cat-file", "blob", base], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace").stdout
        found = sorted({k for _, k, _ in plausible.findings(text)} & (set(LINE_FIX) | {"INLINE-LOCAL-RETURN"}))
        if not found:
            return None
        path = f"{WORK}/{mod}/{os.path.basename(rel)}"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tried = False
        for kinds, asserted in candidates(found):
            new = rewrite(text, kinds, asserted)
            if new == text:
                continue
            tried = True
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(new)
            if accepted(mod, addr, path, text, new, staged):
                break
        else:
            return f"{addr} kept for a worker ({','.join(found)})" if tried else None
        if staged:
            dest = f"{SP}/staging/{'main' if mod == 'main' else 'ov' + mod}/{os.path.basename(rel)}"
        else:
            dest = f"{SP}/clean/{rel}"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new)
        if staged:
            os.remove(rel)
        else:
            with open(MANIFEST, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(f"{rel}\t{base}\tmechanical: {','.join(kinds)}\n")
        with open(LOG, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"{time.strftime('%H:%M')} CLEAN {addr} $0 mechanical {','.join(kinds)}\n")
        return f"{addr} CLEAN ({','.join(kinds)})"
    finally:
        os.rmdir(f"{CLAIMS}/{addr}")


def main():
    if len(sys.argv) not in (1, 3) or (len(sys.argv) == 3 and sys.argv[1] != "-j"):
        sys.exit(USAGE)
    os.makedirs(CLAIMS, exist_ok=True)
    rows = [ln.rstrip("\n").split("\t") for ln in open(QUEUE, encoding="utf-8") if ln.count("\t") >= 2]
    with ThreadPoolExecutor(int(sys.argv[2]) if len(sys.argv) == 3 else 3) as pool:
        for result in pool.map(job, rows):
            if result:
                print(result, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
