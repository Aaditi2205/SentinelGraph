"""Run the bounded failure matrix against a running local server."""
import json
import urllib.request

def post(path, payload):
    request = urllib.request.Request(f"http://127.0.0.1:8000{path}", method="POST", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())

for mode in ("model", "graph", "evidence", "identity", "drift"):
    result = post("/api/agent/decide", {"failureMode": mode})
    print(f"{mode:10} -> {result['action']:6} degraded={result['degraded']} audit={result['audit']['chainValid']}")
result = post("/api/razorpay/simulate", {"mode": "out_of_order"})
print(f"out_of_order -> stateApplied={result['stateApplied']} current={result['currentState']['event']}")
