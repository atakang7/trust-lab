"""Checks the frozen hashes, re-scores every saved run, and prints the pass table. Needs only Python."""
import collections
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "controller"))
import scorer  # noqa: E402

FINAL_SUMMARY = "0b9a371f7b8b1daf25861834d84cc8a78919e2e1940865934f76d6be700940ff"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


failed = 0
for line in (ROOT / "FREEZE.txt").read_text(encoding="utf-8").splitlines()[1:]:
    if not line.strip():
        continue
    digest, name = line.split(maxsplit=1)
    name = name.lstrip("*")
    path = ROOT / name if name.endswith(".md") else ROOT / "controller" / name
    ok = sha(path) == digest
    failed += not ok
    print(f"{'ok  ' if ok else 'FAIL'} frozen {name}")

ok = sha(ROOT / "results/final/summary.jsonl") == FINAL_SUMMARY
failed += not ok
print(f"{'ok  ' if ok else 'FAIL'} final summary")

for run in ("final", "followup120"):
    count = changed = 0
    for meta_path in (ROOT / "results" / run).rglob("meta.json"):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        saved = meta.pop("score", None)
        if saved is None:
            continue
        count += 1
        changed += scorer.score(meta["fault"], meta) != saved
    failed += changed
    print(f"{'ok  ' if not changed else 'FAIL'} {run}: re-scored {count} runs, {changed} differ")

passed, total = collections.Counter(), collections.Counter()
for line in (ROOT / "results/final/summary.jsonl").read_text(encoding="utf-8").splitlines():
    row = json.loads(line)
    total[row["arm"]] += 1
    passed[row["arm"]] += bool(row["score"]["pass"])
names = {"script": "Script", "G": "GPT-6 Luna", "H": "Claude Haiku 5.5", "D": "DeepSeek V3.2", "none": "No action"}
for arm, name in names.items():
    print(f"{name:18} {passed[arm]}/{total[arm]}")
sys.exit(1 if failed else 0)
