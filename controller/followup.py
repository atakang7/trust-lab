"""Labeled follow-up to the frozen final run. Reuses exp.py unchanged and only raises the agent limit.

Agent limit 120 s and 25 calls (tools cut at 110 s or call 24) on N2, E1 and D2, for the three models.
Runs are in shuffled blocks of 9, one repetition each, so a budget stop still leaves complete repetitions.
New runs stop at $0.22. Inside a run, tools are cut at $0.28, so spend stays under $0.30.
"""
import os
import random
import sys

RUN = "followup120"
if os.environ.get("EXP_RUN") != RUN:
    sys.exit(f"refusing to start: EXP_RUN must be {RUN}")

import exp as E  # noqa: E402

if not E.ROOT.endswith(f"/{RUN}"):
    sys.exit("refusing to start: wrong results folder")

SEED = 20261123
INCIDENTS = ("N2", "E1", "D2")
ARMS = ("H", "D", "G")
REPS = 4
START_LIMIT, HARD_LIMIT = 0.22, 0.28
PRICES = {"H": (0.10, 0.50), "D": (0.26, 0.38), "G": (0.10, 0.50)}  # USD per million tokens, in and out

E.AGENT_S, E.MAX_CALLS, E.CAP_USD = 120, 25, START_LIMIT


def schedule():
    rng, runs = random.Random(SEED), []
    for rep in range(1, REPS + 1):
        block = [(f, a, rep) for f in INCIDENTS for a in ARMS]
        rng.shuffle(block)
        runs += block
    return runs


def usage_cost(arm, transcript):
    total, (p_in, p_out) = 0.0, PRICES[arm]
    for entry in transcript:
        u = entry.get("usage") or {}
        estimate = (u.get("prompt_tokens", 0) * p_in + u.get("completion_tokens", 0) * p_out) / 1e6
        total += max(float(u.get("cost") or 0), estimate)
    return total


real_llm = E.C.llm


def guarded_llm(arm, messages, transcript, final_only=False):
    if not final_only and E.spent() + usage_cost(arm, transcript) >= HARD_LIMIT:
        E.log(f"FOLLOWUP budget guard: cutting tools at ${E.spent() + usage_cost(arm, transcript):.3f}")
        messages.append({"role": "user", "content": "Time is up. Stop calling tools. Reply now with ONLY the JSON {\"decision\",\"cause\",\"diagnosis\",\"confidence\",\"actions\"}."})
        final_only = True
    return real_llm(arm, messages, transcript, final_only)


E.schedule = schedule
E.C.llm = guarded_llm

if __name__ == "__main__":
    E.main()
