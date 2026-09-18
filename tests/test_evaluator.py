import copy
import sys
import os
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from policy_gate import evaluate, DecisionStore, IdempotencyConflictError, POLICY_VERSION


def base_request(**overrides):
    req = {
        "request_id": "req-1042",
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
    req.update(overrides)
    return req


class DecisionShapeTests(unittest.TestCase):
    def test_decision_has_required_fields(self):
        d = evaluate(base_request(request_id="req-shape"), store=DecisionStore())
        for key in ("request_id", "verdict", "reason", "policy_version", "evaluated_at", "decision_id"):
            self.assertIn(key, d)
        self.assertEqual(d["policy_version"], POLICY_VERSION)
        self.assertIn(d["verdict"], ("ADMIT", "HALT", "DEFER", "REFER"))


class NormalAdmitTests(unittest.TestCase):
    def test_admit_normal(self):
        d = evaluate(base_request(request_id="req-admit-1"), store=DecisionStore())
        self.assertEqual(d["verdict"], "ADMIT")


class ExplicitHaltTests(unittest.TestCase):
    def test_halt_vendor_not_approved(self):
        req = base_request(request_id="req-halt-vendor")
        req["evidence"]["vendor_status"] = "rejected"
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "HALT")
        self.assertIn("vendor", d["reason"].lower())

    def test_halt_amount_exceeds_balance(self):
        req = base_request(request_id="req-halt-balance")
        req["action"]["amount"] = 5000
        req["evidence"]["account_balance"] = 1000
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "HALT")
        self.assertIn("balance", d["reason"].lower())

    def test_halt_unauthorized_role(self):
        req = base_request(request_id="req-halt-role")
        req["actor"]["role"] = "marketing_agent"
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "HALT")
        self.assertIn("not authorized", d["reason"].lower())


class StaleEvidenceTests(unittest.TestCase):
    def test_defer_stale_vendor_evidence(self):
        req = base_request(request_id="req-defer-stale")
        req["evidence"]["vendor_status_checked_at"] = "2026-09-14T19:00:00Z"  # 80 min old
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "DEFER")
        self.assertIn("minute", d["reason"].lower())


class MissingEvidenceTests(unittest.TestCase):
    def test_defer_missing_account_balance(self):
        req = base_request(request_id="req-defer-missing-balance")
        del req["evidence"]["account_balance"]
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "DEFER")
        self.assertIn("account_balance", d["reason"])

    def test_defer_missing_vendor_status(self):
        req = base_request(request_id="req-defer-missing-vendor")
        del req["evidence"]["vendor_status"]
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "DEFER")
        self.assertIn("vendor_status", d["reason"])

    def test_defer_malformed_request_missing_action(self):
        req = {"request_id": "req-malformed", "actor": {"role": "finance_agent"}}
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "DEFER")

    def test_defer_missing_request_id_still_returns_decision(self):
        req = base_request()
        del req["request_id"]
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "DEFER")


class AnotherAuthorityTests(unittest.TestCase):
    def test_refer_large_transfer(self):
        req = base_request(request_id="req-refer-large")
        req["action"]["amount"] = 75000
        req["evidence"]["account_balance"] = 200000
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "REFER")
        self.assertIn("another authority", d["reason"].lower())


class UnknownActionTests(unittest.TestCase):
    def test_halt_unknown_action_type(self):
        req = base_request(request_id="req-unknown-action")
        req["action"] = {"type": "delete_vendor", "target": "vendor-882"}
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "HALT")
        self.assertIn("not covered", d["reason"].lower())


class MultiRuleInteractionTests(unittest.TestCase):
    def test_vendor_not_approved_outranks_refer(self):
        """Amount over threshold AND vendor not approved -> HALT wins.

        With the evaluation order 1, 2, 3, 5, 6, 4, 7, the evidence-based
        checks (5: balance, 6: vendor) run before the authority-threshold
        check (4). So a rejected vendor is caught at step 6 and returns
        HALT before step 4 ever gets a chance to REFER. REFER is now only
        reached when the evidence is otherwise entirely clean.
        """
        req = base_request(request_id="req-multi-refer")
        req["action"]["amount"] = 90000
        req["evidence"]["account_balance"] = 200000
        req["evidence"]["vendor_status"] = "rejected"
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "HALT")
        self.assertIn("vendor", d["reason"].lower())

    def test_refer_only_reached_with_clean_evidence(self):
        """Amount over threshold, but balance and vendor both check out ->
        REFER. This is the only path to REFER now: evidence has to pass
        steps 5 and 6 cleanly before step 4 is even reached."""
        req = base_request(request_id="req-multi-refer-clean")
        req["action"]["amount"] = 90000
        req["evidence"]["account_balance"] = 200000
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "REFER")

    def test_certain_halt_outranks_stale_evidence_defer(self):
        """Amount exceeds balance (certain HALT) AND vendor evidence is
        stale (would otherwise DEFER) -> HALT wins.

        Rationale: DEFER means "cannot legitimately determine ADMIT or
        HALT." If HALT is already fully supported by evidence that has no
        freshness requirement (account_balance), the stale, unrelated
        vendor evidence doesn't change that we CAN determine HALT.
        """
        req = base_request(request_id="req-multi-halt")
        req["action"]["amount"] = 5000
        req["evidence"]["account_balance"] = 1000
        req["evidence"]["vendor_status_checked_at"] = "2026-09-14T19:00:00Z"
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "HALT")
        self.assertIn("balance", d["reason"].lower())

    def test_unknown_action_outranks_everything(self):
        """Unauthorized-looking role + huge amount + unknown action type ->
        still HALT on scope, because we shouldn't even try to interpret
        amount/authority for a type the policy has no schema for."""
        req = base_request(request_id="req-multi-unknown")
        req["action"] = {"type": "launch_missiles", "amount": 999999999}
        req["actor"]["role"] = "finance_agent"
        d = evaluate(req, store=DecisionStore())
        self.assertEqual(d["verdict"], "HALT")
        self.assertIn("not covered", d["reason"].lower())


class DeterminismTests(unittest.TestCase):
    def test_identical_input_same_decision_id(self):
        store1 = DecisionStore()
        store2 = DecisionStore()
        req_a = base_request(request_id="req-det-a")
        req_b = base_request(request_id="req-det-a")  # separate dict, same content
        d1 = evaluate(req_a, store=store1)
        d2 = evaluate(req_b, store=store2)
        self.assertEqual(d1["decision_id"], d2["decision_id"])
        self.assertEqual(d1["verdict"], d2["verdict"])


class ReplayTests(unittest.TestCase):
    def test_identical_request_replayed_returns_same_decision(self):
        store = DecisionStore()
        req = base_request(request_id="req-replay-1")
        d1 = evaluate(req, store=store)
        d2 = evaluate(copy.deepcopy(req), store=store)
        self.assertEqual(d1, d2)

    def test_same_request_id_changed_payload_conflicts(self):
        store = DecisionStore()
        req1 = base_request(request_id="req-conflict-1")
        evaluate(req1, store=store)

        req2 = base_request(request_id="req-conflict-1")
        req2["action"]["amount"] = 1  # materially different payload, same id
        with self.assertRaises(IdempotencyConflictError):
            evaluate(req2, store=store)

    def test_same_request_id_same_payload_different_object_identity_ok(self):
        store = DecisionStore()
        req1 = base_request(request_id="req-conflict-2")
        d1 = evaluate(req1, store=store)
        req2 = copy.deepcopy(req1)
        d2 = evaluate(req2, store=store)
        self.assertEqual(d1["decision_id"], d2["decision_id"])

    def test_policy_change_does_not_alter_cached_replay(self):
        import policy_gate.policy as policy_mod

        store = DecisionStore()
        req = base_request(request_id="req-policy-change")
        d1 = evaluate(req, store=store)

        original_version = policy_mod.POLICY_VERSION
        original_threshold = dict(policy_mod.REFER_ABOVE_AMOUNT)
        try:
            policy_mod.POLICY_VERSION = "9999.simulated"
            policy_mod.REFER_ABOVE_AMOUNT["transfer_funds"] = 1
            d2 = evaluate(copy.deepcopy(req), store=store)
        finally:
            policy_mod.POLICY_VERSION = original_version
            policy_mod.REFER_ABOVE_AMOUNT.clear()
            policy_mod.REFER_ABOVE_AMOUNT.update(original_threshold)

        # Replay returns the ORIGINAL decision unchanged (same decision_id,
        # same verdict, same policy_version) even though the in-place
        # mutation above changed the REFER threshold that a fresh
        # evaluation of this same request would now be evaluated against.
        self.assertEqual(d1, d2)
        self.assertEqual(d2["verdict"], "ADMIT")


if __name__ == "__main__":
    unittest.main()
