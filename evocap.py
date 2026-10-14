import os as _kpos, sys as _kpsys
_kpsys.path.insert(0, _kpos.path.dirname(_kpos.path.abspath(__file__)))
import kitpaths as _kp
import glob
import json
import os
import re
import sys

SP = _kp.SP
KIT = _kp.KIT
PROJECTS = _kp.CLAUDE_PROJECTS
PRICE = {"claude-opus": 5e-6, "claude-sonnet": 3e-6, "claude-haiku": 1e-6}


def knob(name, default):
    try:
        return type(default)(open(f"{SP}/{name}").read().split()[0])
    except (OSError, ValueError, IndexError):
        return default


def usd(model, u):
    rate = next((v for k, v in PRICE.items() if (model or "").startswith(k)), PRICE["claude-opus"])
    return rate * ((u.get("input_tokens") or 0) + 2 * (u.get("cache_creation_input_tokens") or 0)
                   + 0.1 * (u.get("cache_read_input_tokens") or 0) + 5 * (u.get("output_tokens") or 0))


def agent_file_cost(path):
    usage, first_ts = {}, None
    for line in open(path, encoding="utf-8", errors="ignore"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        first_ts = first_ts or d.get("timestamp")
        msg = d.get("message") if d.get("type") == "assistant" else None
        if not isinstance(msg, dict) or not msg.get("id") or not msg.get("usage"):
            continue
        prev = usage.get(msg["id"])
        if prev is None:
            usage[msg["id"]] = (msg.get("model"), dict(msg["usage"]))
        else:
            for k, v in msg["usage"].items():
                if isinstance(v, int) and v > (prev[1].get(k) or 0):
                    prev[1][k] = v
    return sum(usd(m, u) for m, u in usage.values()), first_ts


def scorer_fitnesses(result):
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except ValueError:
        return []
    rows = data.get("results", []) if isinstance(data, dict) else []
    return [r["fitness"] for r in rows if isinstance(r, dict) and isinstance(r.get("fitness"), int)]


def runs_for(addr):
    runs = []
    for wf in glob.glob(f"{PROJECTS}/*/*/subagents/workflows/wf_*"):
        journal = os.path.join(wf, "journal.jsonl")
        try:
            text = open(journal, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if f"/evo/{addr}/" not in text or '"label": "score-g0"' not in text.replace('":"', '": "'):
            continue
        labels, base, later = {}, [], []
        for line in text.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "started":
                labels[e.get("agentId")] = e.get("label") or ""
            elif e.get("type") == "result":
                label = labels.get(e.get("agentId"), "")
                m = re.match(r"score-g(\d+)$", label)
                if m:
                    (base if m.group(1) == "0" else later).extend(scorer_fitnesses(e.get("result")))
        cost, start = 0.0, None
        for f in glob.glob(os.path.join(wf, "agent-*.jsonl")):
            c, ts = agent_file_cost(f)
            cost += c
            if ts and (start is None or ts < start):
                start = ts
        runs.append({"wf": os.path.basename(wf), "start": start or "", "cost": cost,
                     "base": min(base) if base else None, "best": min(later) if later else None})
    return sorted(runs, key=lambda r: r["start"])


def reserve(mod, addr, path=None):
    path = path or f"{SP}/wlog/evolve_queue.txt"
    entry = f"{'main' if mod == 'main' else 'ov' + mod.removeprefix('ov')}:{addr}"
    try:
        held = {l.strip().split(":")[-1].lower() for l in open(path, encoding="utf-8") if ":" in l}
    except OSError:
        held = set()
    if addr not in held:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(entry + "\n")


def main():
    if len(sys.argv) < 2:
        print("usage: evocap.py <addr> [--reserve <main|NNN>]")
        return 2
    addr = sys.argv[1].lower()
    if "--reserve" in sys.argv:
        reserve(sys.argv[sys.argv.index("--reserve") + 1], addr)
    cap_usd = knob("EVOCAP_USD", 150.0)
    cap_flat = knob("EVOCAP_FLAT", 2)
    runs = runs_for(addr)
    spent = sum(r["cost"] for r in runs)
    best_so_far, improved = None, []
    for r in runs:
        start = r["base"] if best_so_far is None else best_so_far
        improved.append(r["best"] is not None and start is not None and r["best"] < start)
        seen = [x for x in (r["base"], r["best"], best_so_far) if x is not None]
        best_so_far = min(seen) if seen else None
    flat = 0
    for up in reversed(improved):
        if up:
            break
        flat += 1
    matched = any(r["best"] == 0 or r["base"] == 0 for r in runs)
    for r in runs:
        print(f"  run {r['wf']} {r['start'][:16]} ${r['cost']:.2f} base {r['base']} best {r['best']}")
    verdict = "STOP" if runs and not matched and (spent >= cap_usd or flat >= cap_flat) else "OK"
    print(f"EVOCAP {verdict} {addr}: ${spent:.2f} over {len(runs)} runs, {flat} latest without a better best "
          f"(caps ${cap_usd:.0f} / {cap_flat})")
    return 1 if verdict == "STOP" else 0


if __name__ == "__main__":
    sys.exit(main())
