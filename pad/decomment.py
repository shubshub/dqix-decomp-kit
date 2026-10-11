import re
import sys

KEEP = re.compile(r"^\s*//\s*(USA:|KEEP-NAME)")


def strip(src):
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1])
            i = j + 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif src.startswith("//", i):
            line_start = src.rfind("\n", 0, i) + 1
            j = src.find("\n", i)
            j = n if j < 0 else j
            if KEEP.match(src[line_start:j]):
                out.append(src[i:j])
            i = j
        else:
            out.append(c)
            i += 1
    lines, prev_blank = [], True
    orig = src.split("\n")
    for k, ln in enumerate("".join(out).split("\n")):
        ln = ln.rstrip()
        blank = not ln.strip()
        if blank and (prev_blank or (k < len(orig) and orig[k].strip())):
            continue
        lines.append(ln)
        prev_blank = blank
    return "\n".join(lines).rstrip("\n") + "\n"


if __name__ == "__main__":
    text = open(sys.argv[1], encoding="utf-8").read()
    open(sys.argv[2], "w", encoding="utf-8", newline="\n").write(strip(text))
