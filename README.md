# Coherence Protocol — Action Gate

Dependency-free policy evaluator: `evaluate(request) -> decision`. See
`policy_gate/evaluator.py`'s docstring for the rule ladder and rationale.
Tests: `python3 -m unittest discover -s tests` (21 tests). Sample
scenarios: `python3 sample_requests.py`. Optional HTTP server: `python3
-m policy_gate.http_app` (POST JSON to `/evaluate`).

## 1. Assumptions

`account_balance` has no freshness rule, so once present it's
authoritative — only `vendor_status` has an explicit 30-minute staleness
rule. "Another authority" (rule 2) is a per-action-type threshold
(`transfer_funds` at $50,000). `evaluated_at` must parse as ISO-8601;
without it, no staleness math is possible, so the request DEFERs. A
negative vendor-evidence age (checked-at in the future) is also
untrustworthy and DEFERs. The policy is a fixed allow-list keyed by
action type; anything absent is unauthorized by default (rule 6).

## 2. DEFER vs REFER

DEFER: not enough information to legitimately reach ADMIT or HALT —
missing/stale evidence, or a malformed request. It's about evidence
quality. REFER: everything checks out, but the decision is still out of
this service's jurisdiction — the $50,000 threshold routes elsewhere.
DEFER is "ask again with better evidence"; REFER is "ask someone else."

## 3. Rule precedence

A fixed ladder (full rationale in `evaluator.py`), evaluated as 1, 2, 3,
5, 6, 4, 7: structural validity → action covered by policy (rule 6) →
actor authorized (rule 1) → balance (rule 5) → vendor freshness/approval
(rules 3 & 4) → authority threshold (rule 2, REFER) → ADMIT. Scope (2, 3)
precedes everything, since an unknown/unauthorized action shouldn't be
evaluated further. Evidence quality (5, 6) precedes jurisdiction (4): a
request only reaches REFER once evidence is already clean, so REFER now
means "evidence checks out, but the amount is still someone else's call,"
not "route regardless of evidence." Within the evidence tier, a HALT
fully supported by non-stale evidence pre-empts a DEFER from unrelated
stale evidence.

## 4. What makes two evaluations "the same"

The canonical (key-sorted, whitespace-free) JSON of the whole request,
including `evaluated_at`, hashed as a fingerprint. Same `request_id` +
same fingerprint = same cached decision, same `decision_id`.

## 5. Retry/replay behavior (`store.py`)

1. **Identical retry**: fingerprint matches → return the original cached
   decision, never recompute.
2. **Same `request_id`, changed payload**: fingerprint mismatch →
   `IdempotencyConflictError` (HTTP 409). Never silently overwritten;
   caller must mint a new `request_id`.
3. **Policy changes between evaluations**: replaying an already-decided
   request returns the original decision regardless of policy drift — a
   retry must never see a different verdict. A new evaluation (new
   `request_id`) picks up the current policy.
4. **Evidence changes**: indistinguishable from case 2 — same conflict,
   same fix.

## 6. Before trusting this in front of an irreversible action

Persistent, crash-safe decision storage instead of an in-process dict;
signed/authenticated requests and actor verification; a real policy
change-approval process; monitoring on DEFER/REFER rates; human sign-off
wired into REFER, not just a string.

## 7. Deliberately not built

Persistent storage, API auth, policy hot-reload, batch evaluation, and a
production WSGI app (`http.server` is illustrative only).
