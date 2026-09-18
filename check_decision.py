import json
from policy_gate import evaluate, DecisionStore

req = {
    "request_id": "req-1042",
    "actor": {"id": "agent-17", "role": "finance_agent"},
    "action": {"type": "transfer_funds", "amount": 24000, "currency": "USD", "destination": "vendor-882"},
    "evidence": {
        "account_balance": 81000,
        "vendor_status": "approved",
        "vendor_status_checked_at": "2026-09-14T20:15:00Z",
    },
    "evaluated_at": "2026-09-14T20:20:00Z",
}

d = evaluate(req, store=DecisionStore())
print(json.dumps(d, indent=2))

required = {"request_id", "verdict", "reason", "policy_version", "evaluated_at", "decision_id"}
missing = required - d.keys()
print()
print("Missing fields:", missing if missing else "none -- shape is correct")
