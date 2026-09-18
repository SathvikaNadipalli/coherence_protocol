# Coherence Protocol — Action Gate

Dependency-free policy evaluator: `evaluate(request) -> decision`. See
`policy_gate/evaluator.py`'s docstring for the rule ladder and rationale.
Tests: `python3 -m unittest discover -s tests` (21 tests). Sample
scenarios: `python3 sample_requests.py`. Optional HTTP server: `python3
-m policy_gate.http_app` (POST JSON to `/evaluate`).

## 1. Assumptions
We assumed that account balance is fresh and up to date, so there was 
no time limit added to it unlike the `vendor_status`. I also assumed
that if REFER was only suposed to be down when all other conditions were
met so that the refered party can assume everything else checks out.

## 2. DEFER vs REFER

DEFER: not enough information to legitimately reach ADMIT or HALT —
missing/stale evidence, or a malformed request. It's about evidence
quality. REFER: everything checks out, but the decision is still out of
this service's jurisdiction — the $50,000 threshold routes elsewhere.
DEFER is "ask again with better evidence" while REFER is "ask someone else."

## 3. Rule precedence

The rules are as follows:
Step 1: We check if the request has complete information, if not we DEFER 
Step 2: We check that the action types are covered by the policy, else HALT
Step 3: We check that the finance_agent is doing tranfer_funds, else HALT
Step 4: Make sure sufficient funds, else HALT
Step 5: Check vendor, if not approved HALT, or if not fresh DEFER
Step 6: If amount over $50,000 REFER
Step 7: If none of the above steps get fired, ADMIT



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
