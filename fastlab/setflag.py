"""Instant flag change: rewrite the flag file flagd watches (shared with flagd-ui). Usage: setflag.py flag=variant ..."""
import json, subprocess, sys
pod = subprocess.check_output(["kubectl", "-n", "trust-lab", "get", "pod", "-l", "app.kubernetes.io/component=flagd", "-o", "name"], text=True).split()[0]
ex = ["kubectl", "-n", "trust-lab", "exec", "-i", pod, "-c", "flagd-ui", "--"]
j = json.loads(subprocess.check_output(ex + ["cat", "/app/data/demo.flagd.json"]))
for a in sys.argv[1:]:
    f, v = a.split("="); j["flags"][f]["defaultVariant"] = v
subprocess.run(ex + ["sh", "-c", "cat > /app/data/.tmp && mv /app/data/.tmp /app/data/demo.flagd.json"], input=json.dumps(j, indent=2).encode(), check=True)
