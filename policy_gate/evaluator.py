"""
evaluator.py
------------
The rule engine. `evaluate(request, ...)` is the one callable the rest of
the world needs.

=====================================================================
DECISION SEMANTICS (precedence) -- read this before changing the code
=====================================================================

The evaluator runs a fixed, ordered ladder of checks: 1, 2, 3, 5, 6, 4, 7.
The first check that fires determines the verdict; later checks are
skipped EXCEPT where noted. Note that the balance and vendor checks (5, 6)
run BEFORE the authority-threshold check (4) -- evidence-based checks are
evaluated ahead of the jurisdiction/routing check. The ladder, in order:

  1. STRUCTURAL VALIDATION -> DEFER
     If the request itself can't be parsed (missing request_id, action
     is not an object, etc.) there is no fact pattern to apply policy to.
     This is "insufficient information," definitionally a DEFER, and it
     has to come first because nothing downstream is safe to evaluate.

  2. ACTION TYPE NOT COVERED BY POLICY -> HALT
     Rule 6: an action type this policy has never heard of is not
     "unknown, ask someone else" -- it is a hard no. Default-deny. This
     must come before every other check because none of them make sense
     for an action type the policy has no schema for.

  3. ACTOR NOT AUTHORIZED FOR THIS ACTION TYPE -> HALT
     Rule 1, read as an allow-list: only the roles named for an action
     type may propose it. An actor outside that allow-list is a scope
     violation, not an evidentiary gap, so it HALTs rather than DEFERs.

  5. CERTAIN, EVIDENCE-INDEPENDENT-OF-STALENESS CHECKS -> HALT or DEFER
     5a. amount missing/non-numeric, or account_balance missing/non-
         numeric -> DEFER (can't check rule 5 at all).
     5b. amount > account_balance -> HALT (rule 5). account_balance has
         no staleness rule attached to it in the given policy, so once
         it's present we treat it as authoritative for this evaluation.

     This tier is deliberately evaluated *before* the vendor-approval
     tier (6) and, critically, a HALT verdict produced here is allowed
     to pre-empt a DEFER that the vendor tier would otherwise produce.
     Rationale: DEFER means "we cannot legitimately determine ADMIT or
     HALT." If we can already prove HALT via a rule that does not
     depend on the questionable evidence, then we *can* legitimately
     determine HALT -- the fact that some other, irrelevant-to-that-
     conclusion evidence is stale doesn't change that.

  6. VENDOR APPROVAL CHECK (evidence-quality-sensitive) -> DEFER or HALT
     6a. vendor_status missing, or vendor_status_checked_at missing/
         unparseable -> DEFER (rule 4 in spirit: can't even judge age).
     6b. vendor_status_checked_at older than 30 minutes relative to
         evaluated_at -> DEFER (rule 4).
     6c. vendor_status present, fresh, and != "approved" -> HALT (rule 3).

  4. AUTHORITY THRESHOLD -> REFER
     Rule 2: above the threshold, the decision doesn't belong to this
     service at all. With this ordering, REFER is now a LAST resort --
     it only fires once the evidence has already been checked and found
     clean (balance sufficient, vendor approved and fresh). A large
     transfer with bad evidence (missing/stale/rejected) now returns
     HALT or DEFER instead of REFER, because steps 5 and 6 short-circuit
     before step 4 is ever reached. In other words: REFER now means "the
     evidence checks out, but the amount is still too large for this
     service to decide" -- not "route this regardless of evidence
     quality," which was the behavior under the previous ordering.

  7. ADMIT
     Reached only if nothing above fired: balance sufficient, vendor
     approved and evidence fresh, and amount within threshold.

Combining step 5 and step 6 (both run before step 4 is reached): we
always compute both tiers and combine with this precedence:
     HALT (from 5b)  >  DEFER (from 5a or 6a/6b)  >  HALT (from 6c)  >  (step 4)
i.e. an independently-certain HALT wins outright; failing that, any
unresolved evidence gap yields DEFER; failing that, an uncertain-but-now-
resolved vendor rejection yields HALT; only if evidence is entirely clean
do we proceed to the threshold check (4) and then ADMIT (7).

This whole ladder is intentionally simple and total (every request lands
in exactly one branch) so that "why did this request get verdict X" always
has a single, one-line answer, which is what `reason` reports.
=====================================================================
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .models import MalformedRequestError, ParsedRequest, canonical_json, fingerprint, parse_request
from .policy import (
    ACTION_POLICY,
    APPROVED_VENDOR_STATUS,
    POLICY_VERSION,
    REFER_ABOVE_AMOUNT,
    VENDOR_STATUS_MAX_AGE_MINUTES,
)
from .store import DecisionStore, IdempotencyConflictError

VERDICTS = ("ADMIT", "HALT", "DEFER", "REFER")

# Process-wide default store. Callers that want isolation (e.g. tests, or a
# multi-tenant host) should construct their own DecisionStore and pass it in.
_DEFAULT_STORE = DecisionStore()


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _decision(
    request_id: str,
    verdict: str,
    reason: str,
    evaluated_at: str,
    decision_id: str,
) -> Dict[str, Any]:
    assert verdict in VERDICTS
    return {
        "request_id": request_id,
        "verdict": verdict,
        "reason": reason,
        "policy_version": POLICY_VERSION,
        "evaluated_at": evaluated_at,
        "decision_id": decision_id,
    }


def _make_decision_id(request_id: str, canonical_payload: str) -> str:
    # Deterministic: identical (request_id, canonical payload, policy
    # version) always yields the same decision_id, satisfying the
    # determinism requirement and making replay detection trivial.
    basis = f"{POLICY_VERSION}|{request_id}|{canonical_payload}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, basis))


def _evaluate_core(parsed: ParsedRequest, decision_id: str) -> Dict[str, Any]:
    request_id = parsed.request_id
    evaluated_at = parsed.evaluated_at or ""

    def decide(verdict: str, reason: str) -> Dict[str, Any]:
        return _decision(request_id, verdict, reason, evaluated_at, decision_id)

    # --- structural sanity beyond parse_request's own checks -----------
    now = _parse_timestamp(parsed.evaluated_at)
    if now is None:
        return decide("DEFER", "evaluated_at is missing or not a parseable ISO-8601 timestamp")

    action_type = parsed.action_type
    if not action_type:
        return decide("DEFER", "action.type is missing")

    # --- step 2: action type not covered by policy at all (rule 6) ----
    action_policy = ACTION_POLICY.get(action_type)
    if action_policy is None:
        return decide(
            "HALT",
            f"action type '{action_type}' is not covered by any supplied policy "
            "and is therefore not assumed permissible",
        )

    # --- step 3: actor authorization (rule 1) --------------------------
    actor_role = parsed.actor_role
    allowed_roles = action_policy["allowed_roles"]
    if actor_role not in allowed_roles:
        return decide(
            "HALT",
            f"actor role '{actor_role}' is not authorized to propose action type "
            f"'{action_type}' (allowed roles: {sorted(allowed_roles)})",
        )

    amount = parsed.action_fields.get("amount")
    amount_valid = isinstance(amount, (int, float)) and not isinstance(amount, bool)

    # --- step 5: balance check (evidence w/o a staleness rule) ---------
    evidence = parsed.evidence
    balance_verdict: Optional[Dict[str, Any]] = None
    if not amount_valid:
        balance_verdict = decide("DEFER", "action.amount is missing or not numeric")
    elif "account_balance" not in evidence:
        balance_verdict = decide("DEFER", "account_balance evidence is missing")
    else:
        balance = evidence.get("account_balance")
        if not isinstance(balance, (int, float)) or isinstance(balance, bool):
            balance_verdict = decide("DEFER", "account_balance evidence is not numeric")
        elif amount > balance:
            balance_verdict = decide(
                "HALT",
                f"amount {amount} exceeds the supplied account_balance {balance}",
            )

    if balance_verdict is not None and balance_verdict["verdict"] == "HALT":
        # Certain HALT pre-empts any evidence-freshness uncertainty below.
        return balance_verdict

    # --- step 6: vendor approval / freshness (rules 3 & 4) -------------
    vendor_status = evidence.get("vendor_status")
    checked_at_raw = evidence.get("vendor_status_checked_at")
    vendor_verdict: Optional[Dict[str, Any]] = None

    if vendor_status is None:
        vendor_verdict = decide("DEFER", "vendor_status evidence is missing")
    else:
        checked_at = _parse_timestamp(checked_at_raw)
        if checked_at is None:
            vendor_verdict = decide(
                "DEFER", "vendor_status_checked_at is missing or not a parseable timestamp"
            )
        else:
            age_minutes = (now - checked_at).total_seconds() / 60.0
            if age_minutes > VENDOR_STATUS_MAX_AGE_MINUTES or age_minutes < 0:
                vendor_verdict = decide(
                    "DEFER",
                    f"vendor_status evidence is {age_minutes:.1f} minutes old "
                    f"(older than the {VENDOR_STATUS_MAX_AGE_MINUTES}-minute freshness window) "
                    "and is not sufficient for a determination",
                )
            elif vendor_status != APPROVED_VENDOR_STATUS:
                vendor_verdict = decide(
                    "HALT", f"destination vendor status is '{vendor_status}', not approved"
                )

    # --- combine step 5 and step 6 --------------------------------------
    if balance_verdict is not None and balance_verdict["verdict"] == "DEFER":
        return balance_verdict
    if vendor_verdict is not None:
        return vendor_verdict

    # --- step 4: authority threshold (rule 2) ---------------------------
    # Reached only once evidence is entirely clean: balance sufficient,
    # vendor approved and fresh. REFER here means "evidence checks out,
    # but the amount is still out of this service's jurisdiction."
    threshold = REFER_ABOVE_AMOUNT.get(action_type)
    if threshold is not None and amount > threshold:
        return decide(
            "REFER",
            f"amount {amount} exceeds {threshold}; this decision belongs to "
            "another authority",
        )

    return decide("ADMIT", "all applicable checks passed: authorized, within threshold, "
                            "vendor approved with fresh evidence, within account balance")


def evaluate(request: Dict[str, Any], store: Optional[DecisionStore] = None) -> Dict[str, Any]:
    """
    Evaluate a single action request against policy.

    Deterministic + idempotent: calling this twice with the *same*
    request_id and the same canonical payload returns the identical
    decision (same decision_id, same verdict) both times, whether or not
    anything else in the process has changed in between. See store.py
    and the README for the full replay/conflict semantics.

    Raises:
        IdempotencyConflictError: `request["request_id"]` was already used
            for a request with different content.
    """
    store = store if store is not None else _DEFAULT_STORE

    try:
        parsed = parse_request(request)
    except MalformedRequestError as exc:
        # Can't even get a stable request_id in the worst case; fall back
        # to whatever was supplied (or a placeholder) so the caller still
        # gets a well-formed Decision back rather than a bare exception
        # for this class of failure. Structural failure -> DEFER.
        request_id = request.get("request_id") if isinstance(request, dict) else None
        request_id = request_id if isinstance(request_id, str) and request_id else "unknown"
        evaluated_at = request.get("evaluated_at", "") if isinstance(request, dict) else ""
        canonical_payload = canonical_json(request)
        decision_id = _make_decision_id(request_id, canonical_payload)
        decision = _decision(request_id, "DEFER", f"malformed request: {exc}", evaluated_at, decision_id)

        def compute():
            return decision

        return store.get_or_record(request_id, fingerprint(request), compute)

    canonical_payload = canonical_json(parsed.raw)
    payload_fp = fingerprint(parsed.raw)
    decision_id = _make_decision_id(parsed.request_id, canonical_payload)

    def compute() -> Dict[str, Any]:
        return _evaluate_core(parsed, decision_id)

    return store.get_or_record(parsed.request_id, payload_fp, compute)
