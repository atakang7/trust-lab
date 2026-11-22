"""Builds grid.html: 12 incidents x 5 arms, every run visible. Data is the frozen final run."""
import html
import pathlib

G = {"C1": {"none": "F", "G": "PPPP", "D": "FFFF", "script": "PPP", "H": "PPPP"}, "C2": {"none": "F", "G": "PPPF", "D": "PPFF", "H": "PPFP", "script": "PPP"}, "C3": {"none": "F", "G": "PPPP", "H": "PPPF", "D": "PPPP", "script": "PPP"}, "D1": {"none": "F", "D": "PFFP", "H": "PPPP", "script": "RRR", "G": "PPPP"}, "D2": {"none": "F", "H": "FPFF", "script": "FFF", "G": "FPPF", "D": "FFFF"}, "D3": {"none": "F", "D": "PPPP", "G": "PPPP", "H": "PPPF", "script": "FFF"}, "E1": {"none": "F", "D": "FFFF", "script": "FFF", "G": "FFPF", "H": "FPFF"}, "N1": {"none": "F", "G": "LLPL", "H": "FFFF", "D": "RFFL", "script": "FFF"}, "N2": {"none": "F", "H": "FFFF", "D": "FFFF", "G": "FFFF", "script": "FFF"}, "Z1": {"none": "P", "G": "PFPP", "H": "PPPP", "script": "PPP", "D": "PFPF"}, "Z2": {"none": "P", "H": "PPPP", "D": "PPPP", "G": "PPPP", "script": "PPP"}, "Z3": {"none": "P", "H": "PPPP", "G": "PPPP", "script": "PPP", "D": "PPPP"}}

GROUPS = [
    ("Plain breakage", [("C1", "Payment flag forced to fail"), ("C2", "Checkout points at wrong port"), ("C3", "Catalog scaled to zero")]),
    ("Newest change is innocent", [("D1", "Bad flag, then harmless release"), ("D2", "Hidden port change, then release"), ("D3", "Cart at zero, then harmless flag")]),
    ("Nothing to revert", [("N1", "Cart database paused"), ("N2", "Payment process frozen")]),
    ("Right move is do nothing", [("Z1", "Pod died, already healed"), ("Z2", "False alarm next to a release"), ("Z3", "Failure cleared before the page")]),
    ("Must call a human", [("E1", "Network policy blocks payment")]),
]
ARMS = [("script", "Script"), ("G", "Luna"), ("H", "Haiku"), ("D", "DeepSeek"), ("none", "No action")]
NAMES = {"P": "correct", "L": "passed, wiped cart data", "R": "recovered, but hit an innocent service", "F": "failed"}
LX, TOP, COL, ROW, GAP, W = 268, 44, 92, 30, 24, 268 + 5 * 92 + 8


def mark(state, x, y, tip):
    t = f"<title>{html.escape(tip)}</title>"
    hit = f'<rect x="{x-7}" y="{y-11}" width="14" height="22" fill="transparent"/>'
    if state == "P":
        m = f'<circle cx="{x}" cy="{y}" r="5" class="pass"/>'
    elif state == "L":
        m = f'<path d="M{x} {y-6.5}L{x+6.5} {y}L{x} {y+6.5}L{x-6.5} {y}Z" class="lost"/>'
    elif state == "R":
        m = f'<circle cx="{x}" cy="{y}" r="4.2" class="rec"/>'
    else:
        m = f'<path d="M{x-3.5} {y-3.5}L{x+3.5} {y+3.5}M{x+3.5} {y-3.5}L{x-3.5} {y+3.5}" class="fail"/>'
    return f"<g>{t}{hit}{m}</g>"


out, y = [], TOP
for i, (_, label) in enumerate(ARMS):
    out.append(f'<text x="{LX + COL*i + COL/2}" y="24" text-anchor="middle" class="head">{label}</text>')
for group, items in GROUPS:
    y += GAP
    out.append(f'<text x="0" y="{y-6}" class="group">{group}</text>')
    out.append(f'<line x1="0" x2="{W}" y1="{y}" y2="{y}" class="rule"/>')
    for inc, label in items:
        cy = y + ROW / 2
        out.append(f'<text x="0" y="{cy+4}" class="row">{label}</text>')
        for i, (arm, arm_label) in enumerate(ARMS):
            runs = G[inc][arm]
            cx = LX + COL * i + COL / 2
            start = cx - (len(runs) - 1) * 8
            for k, state in enumerate(runs):
                out.append(mark(state, start + k * 16, cy, f"{label} · {arm_label} · run {k+1}: {NAMES[state]}"))
        y += ROW
H = int(y + 8)

legend = "".join(
    f'<span><svg width="16" height="16" viewBox="-8 -8 16 16" aria-hidden="true">{body}</svg>{text}</span>'
    for body, text in [
        ('<circle r="5" class="pass"/>', "Correct"),
        ('<path d="M0 -6.5L6.5 0L0 6.5L-6.5 0Z" class="lost"/>', "Passed, but wiped cart data"),
        ('<circle r="4.2" class="rec"/>', "Recovered, hit an innocent service"),
        ('<path d="M-3.5 -3.5L3.5 3.5M3.5 -3.5L-3.5 3.5" class="fail"/>', "Failed"),
    ]
)

page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Incident Grid</title>
<style>
:root{{--bg:#fff;--ink:#1f2328;--muted:#646b75;--rule:#e3e5e8;--pass:#008f80;--lost:#c2410c;--rec:#7c3aed;--fail:#9aa0a8}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#16181c;--ink:#e8eaed;--muted:#a0a6af;--rule:#30343a;--pass:#14a892;--lost:#d4701f;--rec:#9a7ae6;--fail:#6f7680}}}}
:root[data-theme=dark]{{--bg:#16181c;--ink:#e8eaed;--muted:#a0a6af;--rule:#30343a;--pass:#14a892;--lost:#d4701f;--rec:#9a7ae6;--fail:#6f7680}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--ink);font:14px/1.4 system-ui,sans-serif}}
figure{{margin:0 auto;max-width:{W}px}}.scroll{{overflow-x:auto}}svg.grid{{display:block;min-width:640px;width:100%;height:auto}}
.head{{font-size:13px;font-weight:600;fill:var(--ink)}}.group{{font-size:11.5px;font-weight:600;fill:var(--muted)}}.row{{font-size:13px;fill:var(--ink)}}.rule{{stroke:var(--rule)}}
.pass{{fill:var(--pass)}}.lost{{fill:var(--lost)}}.rec{{fill:none;stroke:var(--rec);stroke-width:2}}.fail{{fill:none;stroke:var(--fail);stroke-width:2;stroke-linecap:round}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 18px;margin:0 0 10px;color:var(--muted);font-size:13px}}.legend span{{display:inline-flex;align-items:center;gap:6px}}
figcaption{{margin-top:10px;color:var(--muted);font-size:13px}}
</style></head><body><figure><div class="legend">{legend}</div><div class="scroll">
<svg class="grid" viewBox="0 0 {W} {H}" role="img" aria-label="Every run of 12 incidents across script, Luna, Haiku, DeepSeek and no action">{''.join(out)}</svg></div>
<figcaption>Each mark is one run. 192 runs, 35 second decision limit. AI gets 4 runs per incident, the script 3, no action 1.</figcaption></figure></body></html>"""
pathlib.Path(__file__).with_name("grid.html").write_text(page, encoding="utf-8")
print("ok", W, H)
