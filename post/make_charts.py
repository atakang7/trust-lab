"""Builds the post's charts from the raw run files in results/final. Needs only Python."""
import collections
import datetime as dt
import html
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = pathlib.Path(__file__).resolve().parent / "charts"
NAMES = {"G": "GPT-6 Luna", "H": "Claude Haiku 5.5", "D": "DeepSeek V3.2", "script": "Revert script"}
STYLE = (
    "<style>.tl{--i:#18181b;--m:#6b6b73;--r:#e4ded3;--p:#008f80;--x:#c2410c;--n:#8a8f98;font-family:Inter,system-ui,sans-serif}"
    "[data-theme=dark] .tl,[data-theme=hc-dark] .tl{--i:#f4f4f5;--m:#a1a1aa;--r:#34343a;--p:#14a892;--x:#e0702a;--n:#7d828b}"
    ".tl text{fill:var(--i);font-size:12px}.tl .m{fill:var(--m);font-size:11px}.tl .b{font-weight:600}"
    ".tl .w{fill:#fff;font-size:11px;font-weight:600}.tl .rule{stroke:var(--r);stroke-width:1}"
    ".tl .ok{fill:var(--p)}.tl .bad{fill:var(--x)}.tl .neu{fill:var(--n)}</style>"
)


def load():
    runs = []
    for path in (ROOT / "results/final").glob("*/*/meta.json"):
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta["probe"] = json.loads((path.parent / "probe.json").read_text(encoding="utf-8"))
        meta["gate"] = (path.parent / "gate.log").read_text(encoding="utf-8") if (path.parent / "gate.log").exists() else ""
        runs.append(meta)
    return runs


def esc(value):
    return html.escape(str(value), quote=True)


def svg(name, width, height, label, body, css_width="max(100%,600px)"):
    text = (f'<svg class="tl" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)}" '
            f'xmlns="http://www.w3.org/2000/svg" style="width:{css_width};height:auto;display:block">{STYLE}'
            f'<style>.prose .tl .pg{{display:none}}</style><rect class="pg" x="-4" y="-4" width="{width+8}" height="{height+8}" fill="#fff"/>{body}</svg>')
    (OUT / f"{name}.svg").write_text(text, encoding="utf-8", newline="\n")


def applies(meta):
    start = meta["t0"]
    if not isinstance(start, (int, float)):
        start = dt.datetime.fromisoformat(str(start)).timestamp()
    found = []
    for line in meta["gate"].splitlines():
        if " APPLY OK " in line:
            stamp, rest = line.split(" APPLY OK ", 1)
            found.append((dt.datetime.fromisoformat(stamp).timestamp() - start, json.loads(rest.split(" :: ")[0])))
    return found


def segments(probe):
    out = []
    for t, ok, _ in probe:
        if out and out[-1][2] == bool(ok):
            out[-1][1] = t
            out[-1][3] += 1
        else:
            out.append([t, t, bool(ok), 1])
    return out


def timeline(name, rows, label, tick):
    left, right, top, row = 128, 628, 44, 27
    lo, hi = -10, 125
    x = lambda t: left + (min(hi, max(lo, t)) - lo) / (hi - lo) * (right - left)
    height = top + row * len(rows) + 34
    parts = [f'<rect class="ok" x="{left}" y="6" width="10" height="10" rx="2"/><text class="m" x="{left+15}" y="15">request passed</text>',
             f'<rect class="bad" x="{left+112}" y="6" width="10" height="10" rx="2"/><text class="m" x="{left+127}" y="15">request failed</text>',
             f'<path d="M{left+232} 6l5 5-5 5-5-5z" fill="var(--i)"/><text class="m" x="{left+242}" y="15">fix applied</text>']
    for sec in range(0, 121, 30):
        parts.append(f'<line class="rule" x1="{x(sec):.1f}" x2="{x(sec):.1f}" y1="{top-6}" y2="{top+row*len(rows)}"/>'
                     f'<text class="m" text-anchor="middle" x="{x(sec):.1f}" y="{top+row*len(rows)+16}">{"fault" if sec == 0 else f"{sec}s"}</text>')
    for i, (title, meta) in enumerate(rows):
        y = top + i * row
        parts.append(f'<text x="0" y="{y+14}">{esc(title)}</text>')
        for a, b, ok, count in segments(meta["probe"]):
            word = "passed" if ok else "failed"
            parts.append(f'<rect class="{"ok" if ok else "bad"}" x="{x(a):.1f}" y="{y+3}" width="{max(2, x(b + 1) - x(a) - 1):.1f}" height="15" rx="2">'
                         f'<title>{count} requests {word}, {a:.1f}s to {b:.1f}s</title></rect>')
        for t, action in applies(meta):
            parts.append(f'<path d="M{x(t):.1f} {y-1}l6 11.5-6 11.5-6-11.5z" fill="var(--i)" stroke="var(--tick,#fff)" stroke-width="1.2">'
                         f'<title>{esc(tick(action))} at {t:.0f}s</title></path>')
    svg(name, 640, height, label, "".join(parts))


def confidence(runs):
    left, right, step = 128, 628, 8.2
    lo, hi = 60, 100
    x = lambda c: left + (c - lo) / (hi - lo) * (right - left)
    parts, y = [], 34
    parts.append('<circle class="ok" cx="134" cy="11" r="4"/><text class="m" x="143" y="15">passed</text>'
                 '<circle class="neu" cx="210" cy="11" r="4"/><text class="m" x="219" y="15">failed</text>'
                 '<path class="bad" d="M286 5l6 6-6 6-6-6z"/><text class="m" x="297" y="15">passed, but wiped the carts</text>')
    bands = []
    for arm in "GHD":
        group = [m for m in runs if m["arm"] == arm]
        wins, losses = collections.defaultdict(list), collections.defaultdict(list)
        for m in sorted(group, key=lambda r: not r["score"]["state_loss"]):
            (wins if m["score"]["pass"] else losses)[max(lo, m["confidence"])].append(m)
        up = max(map(len, wins.values()), default=0)
        down = max(map(len, losses.values()), default=0)
        base = y + up * step + 5
        bands.append((arm, base, y, base + down * step + 5, group))
        y = base + down * step + 22
    height = y + 14
    gate = x(90)
    parts.append(f'<rect x="{gate:.1f}" y="26" width="{right-gate:.1f}" height="{y-32:.1f}" fill="var(--p)" opacity=".09"/>'
                 f'<line x1="{gate:.1f}" x2="{gate:.1f}" y1="26" y2="{y-6:.1f}" stroke="var(--i)" stroke-dasharray="3 3"/>')
    for arm, base, _, _, group in bands:
        wins = sum(bool(m["score"]["pass"]) for m in group)
        parts.append(f'<text class="b" x="0" y="{base-2:.1f}">{esc(NAMES[arm])}</text><text class="m" x="0" y="{base+12:.1f}">{wins}/{len(group)} passed</text>'
                     f'<line class="rule" x1="{left}" x2="{right}" y1="{base:.1f}" y2="{base:.1f}"/>')
        stacks = collections.Counter()
        for m in sorted(group, key=lambda r: not r["score"]["state_loss"]):
            win = bool(m["score"]["pass"])
            key = (max(lo, m["confidence"]), win)
            stacks[key] += 1
            cx = x(key[0])
            cy = base - stacks[key] * step + 2.5 if win else base + stacks[key] * step - 2.5
            tip = f'<title>{esc(m["fault"])} run {m["rep"]}: {m["confidence"]}% {esc(m["decision"])}, {"pass" if win else "fail"}</title>'
            if m["score"]["state_loss"]:
                parts.append(f'<path class="bad" d="M{cx:.1f} {cy-5:.1f}l5 5-5 5-5-5z">{tip}</path>')
            else:
                parts.append(f'<circle class="{"ok" if win else "neu"}" cx="{cx:.1f}" cy="{cy:.1f}" r="3.4">{tip}</circle>')
    for c in range(60, 101, 10):
        parts.append(f'<text class="m" text-anchor="{'end' if c == 100 else 'middle'}" x="{x(c):.1f}" y="{y+6:.1f}">{"≤60" if c == 60 else c}%</text>')
    svg("confidence", 640, height, "Stated confidence of every AI decision, passes above each line and failures below", "".join(parts))


def failures(runs):
    width, row = 380, 50
    parts = ['<rect class="ok" x="0" y="4" width="10" height="10" rx="2"/><text class="m" x="15" y="13">passed</text>'
             '<rect class="neu" x="150" y="4" width="10" height="10" rx="2"/><text class="m" x="165" y="13">failed, changed nothing</text>'
             '<rect class="neu" opacity=".5" x="0" y="22" width="10" height="10" rx="2"/><text class="m" x="15" y="31">right target, no recovery</text>'
             '<rect class="bad" x="150" y="22" width="10" height="10" rx="2"/><text class="m" x="165" y="31">failed, hit an innocent service</text>']
    result = {}
    for i, arm in enumerate(("G", "H", "D", "script")):
        group = [m for m in runs if m["arm"] == arm]
        win = sum(bool(m["score"]["pass"]) for m in group)
        harm = sum(1 for m in group if not m["score"]["pass"] and m["score"]["innocent_writes"])
        quiet = sum(1 for m in group if not m["score"]["pass"] and not m["score"]["writes"])
        near = len(group) - win - harm - quiet
        result[arm] = (win, quiet, near, harm)
        y = 62 + i * row
        parts.append(f'<text class="b" x="0" y="{y-5}">{esc(NAMES[arm])}</text>')
        at = 0
        for count, cls, extra in ((win, "ok", ""), (quiet, "neu", ""), (near, "neu", ' opacity=".5"'), (harm, "bad", "")):
            if not count:
                continue
            bar = count / len(group) * width
            parts.append(f'<rect class="{cls}"{extra} x="{at:.1f}" y="{y}" width="{bar-2:.1f}" height="24" rx="3"><title>{count} of {len(group)}</title></rect>')
            if bar >= 14:
                parts.append(f'<text class="w" text-anchor="middle" x="{at+bar/2-1:.1f}" y="{y+16}">{count}</text>')
            at += bar
    svg("failures", width, 62 + 4 * row - 16, "How each arm's runs ended", "".join(parts), "100%")
    return result


def main():
    OUT.mkdir(exist_ok=True)
    runs = load()
    pick = lambda fault, arm: sorted((m for m in runs if m["fault"] == fault and m["arm"] == arm), key=lambda m: m["rep"])
    name = lambda action: {"rollout_undo": f'undo {action.get("name")}', "set_flag": f'{action.get("flag")} off'}.get(action["type"], f'patch {action.get("name")}')
    timeline("d1", [(f"Script, run {m['rep']}", m) for m in pick("D1", "script")] + [(f"Luna, run {m['rep']}", m) for m in pick("D1", "G")],
             "Every request during incident D1 for the revert script and GPT-6 Luna", name)
    timeline("n1", [(f"Luna at {m['confidence']}%", m) for m in pick("N1", "G") if m["score"]["state_loss"]],
             "Requests during N1 for the three Luna runs that raised the memory limit", name)
    confidence([m for m in runs if m["arm"] in "GHD"])
    ends = failures(runs)
    ai = [m for m in runs if m["arm"] in "GHD"]
    high = [m for m in ai if m["confidence"] >= 90]
    print(json.dumps({
        "probes": sum(len(m["probe"]) for m in runs), "ends": ends,
        "ai_failed": sum(not m["score"]["pass"] for m in ai), "ai_failed_no_write": sum(v[1] for k, v in ends.items() if k != "script"),
        "high": len(high), "high_pass": sum(bool(m["score"]["pass"]) for m in high), "high_wiped": sum(m["score"]["state_loss"] for m in high),
        "ai": len(ai), "d1_applies": {m["id"]: [round(t, 1) for t, _ in applies(m)] for m in pick("D1", "script") + pick("D1", "G")},
        "n1_applies": {m["id"]: [round(t, 1) for t, _ in applies(m)] for m in pick("N1", "G")},
        "e1": [(m["arm"], m["rep"], m["decision"], m["score"]["pass"], (m["cause"] or "")[:70]) for m in ai if m["fault"] == "E1"],
    }, indent=0))


main()
