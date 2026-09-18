"""
store.py
--------
In-memory idempotency/replay store for decisions.

Semantics (see README for the full explanation):

  * Same request_id + same canonical payload, replayed any number of
    times -> the *original* decision is returned every time, unchanged,
    even if the policy has since changed. This is what makes "the caller
    retried because it didn't see the first response" safe: a retry can
    never observe a different verdict than the original call would have
    produced.

  * Same request_id + a *different* canonical payload -> treated as a
    caller bug (reusing an id for a new request) and raises
    IdempotencyConflictError rather than silently overwriting or
    silently answering from stale state. The caller must mint a new
    request_id for a genuinely new request/evidence snapshot.

This store is intentionally a plain dict behind a lock-free single-process
model -- swapping it for Redis/Postgres/etc. for multi-process deployment
is a drop-in replacement of this one class (see README "what I didn't
build").
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional


class IdempotencyConflictError(Exception):
    """Same request_id seen before with a materially different payload."""

    def __init__(self, request_id: str):
        super().__init__(
            f"request_id {request_id!r} was already evaluated with a different "
            "payload. Reuse of a request_id for a different request/action/"
            "evidence is not allowed -- mint a new request_id."
        )
        self.request_id = request_id


@dataclass
class StoredDecision:
    payload_fingerprint: str
    decision: Dict[str, Any]


class DecisionStore:
    def __init__(self) -> None:
        self._by_request_id: Dict[str, StoredDecision] = {}
        self._lock = threading.Lock()

    def get_or_record(
        self, request_id: str, payload_fingerprint: str, compute_decision
    ) -> Dict[str, Any]:
        """
        Return the cached decision for `request_id` if the fingerprint
        matches a prior call; otherwise compute (via `compute_decision`,
        a zero-arg callable), record it, and return it.

        Raises IdempotencyConflictError if `request_id` was previously
        recorded with a *different* fingerprint.
        """
        with self._lock:
            existing = self._by_request_id.get(request_id)
            if existing is not None:
                if existing.payload_fingerprint != payload_fingerprint:
                    raise IdempotencyConflictError(request_id)
                return existing.decision

        # Compute outside the lock: policy evaluation is pure/cheap and
        # doesn't need to hold the store lock.
        decision = compute_decision()

        with self._lock:
            existing = self._by_request_id.get(request_id)
            if existing is not None:
                # Lost a race with a concurrent identical/conflicting call.
                if existing.payload_fingerprint != payload_fingerprint:
                    raise IdempotencyConflictError(request_id)
                return existing.decision
            self._by_request_id[request_id] = StoredDecision(
                payload_fingerprint=payload_fingerprint, decision=decision
            )
            return decision

    def peek(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            existing = self._by_request_id.get(request_id)
            return existing.decision if existing else None
