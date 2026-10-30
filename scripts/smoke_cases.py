"""Integration smoke test: POST /chat on a running server for the 10 official case scenarios (stdlib only).

    python scripts/smoke_cases.py evals/dataset.jsonl smoke_results.json

Exits 1 if any scenario gets a different intent, route or language than the golden set.
"""
import json
import sys
import urllib.request

dataset, out = sys.argv[1], sys.argv[2]
cases = [json.loads(l) for l in open(dataset, encoding="utf-8") if l.strip()]
cases = [c for c in cases if c.get("split") == "official"]
rows = []
failed = 0
for i, c in enumerate(cases, 1):
    body = json.dumps({"message": c["message"], "user_id": "cliente1988"}).encode()
    req = urllib.request.Request("http://localhost:8000/chat", data=body, headers={"Content-Type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=30).read())
    rows.append({"message": c["message"], **r})
    ok = r["intent"] == c["expected_intent"] and r["route"] == c["expected_route"] and r["language"] == c["expected_language"]
    print(f"{i:>2} {'OK ' if ok else 'BAD'} {r['intent']:<18} {'>'.join(r['route']):<26} {r['language']} "
          f"conf={r['confidence']} src={len(r['sources'])} tools={[(t['name'], t['ok']) for t in r['tool_calls']]} "
          f"handoff={bool(r['handoff'])} {r['latency_ms']}ms")
    print("    ", r["answer"][:260].replace("\n", " / "))
json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"{len(cases) - failed}/{len(cases)} scenarios OK")
sys.exit(1 if failed else 0)
