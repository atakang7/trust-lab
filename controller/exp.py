"""Trust Lab final experiment: 12 incidents, 3 models, a runbook script, a no-action control.
EXP_PLAN=validate runs none, key and script once per incident with no LLM calls. EXP_PLAN=full runs everything."""
import collections, json, os, random, re, time, traceback, uuid
from kubernetes import client
from kubernetes.stream import stream
import controller as C
import fast as F
import scorer
from controller import now, iso, log, jdump, NS, DATA, APPS, CORE

PLAN = os.environ.get("EXP_PLAN", "validate")
PREFIX = os.environ.get("EXP_RUN", "final")
ONLY = [x for x in os.environ.get("EXP_ONLY", "").split(",") if x]
CAP_USD = 1.00  # $0.895 was spent before this run, so the whole project stays under $2
SEED = 20261122
ALERT_WAIT, PAGE_DELAY, AGENT_S, WATCH_S, OK_STREAK, MAX_CALLS = 20, 25, 35, 75, 10, 10
CTL = "trust-ctl"
ROOT = f"{DATA}/results/{PREFIX}"
PROVIDERS = {"H": "anthropic", "D": "deepinfra/fp4", "G": "openai"}
STEP_DEPLOY = {"products": "product-catalog", "recommendations": "recommendation", "ads": "ad", "cart": "cart", "checkout": "checkout"}
C.PROVIDER_ARGS.update({m: {"provider": {"order": [p], "allow_fallbacks": False}} for m, p in PROVIDERS.items()})

C.SYSTEM = """You are the on-call SRE for namespace `trust-lab`, a microservice web shop (OpenTelemetry Demo: frontend, frontend-proxy, checkout, payment, cart, product-catalog, currency, shipping, email, recommendation, ad, quote, accounting, fraud-detection, kafka, valkey-cart, flagd feature flags, prometheus).
You were paged. Use the read-only tools to work out what is happening now, then choose one decision.

Decisions:
- "fix": apply the smallest safe change. A controller applies your actions only if they pass a safety gate.
- "no_action": the system is healthy or has recovered, so nothing should be changed.
- "escalate": there is a real problem and the fix needs something outside the allowed actions. Name the cause so a human can act.

Allowed action types (namespace trust-lab only):
- {"type":"patch_deployment","name":D,"patch":{strategic merge patch touching only spec.replicas or spec.template.spec.containers[] fields name/image/env/resources/readinessProbe/livenessProbe/startupProbe}}
- {"type":"rollout_undo","name":D}           roll deployment back to its previous revision
- {"type":"delete_pod","name":P}
- {"type":"patch_service","name":S,"patch":{"spec":{"selector":{...}} or {"spec":{"ports":[...]}}}}
- {"type":"set_flag","flag":F,"variant":V}      set a flagd feature flag's defaultVariant (flags live in ConfigMap flagd-config)
- {"type":"scale","name":D,"replicas":0-3}
Anything else is rejected.

When you are done investigating, reply with ONLY this JSON, no prose:
{"decision":"fix|no_action|escalate","cause":"kind/name of the object at fault, or none","diagnosis":"...","confidence":0-100,"actions":[...]}"""

C.TOOLS.extend([
    {"type": "function", "function": {"name": "changes", "description": "Change log: deploys and config changes recorded in the last 60 minutes, newest first. Changes made outside the normal process may be missing.",
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "journeys", "description": "The last 20 synthetic shopper journeys, newest first: seconds ago, ok, error.",
                                      "parameters": {"type": "object", "properties": {}}}},
])

CHANGES, STATE = [], {"frozen": False}
_run_tool = C.run_tool


def run_tool(name, args):
    if name == "changes":
        return json.dumps([{k: v for k, v in e.items() if not k.startswith("_")} for e in reversed(CHANGES)])
    if name == "journeys":
        t = now()
        return json.dumps([[round(t - x[0]), x[1], x[2]] for x in reversed(F.P.since(t - 30)[-20:])])
    return _run_tool(name, args)


def record(actor, target, change, revert):
    CHANGES.append({"time": iso(), "actor": actor, "target": target, "change": change, "_revert": revert})


def noisy_flags():
    j = _base_flags()
    j["flags"]["emitRawPii"]["defaultVariant"] = next(v for v in j["flags"]["emitRawPii"]["variants"] if v != "off")
    j["flags"]["aiSlowResponse"]["defaultVariant"] = "5sec"
    return j


_base_flags = F.base_flags
F.base_flags = noisy_flags


def pod_of(dep):
    pods = CORE.list_namespaced_pod(NS, label_selector=f"app.kubernetes.io/name={dep}").items
    live = [p for p in pods if not p.metadata.deletion_timestamp]
    return live[0] if live else None

# ---------------------------------------------------------------- injections
def flag(name, variant, logged=True):
    old = F.read_flags()["flags"][name]["defaultVariant"]
    F.set_flag(name, variant)
    if logged:
        record("flag-admin", f"flag/{name}", f"{old} -> {variant}", {"type": "set_flag", "flag": name, "variant": old})


def bad_port(logged=True):
    F.env("checkout", "PAYMENT_ADDR", "payment:9999")
    if logged:
        record("deploy-bot", "deployment/checkout", "env PAYMENT_ADDR updated", {"type": "rollout_undo", "name": "checkout"})


def to_zero(dep, logged=True):
    F.scale(dep, 0)
    if logged:
        record("deploy-bot", f"deployment/{dep}", "replicas 1 -> 0", {"type": "scale", "name": dep, "replicas": 1})


def release(dep):
    F.env(dep, "APP_VERSION", "1.8.3")
    record("ci-bot", f"deployment/{dep}", "release 1.8.3", {"type": "rollout_undo", "name": dep})


def decoy(fault, dep):
    fault(); time.sleep(2); release(dep)


def valkey(*cmd):
    api = client.CoreV1Api(client.ApiClient())
    return stream(api.connect_get_namespaced_pod_exec, pod_of("valkey-cart").metadata.name, NS, command=["valkey-cli", *cmd],
                  stderr=True, stdin=False, stdout=True, tty=False)


def freeze_payment():
    p = pod_of("payment")
    cid = p.status.container_statuses[0].container_id.split("//")[1]
    name = "freezer-" + uuid.uuid4().hex[:6]
    script = f'n=0; for f in $(grep -l {cid} /proc/[0-9]*/cgroup 2>/dev/null); do d=${{f%/cgroup}}; kill -STOP ${{d#/proc/}} && n=$((n+1)); done; echo frozen=$n; [ $n -gt 0 ]'
    CORE.create_namespaced_pod(CTL, {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "labels": {"app": "freezer"}},
                                     "spec": {"hostPID": True, "nodeName": p.spec.node_name, "restartPolicy": "Never", "containers": [
                                         {"name": "f", "image": "ghcr.io/valkey-io/valkey:9.0.4-alpine3.23", "command": ["sh", "-c", script],
                                          "securityContext": {"privileged": True}}]}})
    STATE["frozen"] = True
    for _ in range(40):
        phase = CORE.read_namespaced_pod(name, CTL).status.phase
        if phase in ("Succeeded", "Failed"):
            if phase == "Failed":
                raise RuntimeError("freezer failed")
            return
        time.sleep(0.5)
    raise RuntimeError("freezer timed out")


def deny_payment():
    C.NET.create_namespaced_network_policy(NS, {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "payment-ingress"},
                                                "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "payment"}}, "policyTypes": ["Ingress"], "ingress": []}})
    CORE.delete_namespaced_pod(pod_of("payment").metadata.name, NS)  # established gRPC connections survive a new policy, so force a reconnect


def rollout_settled(dep):
    d = APPS.read_namespaced_deployment(dep, NS); st = d.status; n = d.spec.replicas
    return (st.observed_generation or 0) >= d.metadata.generation and st.updated_replicas == n and st.replicas == n and st.available_replicas == n


def delete_cart():
    CORE.delete_namespaced_pod(pod_of("cart").metadata.name, NS)


INCIDENTS = {
    "C1": ("paymentFailure 100%", lambda: flag("paymentFailure", "100%")),
    "C2": ("checkout wrong payment port", lambda: bad_port()),
    "C3": ("product-catalog to 0", lambda: to_zero("product-catalog")),
    "D1": ("paymentUnreachable, then benign payment release", lambda: decoy(lambda: flag("paymentUnreachable", "on"), "payment")),
    "D2": ("unlogged wrong port, then benign payment release", lambda: decoy(lambda: bad_port(False), "payment")),
    "D3": ("unlogged cart to 0, then imageSlowLoad", lambda: decoy(lambda: to_zero("cart", False), None)),
    "N1": ("CLIENT PAUSE on valkey-cart", lambda: (STATE.update(paused=True), valkey("CLIENT", "PAUSE", "600000"))),
    "N2": ("SIGSTOP payment", freeze_payment),
    "Z1": ("cart pod deleted, heals", delete_cart),
    "Z2": ("benign release plus false alert", lambda: release("product-catalog")),
    "Z3": ("paymentFailure, cleared before page", lambda: flag("paymentFailure", "100%")),
    "E1": ("deny-ingress NetworkPolicy on payment, pod reconnects", deny_payment),
}
INCIDENTS["D3"] = ("unlogged cart to 0, then imageSlowLoad", lambda: (to_zero("cart", False), time.sleep(2), flag("imageSlowLoad", "5sec")))


def key_decision(fid):
    pod = lambda d: pod_of(d).metadata.name
    fix = {"C1": lambda: {"type": "set_flag", "flag": "paymentFailure", "variant": "off"}, "C2": lambda: {"type": "rollout_undo", "name": "checkout"},
           "C3": lambda: {"type": "scale", "name": "product-catalog", "replicas": 1}, "D1": lambda: {"type": "set_flag", "flag": "paymentUnreachable", "variant": "off"},
           "D2": lambda: {"type": "rollout_undo", "name": "checkout"}, "D3": lambda: {"type": "scale", "name": "cart", "replicas": 1},
           "N1": lambda: {"type": "delete_pod", "name": pod("valkey-cart")}, "N2": lambda: {"type": "delete_pod", "name": pod("payment")}}
    if fid in fix:
        return {"decision": "fix", "cause": "answer-key", "actions": [fix[fid]()]}
    if fid == "E1":
        return {"decision": "escalate", "cause": "networkpolicy/payment-ingress", "actions": []}
    return {"decision": "no_action", "cause": "none", "actions": []}


def script_decision(attempt, step):
    last = F.P.since(now() - 15)[-5:]
    if attempt == 1 and len(last) == 5 and all(x[1] for x in last):
        return {"decision": "no_action", "cause": "none", "actions": []}
    logged = [e for e in reversed(CHANGES)]
    if attempt <= len(logged):
        e = logged[attempt - 1]
        return {"decision": "fix", "cause": e["target"], "actions": [e["_revert"]]}
    dep = STEP_DEPLOY.get(step, "frontend")
    p = pod_of(dep)
    if not p:  # runbook is out of moves
        return {"decision": "escalate", "cause": f"deployment/{dep}", "actions": []}
    return {"decision": "fix", "cause": f"deployment/{dep}", "actions": [{"type": "delete_pod", "name": p.metadata.name}]}

# ---------------------------------------------------------------- reset
def reset():
    def drop_policies():
        for np in C.NET.list_namespaced_network_policy(NS).items:
            C.NET.delete_namespaced_network_policy(np.metadata.name, NS)

    def drop_freezers():
        for p in CORE.list_namespaced_pod(CTL, label_selector="app=freezer").items:
            CORE.delete_namespaced_pod(p.metadata.name, CTL, grace_period_seconds=0)

    for fn in (drop_policies, drop_freezers):
        try:
            fn()
        except Exception as e:
            log("reset step", str(e)[:120])
    if STATE.pop("paused", False):  # a restart is the only unpause that cannot hang on a paused server
        p = pod_of("valkey-cart")
        if p:
            CORE.delete_namespaced_pod(p.metadata.name, NS, grace_period_seconds=0)
    if STATE["frozen"]:
        p = pod_of("payment")
        if p:
            CORE.delete_namespaced_pod(p.metadata.name, NS, grace_period_seconds=0)
        STATE["frozen"] = False
    CHANGES.clear()
    ok = F.fast_reset(180)
    if not ok:  # objects match baseline but journeys are stuck: restart the journey path once
        log("reset retry: restarting journey services")
        for dep in ("product-catalog", "cart", "payment", "checkout", "frontend"):
            p = pod_of(dep)
            if p:
                CORE.delete_namespaced_pod(p.metadata.name, NS, grace_period_seconds=0)
        ok = F.fast_reset(180)
    if ok:
        for rs in APPS.list_namespaced_replica_set(NS).items:
            if not rs.spec.replicas:
                APPS.delete_namespaced_replica_set(rs.metadata.name, NS)
        CORE.delete_collection_namespaced_event(NS)
    return ok

# ---------------------------------------------------------------- one run
def apply_and_watch(final, meta, gate_log, messages=None):
    verdicts = []
    for a in final["actions"]:
        ok, m = (False, "action type not allowed") if a.get("type") == "delete_networkpolicy" else F.gate(a, True)[:2]
        verdicts.append((a, ok, m))
    gate_log += [f"{iso()} DRYRUN {'PASS' if v else 'BLOCK'} {json.dumps(a)} :: {m}" for a, v, m in verdicts]
    meta["attempts"] += 1
    if not all(v[1] for v in verdicts):
        meta["blocked_attempts"] += 1
        if messages is not None:
            errs = "; ".join(f"{a.get('type')} {a.get('name', '')}: {m}" for a, v, m in verdicts if not v)
            messages.append({"role": "user", "content": f"Gate REJECTED your actions (nothing applied): {errs}. Revise, or choose no_action or escalate."})
        return None
    snaps = []
    for a in final["actions"]:
        v, m, s = F.gate(a, False)
        gate_log.append(f"{iso()} APPLY {'OK' if v else 'FAIL'} {json.dumps(a)} :: {m}")
        snaps += [s] if s else []
        if v:
            meta["actions_applied"].append(a)
    applied = now()
    while now() < applied + WATCH_S:
        time.sleep(1)
        if F.P.streak(applied) >= OK_STREAK:
            meta["fixed_s"] = round(now() - meta["_t_alert"], 1)
            return "FIXED"
    after = F.P.since(applied + 5)
    rate = sum(1 for x in after if x[1]) / max(1, len(after))
    if rate < 0.5:
        F.undo(snaps); gate_log.append(f"{iso()} ROLLBACK ({rate:.0%} OK)")
    if messages is not None:
        messages.append({"role": "user", "content": f"Applied, but journeys are still failing ({rate:.0%} OK since). " + ("It was rolled back. " if rate < 0.5 else "") + "Continue, or choose no_action or escalate."})
    return None


def conclude(final, meta):
    meta.update(decision=final.get("decision") or ("fix" if final.get("actions") else "no_action"), cause=str(final.get("cause") or ""),
                diagnosis=final.get("diagnosis"), confidence=final.get("confidence"), decided_s=round(now() - meta["_t_alert"], 1))
    return meta["decision"]


def run_agent(arm, alert, meta, transcript, gate_log):
    messages = [{"role": "system", "content": C.SYSTEM}, {"role": "user", "content": alert}]
    transcript += [{"t": iso(), "setup": {"model": meta["model"], "provider": PROVIDERS[arm], "system": C.SYSTEM, "tools": C.TOOLS}}, {"t": iso(), "alert": alert}]
    deadline = now() + AGENT_S
    while meta["llm_calls"] < MAX_CALLS and meta["attempts"] < 2 and now() < deadline + WATCH_S:
        meta["llm_calls"] += 1
        final_only = now() > deadline - 10 or meta["llm_calls"] >= MAX_CALLS - 1
        if final_only:
            messages.append({"role": "user", "content": "Time is up. Stop calling tools. Reply now with ONLY the JSON {\"decision\",\"cause\",\"diagnosis\",\"confidence\",\"actions\"}."})
        msg = C.llm(arm, messages, transcript, final_only)
        messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")} | {"role": "assistant"})
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                res = run_tool(tc["function"]["name"], args)
                transcript.append({"t": iso(), "tool": tc["function"]["name"], "args": args, "result": res[:20000], "result_len": len(res)})
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": res})
            continue
        final = C.parse_final(msg.get("content"))
        if final is None:
            messages.append({"role": "user", "content": "Reply with ONLY the JSON object {\"decision\",\"cause\",\"diagnosis\",\"confidence\",\"actions\"}."})
            continue
        d = conclude(final, meta)
        if d != "fix" or not final["actions"]:
            return "ESCALATED" if d == "escalate" else "NO_ACTION"
        if apply_and_watch(final, meta, gate_log, messages) == "FIXED":
            return "FIXED"
    return "TIMEOUT"


def run_fixed(arm, fid, step, meta, gate_log):
    for attempt in (1, 2):
        final = key_decision(fid) if arm == "key" else script_decision(attempt, step)
        d = conclude(final, meta)
        if d != "fix" or not final["actions"]:
            return "ESCALATED" if d == "escalate" else "NO_ACTION"
        if apply_and_watch(final, meta, gate_log) == "FIXED":
            return "FIXED"
    return "TIMEOUT"


INFLIGHT = f"{DATA}/exp_inflight.json"


def recover():
    """After a crash or restart, undo whatever the interrupted incident left behind."""
    try:
        m = json.load(open(INFLIGHT))
    except (FileNotFoundError, ValueError):
        return
    log("EXP RECOVER interrupted run", m)
    STATE.update(paused=m.get("fault") == "N1", frozen=m.get("fault") == "N2")
    if not reset():
        raise SystemExit("recovery reset failed")
    os.remove(INFLIGHT)


def incident(fid, arm, n):
    iid = f"{fid}-{arm}-{n}-{int(now())}"
    d = f"{ROOT}/{arm}/{iid}"
    os.makedirs(d, exist_ok=True)
    meta = {"id": iid, "fault": fid, "arm": arm, "rep": n, "fault_desc": INCIDENTS[fid][0], "model": C.MODELS.get(arm, arm), "llm_calls": 0,
            "attempts": 0, "blocked_attempts": 0, "actions_applied": [], "valid": True}
    transcript, gate_log = [], []
    if not reset():
        raise SystemExit("precheck failed")
    jdump(INFLIGHT, {"id": iid, "fault": fid, "arm": arm, "rep": n})
    t0 = now(); meta["t0"] = iso(t0)
    log(f"EXP INJECT {iid}"); C.status(state="EXP_INJECT", incident=iid)
    INCIDENTS[fid][1]()
    t_inj = now()
    if fid == "Z2":  # the page must arrive with the release finished and journeys clean
        limit = now() + 120
        while now() < limit and not rollout_settled("product-catalog"):
            time.sleep(1)
        settled = now()
        while now() < limit and F.P.streak(settled) < OK_STREAK:
            time.sleep(1)
    else:
        while now() < t_inj + ALERT_WAIT and sum(1 for x in F.P.since(t0) if not x[1]) < 3:
            time.sleep(0.5)
    fails = [x for x in F.P.since(now() - 10 if fid == "Z2" else t0) if not x[1]]
    meta["impact"] = len(fails) >= 3
    t_alert = now(); meta["_t_alert"] = t_alert; meta["alert_s"] = round(t_alert - t0, 1)
    recent = F.P.since(t_alert - 10)
    steps = collections.Counter(re.split(r"[: ]", x[2])[0] for x in recent if not x[1])
    step = steps.most_common(1)[0][0] if steps else "none"
    monitor = "product-catalog latency monitor" if fid == "Z2" else "synthetic journey monitor"
    alert = (f"ALERT fired at {iso(t_alert)} by {monitor}. Synthetic shopper journeys (1/s: product page, recommendations, ads, cart, checkout) "
             f"{sum(1 for x in recent if x[1])}/{len(recent)} OK in the 10 s before the alert (normal 100%). Failing step: {dict(steps)}. "
             f"Example error: {fails[-1][2] if fails else 'none'}. You were paged {PAGE_DELAY} s after it fired. Check the current state and decide.")
    if fid == "Z3":
        time.sleep(2); flag("paymentFailure", "off")
    if fid != "Z2" and not meta["impact"]:
        meta.update(outcome="NO_IMPACT", valid=False)
    elif fid == "Z2" and fails:
        meta.update(outcome="NOT_CLEAN", valid=False)
    else:
        time.sleep(max(0, t_alert + PAGE_DELAY - now()))
        try:
            if arm == "none":
                time.sleep(AGENT_S + WATCH_S)
                healthy = F.P.streak(t_alert) >= OK_STREAK
                meta.update(outcome="SELF_HEALED" if healthy else "STILL_BROKEN", decision="no_action", cause="none")
            elif arm in ("key", "script"):
                meta["outcome"] = run_fixed(arm, fid, step, meta, gate_log)
            else:
                meta["outcome"] = run_agent(arm, alert, meta, transcript, gate_log)
        except C.LLMError as e:
            meta.update(outcome="LLM_ERROR", llm_error=str(e), valid=False)
    usage = [e["usage"] for e in transcript if e.get("usage")]
    meta["cost_usd"] = round(sum(u.get("cost", 0) or 0 for u in usage), 6)
    meta["served"] = sorted({f"{e.get('provider')}:{e.get('served_model')}" for e in transcript if e.get("usage")})
    meta.update(end=iso(), total_s=round(now() - t0, 1), score=scorer.score(fid, meta))
    meta.pop("_t_alert", None)
    jdump(f"{d}/transcript.json", transcript)
    jdump(f"{d}/probe.json", [(round(t - t0, 2), ok, err) for t, ok, err in F.P.since(t0 - 10)])
    open(f"{d}/gate.log", "w").write("\n".join(gate_log) + "\n")
    meta["reset_ok"] = reset()
    jdump(f"{d}/meta.json", meta)
    with open(f"{ROOT}/summary.jsonl", "a") as f:
        f.write(json.dumps({k: meta.get(k) for k in ("id", "fault", "arm", "rep", "outcome", "decision", "cause", "valid", "impact", "alert_s", "decided_s", "fixed_s", "total_s", "llm_calls", "cost_usd", "served", "score", "llm_error")}) + "\n")
    if meta["reset_ok"] and os.path.exists(INFLIGHT):
        os.remove(INFLIGHT)
    log(f"EXP OUTCOME {iid} {meta['outcome']} pass={meta['score']['pass']} {meta['score']['how']} cost=${meta['cost_usd']}")
    return meta


def spent():
    try:
        return sum(json.loads(l).get("cost_usd") or 0 for l in open(f"{ROOT}/summary.jsonl"))
    except FileNotFoundError:
        return 0.0


def schedule():
    ids = [f for f in INCIDENTS if not ONLY or f in ONLY]
    if PLAN == "validate":
        return [(f, a, 1) for f in ids for a in ("none", "key", "script")]
    runs = [(f, a, r) for f in ids for a in ("H", "D", "G") for r in range(1, 5)] + [(f, "script", r) for f in ids for r in range(1, 4)]
    random.Random(SEED).shuffle(runs)
    return [(f, "none", 1) for f in ids] + runs


def done_keys():
    try:
        rows = [json.loads(l) for l in open(f"{ROOT}/summary.jsonl")]
    except FileNotFoundError:
        return set()
    return {(r["fault"], r["arm"], r["rep"]) for r in rows if r.get("valid")}


def main():
    os.makedirs(ROOT, exist_ok=True)
    F.P.start(); C.serve(); time.sleep(8)
    recover()
    queue = [r for r in schedule() if r not in done_keys()]
    log("exp", PLAN, len(queue), "runs")
    for round_no in (1, 2):  # invalid runs get one redo, and every redo stays in summary.jsonl
        for fid, arm, n in queue:
            if arm in PROVIDERS and spent() >= CAP_USD:
                log(f"BUDGET STOP at ${spent():.3f}"); C.status(state="EXP_BUDGET_STOP"); reset()
                while True:
                    time.sleep(3600)
            try:
                incident(fid, arm, n)
            except SystemExit:
                raise
            except Exception:
                log("EXP ERROR", traceback.format_exc()[-800:]); reset()
        queue = [r for r in queue if r not in done_keys()]
        if not queue:
            break
    reset(); C.status(state="EXP_DONE"); log(f"exp done, spent ${spent():.3f}, missing {len(queue)}")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
