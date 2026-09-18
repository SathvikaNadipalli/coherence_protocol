"""
sample_requests.py
-------------------
A hand-picked set of mock requests covering every verdict path in the
evaluator, plus a couple of the trickier multi-rule interactions. Run it
directly to see what each one decides and why:

    python3 sample_requests.py

Each entry is (label, request_dict). The label just describes what the
scenario is meant to exercise -- it isn't part of the request itself.
"""

from __future__ import annotations

from policy_gate import evaluate, DecisionStore


class _Drop:
    """Sentinel: pass DROP as a field's value to delete it from the request
    entirely, instead of setting it to a value (used for 'evidence is
    missing' scenarios, as opposed to 'evidence is null/invalid')."""


DROP = _Drop()


def req(**overrides):
    """A clean baseline transfer_funds request; overrides layer on top.

    overrides["evidence"] / ["action"] / ["actor"] merge into the matching
    sub-dict; setting a field to DROP removes that key from the sub-dict
    (or from the top-level request) instead of overwriting it.
    """
    base = {
        "request_id": "req-base",
        "actor": {"id": "agent-17", "role": "finance_agent"},
        "action": {
            "type": "transfer_funds",
            "amount": 24000,
            "currency": "USD",
            "destination": "vendor-882",
        },
        "evidence": {
            "account_balance": 81000,
            "vendor_status": "approved",
            "vendor_status_checked_at": "2026-09-14T20:15:00Z",
        },
        "evaluated_at": "2026-09-14T20:20:00Z",
    }
    for key, value in overrides.items():
        if key in ("actor", "action", "evidence") and isinstance(value, dict):
            merged = dict(base[key])
            for sub_key, sub_value in value.items():
                if sub_value is DROP:
                    merged.pop(sub_key, None)
                else:
                    merged[sub_key] = sub_value
            base[key] = merged
        elif value is DROP:
            base.pop(key, None)
        else:
            base[key] = value
    return base


SAMPLES = [
    (
        "Clean transfer, everything checks out",
        req(request_id="sample-01"),
    ),
    (
        "Vendor status is 'rejected'",
        req(request_id="sample-02", evidence={"vendor_status": "rejected"}),
    ),
    (
        "Amount exceeds the account balance",
        req(request_id="sample-03", action={"amount": 5000}, evidence={"account_balance": 1000}),
    ),
    (
        "Actor role not authorized for transfer_funds",
        req(request_id="sample-04", actor={"role": "marketing_agent"}),
    ),
    (
        "Vendor evidence is 80 minutes old (stale)",
        req(request_id="sample-05", evidence={"vendor_status_checked_at": "2026-09-14T19:00:00Z"}),
    ),
    (
        "account_balance evidence missing entirely",
        req(request_id="sample-06", evidence={"account_balance": DROP}),
    ),
    (
        "vendor_status evidence missing entirely",
        req(request_id="sample-07", evidence={"vendor_status": DROP}),
    ),
    (
        "Malformed request (no action object)",
        {"request_id": "sample-08", "actor": {"role": "finance_agent"}},
    ),
    (
        "Amount over $50,000 but evidence is otherwise clean -> REFER",
        req(request_id="sample-09", action={"amount": 90000}, evidence={"account_balance": 200000}),
    ),
    (
        "Amount over $50,000 AND vendor rejected -> HALT beats REFER",
        req(
            request_id="sample-10",
            action={"amount": 90000},
            evidence={"account_balance": 200000, "vendor_status": "rejected"},
        ),
    ),
    (
        "Unknown action type (never granted to anyone)",
        req(request_id="sample-11", action={"type": "delete_vendor", "amount": 100}),
    ),
    (
        "Balance exceeded AND vendor evidence stale -> certain HALT wins",
        req(
            request_id="sample-12",
            action={"amount": 5000},
            evidence={"account_balance": 1000, "vendor_status_checked_at": "2026-09-14T19:00:00Z"},
        ),
    ),
]


def main() -> None:
    store = DecisionStore()
    width = max(len(label) for label, _ in SAMPLES)

    print(f"{'#':<3} {'VERDICT':<8} {'SCENARIO':<{width}}  REASON")
    print("-" * (3 + 1 + 8 + 1 + width + 2 + 60))

    for i, (label, request) in enumerate(SAMPLES, start=1):
        decision = evaluate(request, store=store)
        print(f"{i:<3} {decision['verdict']:<8} {label:<{width}}  {decision['reason']}")


if __name__ == "__main__":
    main()
