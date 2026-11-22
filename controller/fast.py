"""Fast Trust Lab: each incident <= ~1 min.
inject (instant-impact faults) -> 1 Hz probe alerts in seconds -> agent with ~35 s budget -> gate -> 10 OK in a row = FIXED.
Flags change live through flagd-ui's file API (no flagd restarts); the ConfigMap is kept in sync so the agent can read it."""
import collections, json, os, random, threading, time, traceback, uuid, copy
import requests
import controller as C
from controller import now, iso, log, jdump, NS, SHOP, DATA, APPS, CORE

RUN_MODELS = [m for m in os.environ.get("FAST_MODEL", "H").split(",") if m]  # "none" = control, nobody acts
FAULT_IDS = [f for f in os.environ.get("FAST_FAULTS", "K01").split(",") if f]
REPEAT = int(os.environ.get("FAST_REPEAT", "1"))
PREFIX = os.environ.get("FAST_RUN", "fast")
ALERT_WAIT, AGENT_S, WATCH_S, OK_STREAK, MAX_CALLS = 15, 35, 45, 10, 10
C.REQ_TIMEOUT.update(H=30, S=30, D=30, G=30)

# ---------------------------------------------------------------- 1 Hz probe
class FastProbe(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.buf = collections.deque(maxlen=20000)
        self.lock = threading.Lock()
        self.products = []

    def journey(self, t0):
        step = "products"
        try:
            if not self.products:
                self.products = [p["id"] for p in requests.get(f"{SHOP}/api/products", timeout=3).json()]
            pid = random.choice(self.products)
            u = "probe-" + uuid.uuid4().hex[:8]
            requests.get(f"{SHOP}/api/products/{pid}", timeout=3).raise_for_status()
            step = "recommendations"
            requests.get(f"{SHOP}/api/recommendations", params={"productIds": pid}, timeout=3).raise_for_status()
            step = "ads"
            requests.get(f"{SHOP}/api/data", params={"contextKeys": "telescopes"}, timeout=3).raise_for_status()
            step = "cart"
            requests.post(f"{SHOP}/api/cart", json={"item": {"productId": pid, "quantity": 1}, "userId": u}, timeout=3).raise_for_status()
            step = "checkout"
            r = requests.post(f"{SHOP}/api/checkout?currencyCode=USD", timeout=3, json={
                "userId": u, "email": "probe@example.com", "userCurrency": "USD",
                "address": {"streetAddress": "1600 Amphitheatre Parkway", "city": "Mountain View", "state": "CA", "country": "United States", "zipCode": "94043"},
                "creditCard": {"creditCardCvv": 672, "creditCardExpirationMonth": 1, "creditCardExpirationYear": 2030, "creditCardNumber": "4432801561520454"}})
            ok = r.status_code == 200 and "orderId" in r.text and r.elapsed.total_seconds() < 1.5
            res = (ok, None if ok else f"checkout http {r.status_code} {r.elapsed.total_seconds():.1f}s")
        except Exception as e:
            self.products = [] if step == "products" else self.products
            res = (False, f"{step}: {type(e).__name__} {str(e)[:120]}")
        with self.lock:
            self.buf.append((t0, res[0], res[1]))

    def run(self):
        while True:
            t0 = now()
            threading.Thread(target=self.journey, args=(t0,), daemon=True).start()
            time.sleep(max(0, 1 - (now() - t0)))

    def since(self, start):
        with self.lock:
            return sorted([x for x in self.buf if x[0] >= start], key=lambda x: x[0])

    def streak(self, start):
        """Consecutive OK journeys (newest first) among those started after `start`."""
        n = 0
        for _, ok, _ in reversed(self.since(start)):
            if not ok:
                break
            n += 1
        return n


P = FastProbe()

# ---------------------------------------------------------------- flags: live file + ConfigMap in sync
def read_flags():
    r = requests.get(f"{SHOP}/feature/api/read-file", timeout=5); r.raise_for_status()
    return r.json()


def write_flags(j):
    requests.post(f"{SHOP}/feature/api/write-to-file", json={"data": j}, timeout=5).raise_for_status()
    cm = CORE.read_namespaced_config_map("flagd-config", NS)
    key = next(iter(cm.data))
    CORE.patch_namespaced_config_map("flagd-config", NS, {"data": {key: json.dumps(j, indent=2)}})


def set_flag(flag, variant):
    j = read_flags()
    if flag not in j["flags"] or variant not in j["flags"][flag]["variants"]:
        raise ValueError(f"unknown flag/variant {flag}={variant}")
    j["flags"][flag]["defaultVariant"] = variant
    write_flags(j)


def base_flags():
    cm = next(o for o in jload_base()["ConfigMap"] if o["metadata"]["name"] == "flagd-config")
    return json.loads(next(iter(cm["data"].values())))


def jload_base():
    return C.jload(f"{DATA}/baseline/objects.json")

# ---------------------------------------------------------------- instant-impact faults
def env(dep, name, val):
    APPS.patch_namespaced_deployment(dep, NS, {"spec": {"template": {"spec": {"containers": [{"name": dep, "env": [{"name": name, "value": val}]}]}}}})


def scale(dep, n):
    APPS.patch_namespaced_deployment_scale(dep, NS, {"spec": {"replicas": n}})


FAULTS = {  # id: (desc, inject, root-cause keyword groups)
    "K01": ("flag paymentFailure 100%", lambda: set_flag("paymentFailure", "100%"), [["payment"], ["flag", "paymentfailure"]]),
    "K02": ("flag cartFailure 100%", lambda: set_flag("cartFailure", "100%"), [["cart"], ["flag", "cartfailure"]]),
    "K03": ("flag paymentUnreachable on", lambda: set_flag("paymentUnreachable", "on"), [["payment"], ["flag", "paymentunreachable", "unreachable"]]),
    "K04": ("payment scaled to 0", lambda: scale("payment", 0), [["payment"], ["replica", "scale", "0 pods", "no pods", "no endpoints"]]),
    "K05": ("checkout PAYMENT_ADDR wrong port", lambda: env("checkout", "PAYMENT_ADDR", "payment:9999"), [["payment_addr", "9999"]]),
    "K06": ("product-catalog scaled to 0", lambda: scale("product-catalog", 0), [["product-catalog", "product catalog"], ["replica", "scale", "0 pods", "no pods", "no endpoints"]]),
    "K07": ("cart scaled to 0", lambda: scale("cart", 0), [["cart"], ["replica", "scale", "0 pods", "no pods", "no endpoints"]]),
}

# ---------------------------------------------------------------- gate (flags go live instantly)
def gate(action, dry):
    if isinstance(action, dict) and action.get("type") in ("set_flag", "patch_configmap"):
        flag, var = action.get("flag"), action.get("variant") or action.get("defaultVariant")
        try:
            j = read_flags()
            if flag not in j["flags"] or var not in j["flags"][flag]["variants"]:
                return False, f"unknown flag/variant {flag}={var}", None
            if not dry:
                set_flag(flag, var)
            return True, "dry-run ok" if dry else "applied", {"kind": "Flags", "flags": j}
        except Exception as e:
            return False, f"error: {e}", None
    return C.gate_and_apply(action, dry)


def undo(snaps):
    for s in snaps:
        write_flags(s["flags"]) if s.get("kind") == "Flags" else C.replace_obj(s)

# ---------------------------------------------------------------- reset
def fast_reset(timeout=120):
    t = now()
    base = jload_base()
    write_flags(base_flags())
    drift = []
    for kind in ("Service", "Deployment"):
        live = {o["metadata"]["name"]: o for o in C.list_kind(kind)}
        for o in base[kind]:
            l = live.get(o["metadata"]["name"])
            if l is None or l.get("spec") != o.get("spec"):
                drift.append(f"{kind}/{o['metadata']['name']}"); C.replace_obj(o)
    while now() < t + timeout:
        time.sleep(1)
        if P.streak(t + 1) >= 5:
            log(f"fast reset ok in {now() - t:.0f}s drift={drift}"); return True
    log(f"fast reset FAILED drift={drift}"); return False

# ---------------------------------------------------------------- incident
def incident(iid, fid, MODEL):
    RUN = f"{PREFIX}-{MODEL}"
    d = f"{DATA}/results/{RUN}/{iid}"
    os.makedirs(d, exist_ok=True)
    desc, inject, rc = FAULTS[fid]
    meta = {"id": iid, "run": RUN, "fault": fid, "fault_desc": desc, "model": C.MODELS.get(MODEL, "none"), "llm_calls": 0, "attempts": 0,
            "blocked_attempts": 0, "actions_applied": []}
    transcript, gate_log = [], []
    if P.streak(now() - 15) < 5 and not fast_reset():
        raise SystemExit("precheck failed")

    t0 = now(); meta["t0"] = iso(t0)
    log(f"FAST INJECT {iid} {fid} {desc}"); C.status(state="FAST_INJECT", incident=iid)
    inject()
    while now() < t0 + ALERT_WAIT:  # alert = 3 failed journeys after inject
        time.sleep(0.5)
        if sum(1 for x in P.since(t0) if not x[1]) >= 3:
            break
    fails = [x for x in P.since(t0) if not x[1]]
    meta["impact"] = len(fails) >= 3
    t_alert = now(); meta["alert_s"] = round(t_alert - t0, 1)
    outcome, final = None, None
    if not meta["impact"]:
        outcome = "NO_IMPACT"
    elif MODEL == "none":  # control: same windows, nobody acts
        while now() < t_alert + AGENT_S + WATCH_S:
            time.sleep(1)
            if P.streak(t_alert) >= OK_STREAK:
                outcome = "SELF_HEALED"; meta["fixed_s"] = round(now() - t0, 1); break
        outcome = outcome or "TIMEOUT"
    else:
        recent = P.since(t_alert - 10)
        steps = collections.Counter(x[2].split(":")[0] for x in recent if not x[1])
        alert = (f"ALERT {iso()}: synthetic shopper journeys (1/s: product page, recommendations, ads, cart, checkout) "
                 f"{sum(1 for x in recent if x[1])}/{len(recent)} OK in the last 10 s (normal 100%). "
                 f"Failing step: {dict(steps)}. Example error: {fails[-1][2]}. Users are affected now: be fast, "
                 f"you have about 30 seconds. Investigate and fix.")
        messages = [{"role": "system", "content": C.SYSTEM}, {"role": "user", "content": alert}]
        transcript += [{"t": iso(), "setup": {"model": meta["model"], "system": C.SYSTEM, "tools": C.TOOLS}}, {"t": iso(), "alert": alert}]
        deadline = t_alert + AGENT_S
        try:
            while outcome is None and meta["llm_calls"] < MAX_CALLS and meta["attempts"] < 2 and now() < deadline + WATCH_S:
                meta["llm_calls"] += 1
                final_only = now() > deadline - 10 or meta["llm_calls"] >= MAX_CALLS - 1
                if final_only:
                    messages.append({"role": "user", "content": "Time is up. Stop calling tools. Reply now with ONLY the JSON {\"diagnosis\",\"confidence\",\"actions\"}."})
                msg = C.llm(MODEL, messages, transcript, final_only)
                messages.append({k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")} | {"role": "assistant"})
                if msg.get("tool_calls"):
                    for tc in msg["tool_calls"]:
                        try:
                            args = json.loads(tc["function"].get("arguments") or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        res = C.run_tool(tc["function"]["name"], args)
                        transcript.append({"t": iso(), "tool": tc["function"]["name"], "args": args, "result": res[:20000], "result_len": len(res)})
                        messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": res})
                    continue
                final = C.parse_final(msg.get("content"))
                if final is None:
                    messages.append({"role": "user", "content": "Reply with ONLY the JSON object {\"diagnosis\",\"confidence\",\"actions\"}."})
                    continue
                meta["diagnosis"], meta["confidence"] = final.get("diagnosis"), final.get("confidence")
                meta["decided_s"] = round(now() - t0, 1)
                if not final["actions"]:
                    outcome = "GAVE_UP"; break
                verdicts = [(a, *gate(a, True)[:2]) for a in final["actions"]]
                gate_log += [f"{iso()} DRYRUN {'PASS' if v else 'BLOCK'} {json.dumps(a)} :: {m}" for a, v, m in verdicts]
                meta["attempts"] += 1
                if not all(v[1] for v in verdicts):
                    meta["blocked_attempts"] += 1
                    errs = "; ".join(f"{a.get('type')} {a.get('name', '')}: {m}" for a, v, m in verdicts if not v)
                    messages.append({"role": "user", "content": f"Gate REJECTED your actions (nothing applied): {errs}. Revise or return actions []."})
                    continue
                snaps = []
                for a in final["actions"]:
                    v, m, s = gate(a, False)
                    gate_log.append(f"{iso()} APPLY {'OK' if v else 'FAIL'} {json.dumps(a)} :: {m}")
                    snaps += [s] if s else []
                    if v:
                        meta["actions_applied"].append(a)
                applied = now()
                while now() < applied + WATCH_S:
                    time.sleep(1)
                    if P.streak(applied) >= OK_STREAK:
                        outcome = "FIXED"; meta["fixed_s"] = round(now() - t0, 1); break
                if outcome:
                    break
                after = P.since(applied + 5)
                ok_rate = sum(1 for x in after if x[1]) / max(1, len(after))
                try:  # state snapshot so harness noise vs real failure can be told apart later
                    fl = {k: v["defaultVariant"] for k, v in read_flags()["flags"].items() if v["defaultVariant"] != "off"}
                    dp = {o.metadata.name: f"{o.status.ready_replicas or 0}/{o.spec.replicas}" for o in APPS.list_namespaced_deployment(NS).items if (o.status.ready_replicas or 0) < (o.spec.replicas or 0) or not o.spec.replicas}
                    gate_log.append(f"{iso()} STATE flags_not_off={fl} deploys_not_ready={dp} last_fail={[x[2] for x in after if not x[1]][-1:]}")
                except Exception as e:
                    gate_log.append(f"{iso()} STATE error {e}")
                if ok_rate < 0.5:
                    undo(snaps); gate_log.append(f"{iso()} ROLLBACK (not recovered, {ok_rate:.0%} OK)")
                messages.append({"role": "user", "content": f"Applied, but journeys are still failing ({ok_rate:.0%} OK since). "
                                 + ("It was rolled back. " if ok_rate < 0.5 else "") + "Continue or return actions []."})
        except C.LLMError as e:
            meta["llm_error"] = str(e)
        outcome = outcome or "TIMEOUT"

    meta.update(outcome=outcome, end=iso(), total_s=round(now() - t0, 1),
                root_cause_ok=all(any(k in (meta.get("diagnosis") or "").lower() for k in g) for g in rc) if meta.get("diagnosis") else False)
    U = [e["usage"] for e in transcript if e.get("usage")]
    meta["cost_usd"] = round(sum(u.get("cost", 0) for u in U), 5)
    log(f"FAST OUTCOME {iid} {fid} {outcome} total={meta['total_s']}s cost=${meta['cost_usd']} rc={meta['root_cause_ok']}")
    jdump(f"{d}/transcript.json", transcript)
    jdump(f"{d}/probe.json", [(round(t - t0, 2), ok, err) for t, ok, err in P.since(t0 - 10)])
    open(f"{d}/gate.log", "w").write("\n".join(gate_log) + "\n")
    meta["reset_ok"] = fast_reset()
    jdump(f"{d}/meta.json", meta)
    with open(f"{DATA}/results/{RUN}/summary.jsonl", "a") as f:
        f.write(json.dumps({k: meta.get(k) for k in ("id", "fault", "outcome", "root_cause_ok", "alert_s", "decided_s", "fixed_s", "total_s", "llm_calls", "cost_usd", "impact")}) + "\n")
    return meta


def main():
    P.start(); C.serve()
    time.sleep(8)
    log("fast mode", RUN_MODELS, FAULT_IDS, "x", REPEAT)
    n = 0
    for r in range(REPEAT):
        for fid in FAULT_IDS:
          for MODEL in RUN_MODELS:  # interleaved so every model sees the same cluster conditions
            if MODEL == "none" and r >= int(os.environ.get("FAST_CONTROL_REPS", "1")):
                continue  # control only proves faults persist; one round per fault is enough
            n += 1
            iid = f"{MODEL}{int(now())}-{fid}"
            try:
                incident(iid, fid, MODEL)
            except C.LLMError as e:
                log("llm error", e); fast_reset()
            except SystemExit:
                raise
            except Exception:
                log("FAST ERROR", traceback.format_exc()[-800:]); fast_reset()
    C.status(state="FAST_IDLE")
    log("fast done")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
