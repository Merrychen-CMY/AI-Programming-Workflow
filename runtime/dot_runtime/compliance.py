"""Validate candidate-bound clause evidence; this is not a C static analyzer.

The controller owns the catalog, independent-session registry and approval receipts.
Never populate trusted inputs from fields supplied by the compliance report itself.
"""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath


def catalog_digest(catalog: dict) -> str:
    """Stable digest of the complete normative catalog, including adaptations."""
    payload = json.dumps(catalog, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _evidence_errors(items, root, label):
    errors = []
    if not isinstance(items, list) or not items:
        return [f"{label}: actual evidence is required"]
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"{label}: invalid evidence object")
            continue
        name, digest = item.get("path"), item.get("sha256")
        if (not _nonempty(name) or "\\" in name or
                PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive or
                any(part in ("..", ".") for part in name.split("/"))):
            errors.append(f"{label}: evidence path must be a safe relative path")
            continue
        if name in seen:
            errors.append(f"{label}: duplicate evidence path")
        seen.add(name)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"{label}: evidence requires a SHA-256 digest")
            continue
        try:
            path = (root / name).resolve(strict=True)
            path.relative_to(root)
            if not path.is_file():
                raise ValueError("not a file")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except (OSError, ValueError, RuntimeError):
            errors.append(f"{label}: evidence is missing or outside the evidence root")
            continue
        if actual != digest:
            errors.append(f"{label}: evidence SHA-256 mismatch")
    return errors


def validate_compliance(record: dict, catalog: dict, evidence_root: Path,
                        candidate_id: str, implementation_session_id: str,
                        approved_exceptions=None, trusted_verifier_sessions=None) -> list[str]:
    """Return blocking errors, or [] when every catalog clause is accounted for.

    approved_exceptions maps approval IDs to controller-authenticated receipts.
    trusted_verifier_sessions must come from the controller's completed independent
    verification sessions, not from the report. Human review evidence may be an
    explicit record produced by an independently registered review session.
    Existence and hashes establish artifact integrity, not natural-language truth.
    """
    errors = []
    if not isinstance(record, dict) or not isinstance(catalog, dict):
        return ["Compliance record and catalog must be objects"]
    if not _nonempty(candidate_id) or not _nonempty(implementation_session_id):
        return ["Trusted candidate and implementation session are required"]
    try:
        root = Path(evidence_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("not a directory")
    except (OSError, ValueError, TypeError, RuntimeError):
        return ["Evidence root must be an existing directory"]
    if (not isinstance(trusted_verifier_sessions, (set, frozenset, list, tuple)) or
            not trusted_verifier_sessions or
            not all(_nonempty(s) for s in trusted_verifier_sessions)):
        return ["Controller-authenticated independent verifier sessions are required"]
    trusted_sessions = set(trusted_verifier_sessions)
    if implementation_session_id in trusted_sessions:
        errors.append("Implementation session cannot be an independent verifier")
    approvals = {} if approved_exceptions is None else approved_exceptions
    if not isinstance(approvals, dict):
        return errors + ["Trusted exception receipts must be a mapping"]
    clauses = catalog.get("clauses")
    if not isinstance(clauses, list) or len(clauses) != 145:
        return errors + ["Trusted catalog must contain all 145 clauses"]
    expected = {}
    for clause in clauses:
        if (not isinstance(clause, dict) or not _nonempty(clause.get("id")) or
                clause.get("project_level") not in ("MUST", "SHOULD")):
            errors.append("Trusted catalog contains an invalid clause")
            continue
        if clause["id"] in expected:
            errors.append("Trusted catalog contains duplicate clause IDs")
        expected[clause["id"]] = clause
    if errors:
        return errors
    try:
        digest = catalog_digest(catalog)
    except (TypeError, ValueError):
        return ["Trusted catalog cannot be serialized canonically"]
    baseline = {
        "standard_version": catalog.get("standard_version"),
        "coverage_revision": catalog.get("coverage_revision"),
        "catalog_sha256": digest,
        "candidate_id": candidate_id,
        "implementation_session_id": implementation_session_id,
    }
    for key, value in baseline.items():
        if record.get(key) != value or value is None:
            errors.append(f"Compliance baseline mismatch: {key}")
    rows = record.get("clauses")
    if not isinstance(rows, list):
        return errors + ["Compliance clauses must be an array"]
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("Invalid clause result object")
            continue
        identity = row.get("id")
        if not isinstance(identity, str) or identity not in expected:
            errors.append("Unknown or missing clause ID")
            continue
        if identity in seen:
            errors.append(f"{identity}: duplicate result")
            continue
        seen.add(identity)
        applicability = row.get("applicability")
        if applicability not in ("APPLICABLE", "NOT_APPLICABLE"):
            errors.append(f"{identity}: applicability remains unassessed or invalid")
        if not _nonempty(row.get("applicability_reason")):
            errors.append(f"{identity}: applicability requires a scope-specific reason")
        verifier = row.get("verifier_session_id")
        if (not _nonempty(verifier) or verifier == implementation_session_id or
                verifier not in trusted_sessions):
            errors.append(f"{identity}: verifier must be a trusted independent session")
        if not _nonempty(row.get("verification_method")):
            errors.append(f"{identity}: verification method is required")
        errors.extend(_evidence_errors(row.get("evidence"), root, identity))
        result = row.get("result")
        if applicability == "NOT_APPLICABLE":
            if result != "NOT_APPLICABLE":
                errors.append(f"{identity}: non-applicability must not be reported as PASS")
            if row.get("exception_approval_id") is not None:
                errors.append(f"{identity}: an exception is not non-applicability")
            continue
        if applicability != "APPLICABLE":
            continue
        level = expected[identity]["project_level"]
        if result == "PASS":
            if row.get("exception_approval_id") is not None:
                errors.append(f"{identity}: waived violations must be EXCEPTION, not PASS")
            continue
        if result == "DEVIATION" and level == "SHOULD":
            if not _nonempty(row.get("reason")):
                errors.append(f"{identity}: assessed recommendation deviation needs a reason")
            if row.get("exception_approval_id") is not None:
                errors.append(f"{identity}: use EXCEPTION when invoking an approval")
            continue
        if result != "EXCEPTION":
            errors.append(f"{identity}: {result!r} cannot satisfy applicable {level}")
            continue
        approval_id = row.get("exception_approval_id")
        receipt = approvals.get(approval_id) if isinstance(approval_id, str) else None
        if not isinstance(receipt, dict):
            errors.append(f"{identity}: exception needs a controller-authenticated approval")
            continue
        if not _nonempty(row.get("reason")):
            errors.append(f"{identity}: exception rationale is required")
        required_receipt = {
            "decision": "APPROVED", "approval_id": approval_id, "clause_id": identity,
            "candidate_id": candidate_id, "standard_version": baseline["standard_version"],
            "coverage_revision": baseline["coverage_revision"], "catalog_sha256": digest,
        }
        for key, value in required_receipt.items():
            if receipt.get(key) != value:
                errors.append(f"{identity}: approved exception binding mismatch: {key}")
        if not _nonempty(receipt.get("approved_by")):
            errors.append(f"{identity}: approved exception needs an authenticated approver")
        if receipt.get("approved_by") == implementation_session_id:
            errors.append(f"{identity}: implementer cannot approve their own exception")
    for identity in sorted(set(expected) - seen):
        errors.append(f"{identity}: missing clause applicability/result")
    return errors
