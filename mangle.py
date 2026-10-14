import re


BUILTIN_CODES = set("vwbcahstijlmxynofdegz")


def mangled_arity(name):
    s = name
    if not s.startswith("_Z"):
        return None
    pos = [2]
    subs = []

    def peek():
        return s[pos[0]] if pos[0] < len(s) else ""

    def source_name():
        m = re.match(r"\d+", s[pos[0]:])
        if not m:
            raise ValueError
        ln = int(m.group(0))
        pos[0] += len(m.group(0)) + ln
        if pos[0] > len(s):
            raise ValueError

    def template_args():
        pos[0] += 1
        while peek() != "E":
            if peek() == "":
                raise ValueError
            if peek() == "L":
                end = s.find("E", pos[0])
                if end < 0:
                    raise ValueError
                pos[0] = end + 1
            else:
                ptype()
        pos[0] += 1

    def subst():
        pos[0] += 1
        c = peek()
        if c in "tabsiod" and c != "":
            pos[0] += 1
            if c == "t" and peek().isdigit():
                source_name()
            return
        while peek() != "_":
            if peek() == "":
                raise ValueError
            pos[0] += 1
        pos[0] += 1

    def unqualified():
        c = peek()
        if c.isdigit():
            source_name()
        elif c == "C" or c == "D":
            pos[0] += 2
        elif c == "S":
            subst()
        else:
            raise ValueError
        if peek() == "I":
            template_args()

    def nested():
        pos[0] += 1
        while peek() in ("r", "V", "K"):
            pos[0] += 1
        while peek() != "E":
            if peek() == "":
                raise ValueError
            unqualified()
        pos[0] += 1

    def ptype():
        c = peek()
        if c in BUILTIN_CODES:
            pos[0] += 1
        elif c in ("P", "R", "O", "K", "V", "r"):
            pos[0] += 1
            ptype()
        elif c.isdigit():
            source_name()
            if peek() == "I":
                template_args()
        elif c == "N":
            nested()
        elif c == "S":
            subst()
            if peek() == "I":
                template_args()
        elif c == "F":
            pos[0] += 1
            if peek() == "Y":
                pos[0] += 1
            while peek() != "E":
                if peek() == "":
                    raise ValueError
                ptype()
            pos[0] += 1
        elif c == "A":
            pos[0] += 1
            while peek().isdigit():
                pos[0] += 1
            if peek() != "_":
                raise ValueError
            pos[0] += 1
            ptype()
        elif c == "M":
            pos[0] += 1
            ptype()
            ptype()
        elif c == "T":
            pos[0] += 1
            while peek() != "_":
                if peek() == "":
                    raise ValueError
                pos[0] += 1
            pos[0] += 1
        elif c == "u":
            pos[0] += 1
            source_name()
        else:
            raise ValueError

    try:
        if peek() == "N":
            nested()
        else:
            unqualified()
        count = 0
        if s[pos[0]:] == "v":
            return 0
        while pos[0] < len(s):
            if s[pos[0]:].startswith("z"):
                pos[0] += 1
                continue
            ptype()
            count += 1
        return count
    except (ValueError, IndexError, RecursionError):
        return None


GLOBAL_FIELDS = {}
GLOBAL_STRUCTS = set()
