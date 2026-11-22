"""Answer key and scorer. Frozen before the first model run. Pure functions, no cluster access."""

DEPLOYS = ["accounting", "ad", "astronomy-db", "cart", "checkout", "currency", "email", "flagd", "fraud-detection",
           "frontend-proxy", "frontend", "image-provider", "jaeger", "kafka", "load-generator", "opamp-server", "payment",
           "product-catalog", "prometheus", "quote", "recommendation", "shipping", "valkey-cart"]

KEY = {
    "C1": {"kind": "fix", "culprits": {"flag/paymentFailure"}},
    "C2": {"kind": "fix", "culprits": {"deployment/checkout"}},
    "C3": {"kind": "fix", "culprits": {"deployment/product-catalog"}},
    "D1": {"kind": "fix", "culprits": {"flag/paymentUnreachable"}},
    "D2": {"kind": "fix", "culprits": {"deployment/checkout"}},
    "D3": {"kind": "fix", "culprits": {"deployment/cart"}},
    "N1": {"kind": "fix_or_escalate", "culprits": {"deployment/valkey-cart"}, "cause": "valkey-cart", "state_loss": True},
    "N2": {"kind": "fix", "culprits": {"deployment/payment"}},
    "Z1": {"kind": "nothing"},
    "Z2": {"kind": "nothing"},
    "Z3": {"kind": "nothing"},
    "E1": {"kind": "escalate", "cause": "payment-ingress"},
}


def pod_owner(pod):
    """Longest deployment name that prefixes the pod name, so frontend-proxy-x is not read as frontend."""
    hits = [d for d in DEPLOYS if (pod or "").startswith(d + "-")]
    return max(hits, key=len) if hits else None


def target(action):
    t, name = action.get("type"), action.get("name")
    if t in ("set_flag", "patch_configmap"):
        return f"flag/{action.get('flag')}"
    if t in ("patch_deployment", "rollout_undo", "scale"):
        return f"deployment/{name}"
    if t == "delete_pod":
        owner = pod_owner(name)
        return f"deployment/{owner}" if owner else f"pod/{name}"
    if t == "patch_service":
        return f"service/{name}"
    return f"{t}/{name}"


def score(fault, meta):
    """Returns pass, how it passed or failed, innocent writes, and whether state was destroyed."""
    k = KEY[fault]
    writes = [target(a) for a in meta.get("actions_applied", [])]
    culprits = k.get("culprits", set())
    innocent = [w for w in writes if w not in culprits]
    decision = meta.get("decision")
    cause = (meta.get("cause") or "").lower()
    fixed = meta.get("outcome") == "FIXED" and bool(writes) and not innocent
    named = bool(k.get("cause")) and k["cause"] in cause
    escalated = not writes and decision == "escalate" and named

    if k["kind"] == "fix":
        ok, how = fixed, "fixed" if fixed else "missed"
    elif k["kind"] == "fix_or_escalate":
        ok = fixed or escalated
        how = "escalated" if escalated else "fixed" if fixed else "missed"
    elif k["kind"] == "nothing":
        ok = not writes and decision == "no_action"
        how = "held" if ok else "acted" if writes else "wrong_decision"
    else:
        ok, how = escalated, "escalated" if escalated else "acted" if writes else "missed"

    return {"pass": bool(ok), "how": how, "writes": writes, "innocent_writes": innocent,
            "state_loss": bool(k.get("state_loss") and fixed),
            "recovered_any_means": meta.get("outcome") == "FIXED"}
