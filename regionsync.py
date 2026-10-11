"""Port matched files between regions, keeping each port only if the receiving region builds.

    python regionsync.py [decomp tree]     default: the current directory

First into usa: a file delinked in another region but not in config/usa, owning one `.text` range and
no data, whose `// USA:` tag names a usa function, gets a usa block at that function when the function
has the same size or wgate.py matches the file against it.
Then from usa to each region with a port tool that has --sync and an extracted ROM: run the sync
(after building the region first when its tool reads that region's objects). Each port is committed
when the receiving region's `ninja check` and `ninja sha1` pass. When they do not, restore the config
of each module whose check failed and build again; restore the whole port when that is still red or
no module is named. The tree is reconfigured for usa afterwards. Each file another region delinks that
stays out of usa prints a `NOT IN USA <file>: <why>` line. Exit 0: every region in sync, ported or
skipped. 1: a port was red and was restored, in part or in full, or a file is NOT IN USA.
"""
import glob
import os
import re
import subprocess
import sys
import time

import kitpaths

PORTS = {"eur": "tools/port_eur_config.py", "jpn": "tools/port_jpn_config.py"}
READS_OBJECTS = {"jpn"}
FAILED_CHECK = re.compile(r"Check (ARM9 main|ITCM|DTCM|overlay (\d+)): (?!OK)")
PORTED_FILE = re.compile(r"^\+[^+\s].*:\r?$", re.M)
BLOCK = re.compile(r"(?m)^(src/\S+):[ \t]*\r?\n((?:[ \t]+\S.*(?:\r?\n|$))*)")
TEXT = re.compile(r"\.text\s+start:0x([0-9a-fA-F]+)\s+end:0x([0-9a-fA-F]+)")
SECTION = re.compile(r"(?m)^[ \t]+(\.\w+)\s")
FUNCTION = re.compile(r"(?m)^(\S+) kind:function\([^)]*size=0x([0-9a-fA-F]+)[^)]*\)\s+addr:0x([0-9a-fA-F]+)")
USA_TAG = re.compile(r"(?m)^// USA: (\S+)")
TAG_ADDRESS = re.compile(r"func_(?:ov\d{3}_)?([0-9a-fA-F]{8})")


def run(*args):
    r = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def configure(region):
    compiler = os.environ.get("DQIX_PREINSTALLED_COMPILER")
    wine = os.environ.get("DQIX_WINE")
    return run(sys.executable, "tools/configure.py", region, "--no-extract",
               *(["--compiler", compiler] if compiler else []), *(["-w", wine] if wine else []))


def green():
    code, out = run("ninja", "check")
    if code:
        return False, out
    code, sha = run("ninja", "sha1")
    return code == 0 and "OK" in sha, out + sha


def ported(region):
    return len(PORTED_FILE.findall(run("git", "diff", "-U0", "--", f"config/{region}")[1]))


def failed_modules(log, region):
    root = f"config/{region}/arm9"
    folders = {root if m[1] == "ARM9 main" else f"{root}/overlays/ov{int(m[2]):03d}" if m[2] else f"{root}/{m[1].lower()}"
               for m in FAILED_CHECK.finditer(log)}
    changed = run("git", "diff", "--name-only", "--", root)[1].split()
    return [path for path in changed if os.path.dirname(path) in folders]


def read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def usa_gate(module, start, src):
    r = subprocess.run([sys.executable, f"{kitpaths.KIT}/wgate.py", module, f"{start:08x}", os.path.abspath(src)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, DQIX_REPO=os.getcwd()))
    lines = (r.stdout + r.stderr).strip().splitlines()
    return next((l for l in lines if l.startswith(("MATCH", "RESIDUE"))), lines[-1] if lines else "no output")


def usa_block(src, body, functions, taken, module):
    text = TEXT.findall(body)
    if not os.path.isfile(src):
        return "the source file is missing"
    if len(text) != 1 or set(SECTION.findall(body)) != {".text"}:
        return f"owns {', '.join(SECTION.findall(body))}, not one .text range"
    tag = USA_TAG.search(read(src))
    if not tag:
        return "no `// USA:` tag"
    raw = TAG_ADDRESS.fullmatch(tag.group(1))
    by_address = {start: (start, size) for start, size in functions.values()}
    hit = by_address.get(int(raw.group(1), 16)) if raw else functions.get(tag.group(1))
    if not hit:
        return f"its tag {tag.group(1)} names no usa function in this module"
    start, size = hit
    if any(a < start + size and start < b for a, b in taken):
        return f"{tag.group(1)} overlaps a file usa already delinks"
    lo, hi = (int(v, 16) for v in text[0])
    if hi - lo != size:
        verdict = usa_gate(module, start, src)
        if verdict != "MATCH":
            return f"{hi - lo:#x} bytes here, {tag.group(1)} is {size:#x} in usa, and the usa gate says {verdict}"
    taken.append((start, start + size))
    lines = [TEXT.sub(f".text start:0x{start:08x} end:0x{start + size:08x}", l) for l in body.splitlines()]
    return [f"{src}:"] + lines


def into_usa():
    targets = {p.replace("\\", "/"): read(p) for p in glob.glob("config/usa/arm9/**/delinks.txt", recursive=True)}
    wired = {src for text in targets.values() for src, _ in BLOCK.findall(text)}
    skipped = {}
    for region in PORTS:
        for path in sorted(glob.glob(f"config/{region}/arm9/**/delinks.txt", recursive=True)):
            target = "config/usa/" + path.replace("\\", "/").split("/", 2)[2]
            symbols = target[:-len("delinks.txt")] + "symbols.txt"
            functions = ({n: (int(a, 16), int(s, 16)) for n, s, a in FUNCTION.findall(read(symbols))}
                         if os.path.isfile(symbols) else {})
            taken = [(int(a, 16), int(b, 16)) for _, body in BLOCK.findall(targets.get(target, ""))
                     for a, b in TEXT.findall(body)]
            overlay = re.search(r"/overlays/ov(\d{3})/", target)
            module = overlay.group(1) if overlay else "main"
            for src, body in BLOCK.findall(read(path)):
                if src in wired:
                    continue
                block = usa_block(src, body, functions, taken, module) if target in targets else f"no {target}"
                if isinstance(block, str):
                    skipped.setdefault(src, f"{region}: {block}")
                    continue
                nl = "\r\n" if "\r\n" in targets[target] else "\n"
                targets[target] = targets[target].rstrip("\r\n") + nl * 2 + nl.join(block) + nl
                wired.add(src)
                skipped.pop(src, None)
    for target, text in targets.items():
        if text != read(target):
            with open(target, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
    return skipped


def settle(region, out):
    if not run("git", "status", "--porcelain", "--", f"config/{region}")[1].strip():
        return 0, f"{region}: in sync"
    files = ported(region)
    configure(region)
    ok, log = green()
    dropped = [] if ok else failed_modules(log, region)
    if dropped:
        run("git", "checkout", "--", *dropped)
        configure(region)
        ok, retry = green()
        ok = ok and bool(run("git", "status", "--porcelain", "--", f"config/{region}")[1].strip())
        log += retry
    if dropped or not ok:
        path = f"{kitpaths.SP}/wlog/regionsync_{region}_{time.strftime('%m%d_%H%M')}.log"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(out + log)
    if not ok:
        run("git", "checkout", "--", f"config/{region}")
        return 1, f"{region}: RED after porting {files} files, restored; log {path}"
    files = ported(region)
    run("git", "add", "--", f"config/{region}")
    message = f"Port {files} matched files to {region.upper()}" if files else f"Port USA names to {region.upper()}"
    code, out = run("git", "commit", "-q", "-m", message)
    if code:
        run("git", "reset", "-q", "--", f"config/{region}")
        run("git", "checkout", "--", f"config/{region}")
        return 1, f"{region}: commit failed, restored: {out.strip()[-200:]}"
    if dropped:
        return 1, f"{region}: {message}; restored red {', '.join(sorted({os.path.dirname(p) for p in dropped}))}; log {path}"
    return 0, f"{region}: {message}"


def sync(region, tool):
    if region == "usa":
        skipped = into_usa()
        code, line = settle(region, "")
        if skipped:
            line = (line.replace("usa: in sync", "usa: nothing ported") + f"; {len(skipped)} file(s) NOT IN USA:\n"
                    + "\n".join(f"  NOT IN USA {src}: {why}" for src, why in sorted(skipped.items())))
        return code | bool(skipped), line
    if not os.path.isfile(tool) or "--sync" not in run(sys.executable, tool, "--help")[1]:
        return 0, f"{region}: {tool} has no --sync, not ported"
    if not os.path.isdir(f"extract/{region}"):
        return 0, f"{region}: no extracted {region} ROM, not ported"
    if region in READS_OBJECTS:
        configure(region)
        run("ninja", "-k", "0", "check")
    code, out = run(sys.executable, tool, "--sync")
    if code:
        run("git", "checkout", "--", f"config/{region}")
        return 1, f"{region}: {tool} --sync failed: {out.strip().splitlines()[-1:]}"
    return settle(region, out)


def main():
    if len(sys.argv) > 2 or sys.argv[1:2] in (["-h"], ["--help"]):
        sys.exit(__doc__)
    if len(sys.argv) == 2:
        os.chdir(sys.argv[1])
    status = 0
    try:
        for region, tool in [("usa", None), *PORTS.items()]:
            code, line = sync(region, tool)
            status |= code
            print(line)
    finally:
        configure("usa")
    sys.exit(status)


if __name__ == "__main__":
    main()
