"""
policy.py
---------
Declarative policy definition for the action-gating service.

This module holds *nothing but data* (plus a version string). Keeping the
policy as data rather than as scattered `if` statements in the evaluator
makes it possible to point at exactly which rule produced a verdict, and
makes "policy changed between two evaluations" a meaningful, detectable
event (see `POLICY_VERSION`).

Bump `POLICY_VERSION` any time a value below changes. The evaluator stamps
every decision with the version that was active when the decision was made.
"""

from __future__ import annotations

POLICY_VERSION = "2026-09-18.1"

# Rule 1 / Rule 6: which (actor role -> action type) combinations are even
# covered by this policy at all. An action type that does not appear here
# is, per rule 6, "not assumed permissible" -- i.e. it is rejected, not
# deferred, because the absence of a grant is itself a complete answer.
ACTION_POLICY = {
    "transfer_funds": {
        "allowed_roles": {"finance_agent"},
        "required_evidence": ("account_balance", "vendor_status", "vendor_status_checked_at"),
    },
}

# Rule 2: transfers strictly greater than this amount are routed to another
# authority (human approver / a higher-privilege agent) rather than decided
# here at all.
REFER_ABOVE_AMOUNT = {
    "transfer_funds": 50_000,
}

# Rule 4: vendor-status evidence older than this many minutes (relative to
# `evaluated_at`) is not sufficient to make a determination.
VENDOR_STATUS_MAX_AGE_MINUTES = 30

# Rule 3: the vendor-status value that counts as "approved".
APPROVED_VENDOR_STATUS = "approved"
