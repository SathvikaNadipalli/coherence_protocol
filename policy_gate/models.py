"""
models.py
---------
Small, dependency-free request/decision shapes plus canonicalization.

Canonicalization matters for two things in this project:
  1. `decision_id` must be deterministic: identical canonical inputs must
     produce the same decision_id (and the same substantive verdict).
  2. Idempotent replay: we need a stable fingerprint of "this exact request"
     that is independent of key ordering / whitespace / JSON encoding so we
     can tell "same request retried" apart from "same request_id, different
     payload".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class MalformedRequestError(Exception):
    """Raised when a request is structurally unusable (not a policy fact)."""


@dataclass(frozen=True)
class ParsedRequest:
    request_id: str
    actor_id: Optional[str]
    actor_role: Optional[str]
    action_type: Optional[str]
    action_fields: Dict[str, Any]
    evidence: Dict[str, Any]
    evaluated_at: Optional[str]
    raw: Dict[str, Any] = field(repr=False)


def canonical_json(payload: Any) -> str:
    """Stable, whitespace- and key-order-independent JSON encoding."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(payload: Any) -> str:
    """Short deterministic fingerprint of an arbitrary JSON-able payload."""
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return digest


def parse_request(request: Dict[str, Any]) -> ParsedRequest:
    """
    Turn a raw request dict into a ParsedRequest, or raise
    MalformedRequestError if it is too broken to even reason about.

    Deliberately lenient about *missing* fields inside action/evidence
    (that's a policy question -> DEFER, handled by the evaluator) but
    strict about the request not being a dict, missing a request_id, or
    the top-level `actor`/`action` sections not being dicts at all --
    those are structural problems, not evidentiary ones.
    """
    if not isinstance(request, dict):
        raise MalformedRequestError("request must be a JSON object")

    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise MalformedRequestError("request_id is required and must be a non-empty string")

    actor = request.get("actor")
    if actor is not None and not isinstance(actor, dict):
        raise MalformedRequestError("actor must be an object if present")
    actor = actor or {}

    action = request.get("action")
    if not isinstance(action, dict):
        raise MalformedRequestError("action is required and must be an object")

    evidence = request.get("evidence")
    if evidence is not None and not isinstance(evidence, dict):
        raise MalformedRequestError("evidence must be an object if present")
    evidence = evidence or {}

    evaluated_at = request.get("evaluated_at")
    if evaluated_at is not None and not isinstance(evaluated_at, str):
        raise MalformedRequestError("evaluated_at must be a string timestamp if present")

    action_type = action.get("type")
    action_fields = {k: v for k, v in action.items() if k != "type"}

    return ParsedRequest(
        request_id=request_id,
        actor_id=actor.get("id"),
        actor_role=actor.get("role"),
        action_type=action_type,
        action_fields=action_fields,
        evidence=evidence,
        evaluated_at=evaluated_at,
        raw=request,
    )
