"""Durable, blind, two-reviewer dataset quality authority.

The authority never scores or rewrites content. It owns only identity-bound
review decisions and immutable promotion evidence. Dataset construction stays
in ``production_dataset`` as the single promotion path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from src.aeitron.shared.schemas import StrictModel


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVIEW_STATUSES = {
    "pending",
    "in_review",
    "approved",
    "rejected",
    "conflict",
    "adjudication_required",
}
SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS data_source_snapshots (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_family TEXT NOT NULL,
  immutable_revision TEXT NOT NULL,
  registry_sha256 TEXT NOT NULL,
  license_evidence_sha256 TEXT NOT NULL,
  legal_approval_sha256 TEXT NOT NULL,
  snapshot_sha256 TEXT NOT NULL,
  status TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(source_id, immutable_revision, snapshot_sha256)
);
CREATE TABLE IF NOT EXISTS dataset_review_items (
  id TEXT PRIMARY KEY,
  content_hash TEXT NOT NULL,
  source_snapshot_sha256 TEXT NOT NULL,
  source_id TEXT NOT NULL,
  data_type TEXT NOT NULL,
  high_value INTEGER NOT NULL,
  status TEXT NOT NULL,
  version INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  UNIQUE(content_hash, source_snapshot_sha256)
);
CREATE TABLE IF NOT EXISTS dataset_review_assignments (
  id TEXT PRIMARY KEY,
  review_item_id TEXT NOT NULL REFERENCES dataset_review_items(id) ON DELETE CASCADE,
  reviewer_id TEXT NOT NULL,
  reviewer_slot INTEGER NOT NULL CHECK(reviewer_slot IN (1,2)),
  status TEXT NOT NULL,
  claimed_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  UNIQUE(review_item_id, reviewer_id),
  UNIQUE(review_item_id, reviewer_slot)
);
CREATE TABLE IF NOT EXISTS dataset_review_decisions (
  id TEXT PRIMARY KEY,
  review_item_id TEXT NOT NULL REFERENCES dataset_review_items(id) ON DELETE CASCADE,
  reviewer_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
  rationale TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  source_snapshot_sha256 TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(review_item_id, reviewer_id)
);
CREATE TABLE IF NOT EXISTS dataset_review_adjudications (
  id TEXT PRIMARY KEY,
  review_item_id TEXT NOT NULL UNIQUE REFERENCES dataset_review_items(id) ON DELETE CASCADE,
  adjudicator_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),
  rationale TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dataset_promotion_events (
  id TEXT PRIMARY KEY,
  dataset_id TEXT NOT NULL,
  version_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  policy_sha256 TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('promoted','rejected')),
  evidence_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(dataset_id,version_id,manifest_sha256)
);
CREATE INDEX IF NOT EXISTS idx_dataset_review_status ON dataset_review_items(status,created_at);
CREATE INDEX IF NOT EXISTS idx_dataset_review_source ON dataset_review_items(source_id,data_type,status);
"""


def _validate_hash(value: str, name: str) -> str:
    normalized = value.strip().lower()
    if SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{name} must be SHA-256 hex")
    return normalized


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ReviewItemCreate(StrictModel):
    content_hash: str
    source_snapshot_sha256: str
    source_id: str = Field(min_length=2, max_length=128)
    data_type: str = Field(min_length=2, max_length=80)
    high_value: bool
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_contract(self) -> "ReviewItemCreate":
        object.__setattr__(self, "content_hash", _validate_hash(self.content_hash, "content_hash"))
        object.__setattr__(
            self,
            "source_snapshot_sha256",
            _validate_hash(self.source_snapshot_sha256, "source_snapshot_sha256"),
        )
        return self


class SourceSnapshotCreate(StrictModel):
    source_id: str = Field(min_length=2, max_length=128)
    source_family: str = Field(min_length=2, max_length=128)
    immutable_revision: str = Field(min_length=1, max_length=256)
    registry_sha256: str
    license_evidence_sha256: str
    legal_approval_sha256: str
    snapshot_sha256: str
    status: Literal["quarantine", "approved", "revoked"]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_contract(self) -> "SourceSnapshotCreate":
        if self.immutable_revision == "rolling":
            raise ValueError("source snapshot revision must be immutable")
        for name in (
            "registry_sha256",
            "license_evidence_sha256",
            "legal_approval_sha256",
            "snapshot_sha256",
        ):
            object.__setattr__(self, name, _validate_hash(getattr(self, name), name))
        return self


class SourceSnapshotView(SourceSnapshotCreate):
    snapshot_id: str
    created_at: float


class ReviewDecisionCreate(StrictModel):
    decision: Literal["approve", "reject"]
    rationale: str = Field(min_length=20, max_length=4_000)
    content_hash: str
    source_snapshot_sha256: str
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_contract(self) -> "ReviewDecisionCreate":
        object.__setattr__(self, "content_hash", _validate_hash(self.content_hash, "content_hash"))
        object.__setattr__(
            self,
            "source_snapshot_sha256",
            _validate_hash(self.source_snapshot_sha256, "source_snapshot_sha256"),
        )
        return self


class ReviewAdjudicationCreate(StrictModel):
    decision: Literal["approve", "reject"]
    rationale: str = Field(min_length=30, max_length=4_000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ReviewAssignment(StrictModel):
    assignment_id: str
    review_item_id: str
    reviewer_id: str
    reviewer_slot: int = Field(ge=1, le=2)
    status: str
    claimed_at: float
    expires_at: float


class ReviewDecisionView(StrictModel):
    reviewer_id: str
    decision: Literal["approve", "reject"]
    rationale: str
    evidence: dict[str, Any]
    created_at: float


class ReviewItemView(StrictModel):
    review_item_id: str
    content_hash: str
    source_snapshot_sha256: str
    source_id: str
    data_type: str
    high_value: bool
    status: str
    version: int
    payload: dict[str, Any]
    assignments: list[ReviewAssignment] = Field(default_factory=list)
    decisions: list[ReviewDecisionView] = Field(default_factory=list)
    created_at: float
    updated_at: float


class PromotionEvidenceCreate(StrictModel):
    dataset_id: str = Field(min_length=2, max_length=160)
    version_id: str = Field(min_length=2, max_length=160)
    manifest_sha256: str
    policy_sha256: str
    decision: Literal["promoted", "rejected"]
    evidence: dict[str, Any]

    @model_validator(mode="after")
    def validate_contract(self) -> "PromotionEvidenceCreate":
        object.__setattr__(self, "manifest_sha256", _validate_hash(self.manifest_sha256, "manifest_sha256"))
        object.__setattr__(self, "policy_sha256", _validate_hash(self.policy_sha256, "policy_sha256"))
        return self


class PromotionEvidenceView(PromotionEvidenceCreate):
    promotion_id: str
    actor_id: str
    created_at: float


class SourceReviewEvidence(StrictModel):
    approved: int = Field(ge=0)
    rejected: int = Field(ge=0)
    pending: int = Field(ge=0)
    paired_reviews: int = Field(ge=0)
    reviewer_agreement_kappa: float = Field(ge=-1.0, le=1.0)
    acceptance_rate: float = Field(ge=0.0, le=1.0)


class ReviewEvidenceReport(StrictModel):
    by_source: dict[str, SourceReviewEvidence]
    created_at_unix: float = Field(default_factory=time.time)


class DatasetAuthorityStore(Protocol):
    async def record_source_snapshot(self, request: SourceSnapshotCreate) -> SourceSnapshotView: ...
    async def enqueue(self, request: ReviewItemCreate) -> ReviewItemView: ...
    async def list_items(self, *, status: str | None, limit: int, reviewer_id: str | None) -> list[ReviewItemView]: ...
    async def get_item(self, review_item_id: str, *, reviewer_id: str | None) -> ReviewItemView: ...
    async def claim(self, review_item_id: str, reviewer_id: str, lease_seconds: int) -> ReviewAssignment: ...
    async def decide(self, review_item_id: str, reviewer_id: str, request: ReviewDecisionCreate) -> ReviewItemView: ...
    async def adjudicate(self, review_item_id: str, adjudicator_id: str, request: ReviewAdjudicationCreate) -> ReviewItemView: ...
    async def record_promotion(self, actor_id: str, request: PromotionEvidenceCreate) -> PromotionEvidenceView: ...
    async def review_evidence(self) -> ReviewEvidenceReport: ...


def _terminal_or_visible(status: str, reviewer_id: str | None, decision_reviewer: str) -> bool:
    return status in {"approved", "rejected", "conflict", "adjudication_required"} or reviewer_id == decision_reviewer


def _status_from_decisions(high_value: bool, decisions: list[str]) -> str:
    required = 2 if high_value else 1
    if len(decisions) < required:
        return "in_review"
    unique = set(decisions)
    if len(unique) > 1:
        return "adjudication_required"
    return "approved" if decisions[0] == "approve" else "rejected"


def _cohen_kappa(pairs: list[tuple[str, str]]) -> float:
    if not pairs:
        return 0.0
    observed = sum(left == right for left, right in pairs) / len(pairs)
    first_approve = sum(left == "approve" for left, _ in pairs) / len(pairs)
    second_approve = sum(right == "approve" for _, right in pairs) / len(pairs)
    expected = first_approve * second_approve + (1 - first_approve) * (1 - second_approve)
    return 1.0 if expected >= 1.0 else max(-1.0, min(1.0, (observed - expected) / (1 - expected)))


def _review_evidence_from_rows(items: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> ReviewEvidenceReport:
    decisions_by_item: dict[str, list[dict[str, Any]]] = {}
    for decision in decisions:
        decisions_by_item.setdefault(str(decision["review_item_id"]), []).append(decision)
    accumulators: dict[str, dict[str, Any]] = {}
    for item in items:
        source = str(item["source_id"])
        bucket = accumulators.setdefault(
            source,
            {"approved": 0, "rejected": 0, "pending": 0, "pairs": []},
        )
        status = str(item["status"])
        if status == "approved":
            bucket["approved"] += 1
        elif status == "rejected":
            bucket["rejected"] += 1
        else:
            bucket["pending"] += 1
        item_decisions = sorted(
            decisions_by_item.get(str(item["id"]), []),
            key=lambda row: float(row["created_at"]),
        )
        if len(item_decisions) >= 2:
            bucket["pairs"].append(
                (str(item_decisions[0]["decision"]), str(item_decisions[1]["decision"]))
            )
    by_source: dict[str, SourceReviewEvidence] = {}
    for source, bucket in sorted(accumulators.items()):
        total_terminal = int(bucket["approved"]) + int(bucket["rejected"])
        pairs = list(bucket["pairs"])
        by_source[source] = SourceReviewEvidence(
            approved=int(bucket["approved"]),
            rejected=int(bucket["rejected"]),
            pending=int(bucket["pending"]),
            paired_reviews=len(pairs),
            reviewer_agreement_kappa=round(_cohen_kappa(pairs), 6),
            acceptance_rate=round(int(bucket["approved"]) / max(1, total_terminal), 6),
        )
    return ReviewEvidenceReport(by_source=by_source)


class SQLiteDatasetAuthorityStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with closing(self._connect()) as connection:
            connection.executescript(SQLITE_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    async def record_source_snapshot(self, request: SourceSnapshotCreate) -> SourceSnapshotView:
        def operation() -> SourceSnapshotView:
            with self.lock, closing(self._connect()) as connection:
                now = time.time()
                snapshot_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT OR IGNORE INTO data_source_snapshots(
                      id,source_id,source_family,immutable_revision,registry_sha256,
                      license_evidence_sha256,legal_approval_sha256,snapshot_sha256,
                      status,metadata_json,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        snapshot_id,
                        request.source_id,
                        request.source_family,
                        request.immutable_revision,
                        request.registry_sha256,
                        request.license_evidence_sha256,
                        request.legal_approval_sha256,
                        request.snapshot_sha256,
                        request.status,
                        _json(request.metadata),
                        now,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT * FROM data_source_snapshots
                    WHERE source_id=? AND immutable_revision=? AND snapshot_sha256=?
                    """,
                    (request.source_id, request.immutable_revision, request.snapshot_sha256),
                ).fetchone()
                assert row is not None
                connection.commit()
                return SourceSnapshotView(
                    snapshot_id=row["id"],
                    source_id=row["source_id"],
                    source_family=row["source_family"],
                    immutable_revision=row["immutable_revision"],
                    registry_sha256=row["registry_sha256"],
                    license_evidence_sha256=row["license_evidence_sha256"],
                    legal_approval_sha256=row["legal_approval_sha256"],
                    snapshot_sha256=row["snapshot_sha256"],
                    status=row["status"],
                    metadata=json.loads(row["metadata_json"]),
                    created_at=float(row["created_at"]),
                )

        return await asyncio.to_thread(operation)

    def _view(self, connection: sqlite3.Connection, review_item_id: str, reviewer_id: str | None) -> ReviewItemView:
        row = connection.execute("SELECT * FROM dataset_review_items WHERE id=?", (review_item_id,)).fetchone()
        if row is None:
            raise KeyError(review_item_id)
        assignments = [
            ReviewAssignment(
                assignment_id=item["id"],
                review_item_id=item["review_item_id"],
                reviewer_id=item["reviewer_id"],
                reviewer_slot=int(item["reviewer_slot"]),
                status=item["status"],
                claimed_at=float(item["claimed_at"]),
                expires_at=float(item["expires_at"]),
            )
            for item in connection.execute(
                "SELECT * FROM dataset_review_assignments WHERE review_item_id=? ORDER BY reviewer_slot",
                (review_item_id,),
            )
        ]
        decisions = [
            ReviewDecisionView(
                reviewer_id=item["reviewer_id"],
                decision=item["decision"],
                rationale=item["rationale"],
                evidence=json.loads(item["evidence_json"]),
                created_at=float(item["created_at"]),
            )
            for item in connection.execute(
                "SELECT * FROM dataset_review_decisions WHERE review_item_id=? ORDER BY created_at",
                (review_item_id,),
            )
            if _terminal_or_visible(row["status"], reviewer_id, item["reviewer_id"])
        ]
        return ReviewItemView(
            review_item_id=row["id"],
            content_hash=row["content_hash"],
            source_snapshot_sha256=row["source_snapshot_sha256"],
            source_id=row["source_id"],
            data_type=row["data_type"],
            high_value=bool(row["high_value"]),
            status=row["status"],
            version=int(row["version"]),
            payload=json.loads(row["payload_json"]),
            assignments=assignments,
            decisions=decisions,
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    async def enqueue(self, request: ReviewItemCreate) -> ReviewItemView:
        def operation() -> ReviewItemView:
            with self.lock, closing(self._connect()) as connection:
                now = time.time()
                review_item_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT OR IGNORE INTO dataset_review_items(
                      id,content_hash,source_snapshot_sha256,source_id,data_type,high_value,
                      status,version,payload_json,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,1,?,?,?)
                    """,
                    (
                        review_item_id,
                        request.content_hash,
                        request.source_snapshot_sha256,
                        request.source_id,
                        request.data_type,
                        int(request.high_value),
                        "pending",
                        _json(request.payload),
                        now,
                        now,
                    ),
                )
                existing = connection.execute(
                    "SELECT id FROM dataset_review_items WHERE content_hash=? AND source_snapshot_sha256=?",
                    (request.content_hash, request.source_snapshot_sha256),
                ).fetchone()
                assert existing is not None
                connection.commit()
                return self._view(connection, existing["id"], None)

        return await asyncio.to_thread(operation)

    async def list_items(self, *, status: str | None, limit: int, reviewer_id: str | None) -> list[ReviewItemView]:
        if status is not None and status not in REVIEW_STATUSES:
            raise ValueError("invalid review status")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        def operation() -> list[ReviewItemView]:
            with self.lock, closing(self._connect()) as connection:
                if status:
                    rows = connection.execute(
                        "SELECT id FROM dataset_review_items WHERE status=? ORDER BY created_at LIMIT ?",
                        (status, limit),
                    )
                else:
                    rows = connection.execute("SELECT id FROM dataset_review_items ORDER BY created_at LIMIT ?", (limit,))
                return [self._view(connection, row["id"], reviewer_id) for row in rows]

        return await asyncio.to_thread(operation)

    async def get_item(self, review_item_id: str, *, reviewer_id: str | None) -> ReviewItemView:
        return await asyncio.to_thread(self._get_sync, review_item_id, reviewer_id)

    def _get_sync(self, review_item_id: str, reviewer_id: str | None) -> ReviewItemView:
        with self.lock, closing(self._connect()) as connection:
            return self._view(connection, review_item_id, reviewer_id)

    async def claim(self, review_item_id: str, reviewer_id: str, lease_seconds: int) -> ReviewAssignment:
        if not reviewer_id.strip():
            raise ValueError("reviewer_id cannot be empty")
        if not 60 <= lease_seconds <= 86_400:
            raise ValueError("review lease must be between 60 and 86400 seconds")

        def operation() -> ReviewAssignment:
            with self.lock, closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                now = time.time()
                connection.execute(
                    "DELETE FROM dataset_review_assignments WHERE status='claimed' AND expires_at<?",
                    (now,),
                )
                item = connection.execute("SELECT * FROM dataset_review_items WHERE id=?", (review_item_id,)).fetchone()
                if item is None:
                    raise KeyError(review_item_id)
                if item["status"] in {"approved", "rejected"}:
                    raise ValueError("terminal review item cannot be claimed")
                duplicate = connection.execute(
                    "SELECT 1 FROM dataset_review_assignments WHERE review_item_id=? AND reviewer_id=?",
                    (review_item_id, reviewer_id),
                ).fetchone()
                if duplicate:
                    raise PermissionError("same reviewer cannot occupy both review slots")
                required = 2 if bool(item["high_value"]) else 1
                occupied = {
                    int(row["reviewer_slot"])
                    for row in connection.execute(
                        "SELECT reviewer_slot FROM dataset_review_assignments WHERE review_item_id=?",
                        (review_item_id,),
                    )
                }
                slot = next((candidate for candidate in range(1, required + 1) if candidate not in occupied), None)
                if slot is None:
                    raise ValueError("all review slots are already claimed")
                assignment_id = str(uuid.uuid4())
                expires_at = now + lease_seconds
                connection.execute(
                    """
                    INSERT INTO dataset_review_assignments(
                      id,review_item_id,reviewer_id,reviewer_slot,status,claimed_at,expires_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (assignment_id, review_item_id, reviewer_id, slot, "claimed", now, expires_at),
                )
                connection.execute(
                    "UPDATE dataset_review_items SET status='in_review',version=version+1,updated_at=? WHERE id=?",
                    (now, review_item_id),
                )
                connection.commit()
                return ReviewAssignment(
                    assignment_id=assignment_id,
                    review_item_id=review_item_id,
                    reviewer_id=reviewer_id,
                    reviewer_slot=slot,
                    status="claimed",
                    claimed_at=now,
                    expires_at=expires_at,
                )

        return await asyncio.to_thread(operation)

    async def decide(self, review_item_id: str, reviewer_id: str, request: ReviewDecisionCreate) -> ReviewItemView:
        def operation() -> ReviewItemView:
            with self.lock, closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                now = time.time()
                item = connection.execute("SELECT * FROM dataset_review_items WHERE id=?", (review_item_id,)).fetchone()
                if item is None:
                    raise KeyError(review_item_id)
                if item["content_hash"] != request.content_hash or item["source_snapshot_sha256"] != request.source_snapshot_sha256:
                    raise ValueError("review evidence no longer matches content/source snapshot")
                assignment = connection.execute(
                    """
                    SELECT * FROM dataset_review_assignments
                    WHERE review_item_id=? AND reviewer_id=? AND status='claimed'
                    """,
                    (review_item_id, reviewer_id),
                ).fetchone()
                if assignment is None:
                    raise PermissionError("reviewer does not hold this review item")
                if float(assignment["expires_at"]) < now:
                    raise PermissionError("review assignment has expired")
                connection.execute(
                    """
                    INSERT INTO dataset_review_decisions(
                      id,review_item_id,reviewer_id,decision,rationale,content_hash,
                      source_snapshot_sha256,evidence_json,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid.uuid4()),
                        review_item_id,
                        reviewer_id,
                        request.decision,
                        request.rationale,
                        request.content_hash,
                        request.source_snapshot_sha256,
                        _json(request.evidence),
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE dataset_review_assignments SET status='submitted' WHERE id=?",
                    (assignment["id"],),
                )
                decisions = [
                    row["decision"]
                    for row in connection.execute(
                        "SELECT decision FROM dataset_review_decisions WHERE review_item_id=? ORDER BY created_at",
                        (review_item_id,),
                    )
                ]
                status = _status_from_decisions(bool(item["high_value"]), decisions)
                connection.execute(
                    "UPDATE dataset_review_items SET status=?,version=version+1,updated_at=? WHERE id=?",
                    (status, now, review_item_id),
                )
                connection.commit()
                return self._view(connection, review_item_id, reviewer_id)

        return await asyncio.to_thread(operation)

    async def adjudicate(self, review_item_id: str, adjudicator_id: str, request: ReviewAdjudicationCreate) -> ReviewItemView:
        def operation() -> ReviewItemView:
            with self.lock, closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                item = connection.execute("SELECT * FROM dataset_review_items WHERE id=?", (review_item_id,)).fetchone()
                if item is None:
                    raise KeyError(review_item_id)
                if item["status"] not in {"conflict", "adjudication_required"}:
                    raise ValueError("only conflicting reviews can be adjudicated")
                reviewer = connection.execute(
                    "SELECT 1 FROM dataset_review_decisions WHERE review_item_id=? AND reviewer_id=?",
                    (review_item_id, adjudicator_id),
                ).fetchone()
                if reviewer:
                    raise PermissionError("a reviewer cannot adjudicate the same record")
                now = time.time()
                connection.execute(
                    """
                    INSERT INTO dataset_review_adjudications(
                      id,review_item_id,adjudicator_id,decision,rationale,evidence_json,created_at
                    ) VALUES (?,?,?,?,?,?,?)
                    """,
                    (str(uuid.uuid4()), review_item_id, adjudicator_id, request.decision, request.rationale, _json(request.evidence), now),
                )
                status = "approved" if request.decision == "approve" else "rejected"
                connection.execute(
                    "UPDATE dataset_review_items SET status=?,version=version+1,updated_at=? WHERE id=?",
                    (status, now, review_item_id),
                )
                connection.commit()
                return self._view(connection, review_item_id, adjudicator_id)

        return await asyncio.to_thread(operation)

    async def record_promotion(self, actor_id: str, request: PromotionEvidenceCreate) -> PromotionEvidenceView:
        def operation() -> PromotionEvidenceView:
            with self.lock, closing(self._connect()) as connection:
                now = time.time()
                promotion_id = str(uuid.uuid4())
                connection.execute(
                    """
                    INSERT OR IGNORE INTO dataset_promotion_events(
                      id,dataset_id,version_id,actor_id,manifest_sha256,policy_sha256,
                      decision,evidence_json,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        promotion_id,
                        request.dataset_id,
                        request.version_id,
                        actor_id,
                        request.manifest_sha256,
                        request.policy_sha256,
                        request.decision,
                        _json(request.evidence),
                        now,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT * FROM dataset_promotion_events
                    WHERE dataset_id=? AND version_id=? AND manifest_sha256=?
                    """,
                    (request.dataset_id, request.version_id, request.manifest_sha256),
                ).fetchone()
                assert row is not None
                connection.commit()
                return PromotionEvidenceView(
                    promotion_id=row["id"],
                    dataset_id=row["dataset_id"],
                    version_id=row["version_id"],
                    actor_id=row["actor_id"],
                    manifest_sha256=row["manifest_sha256"],
                    policy_sha256=row["policy_sha256"],
                    decision=row["decision"],
                    evidence=json.loads(row["evidence_json"]),
                    created_at=float(row["created_at"]),
                )

        return await asyncio.to_thread(operation)

    async def review_evidence(self) -> ReviewEvidenceReport:
        def operation() -> ReviewEvidenceReport:
            with self.lock, closing(self._connect()) as connection:
                items = [dict(row) for row in connection.execute("SELECT id,source_id,status FROM dataset_review_items")]
                decisions = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT review_item_id,decision,created_at FROM dataset_review_decisions ORDER BY created_at"
                    )
                ]
                return _review_evidence_from_rows(items, decisions)

        return await asyncio.to_thread(operation)


class PostgresDatasetAuthorityStore:
    """Async Postgres implementation using migration ``0006_dataset_trust``."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: Any = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> Any:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    import asyncpg

                    self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10, command_timeout=30)
        return self._pool

    async def record_source_snapshot(self, request: SourceSnapshotCreate) -> SourceSnapshotView:
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO data_source_snapshots(
                  id,source_id,source_family,immutable_revision,registry_sha256,
                  license_evidence_sha256,legal_approval_sha256,snapshot_sha256,status,metadata
                ) VALUES ($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                ON CONFLICT(source_id,immutable_revision,snapshot_sha256)
                DO UPDATE SET snapshot_sha256=data_source_snapshots.snapshot_sha256
                RETURNING *
                """,
                str(uuid.uuid4()),
                request.source_id,
                request.source_family,
                request.immutable_revision,
                request.registry_sha256,
                request.license_evidence_sha256,
                request.legal_approval_sha256,
                request.snapshot_sha256,
                request.status,
                _json(request.metadata),
            )
            return SourceSnapshotView(
                snapshot_id=str(row["id"]),
                source_id=row["source_id"],
                source_family=row["source_family"],
                immutable_revision=row["immutable_revision"],
                registry_sha256=row["registry_sha256"],
                license_evidence_sha256=row["license_evidence_sha256"],
                legal_approval_sha256=row["legal_approval_sha256"],
                snapshot_sha256=row["snapshot_sha256"],
                status=row["status"],
                metadata=dict(row["metadata"]),
                created_at=row["created_at"].timestamp(),
            )

    async def review_evidence(self) -> ReviewEvidenceReport:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            item_rows = await connection.fetch("SELECT id,source_id,status FROM dataset_review_items")
            decision_rows = await connection.fetch(
                "SELECT review_item_id,decision,created_at FROM dataset_review_decisions ORDER BY created_at"
            )
        items = [
            {"id": str(row["id"]), "source_id": row["source_id"], "status": row["status"]}
            for row in item_rows
        ]
        decisions = [
            {
                "review_item_id": str(row["review_item_id"]),
                "decision": row["decision"],
                "created_at": row["created_at"].timestamp(),
            }
            for row in decision_rows
        ]
        return _review_evidence_from_rows(items, decisions)

    @staticmethod
    def _view_from_rows(item: Any, assignments: list[Any], decisions: list[Any], reviewer_id: str | None) -> ReviewItemView:
        visible = [
            ReviewDecisionView(
                reviewer_id=row["reviewer_id"],
                decision=row["decision"],
                rationale=row["rationale"],
                evidence=dict(row["evidence"]),
                created_at=row["created_at"].timestamp(),
            )
            for row in decisions
            if _terminal_or_visible(item["status"], reviewer_id, row["reviewer_id"])
        ]
        return ReviewItemView(
            review_item_id=str(item["id"]),
            content_hash=item["content_hash"],
            source_snapshot_sha256=item["source_snapshot_sha256"],
            source_id=item["source_id"],
            data_type=item["data_type"],
            high_value=item["high_value"],
            status=item["status"],
            version=item["version"],
            payload=dict(item["payload"]),
            assignments=[
                ReviewAssignment(
                    assignment_id=str(row["id"]),
                    review_item_id=str(row["review_item_id"]),
                    reviewer_id=row["reviewer_id"],
                    reviewer_slot=row["reviewer_slot"],
                    status=row["status"],
                    claimed_at=row["claimed_at"].timestamp(),
                    expires_at=row["expires_at"].timestamp(),
                )
                for row in assignments
            ],
            decisions=visible,
            created_at=item["created_at"].timestamp(),
            updated_at=item["updated_at"].timestamp(),
        )

    async def _view(self, connection: Any, review_item_id: str, reviewer_id: str | None) -> ReviewItemView:
        item = await connection.fetchrow("SELECT * FROM dataset_review_items WHERE id=$1::uuid", review_item_id)
        if item is None:
            raise KeyError(review_item_id)
        assignments = await connection.fetch(
            "SELECT * FROM dataset_review_assignments WHERE review_item_id=$1::uuid ORDER BY reviewer_slot",
            review_item_id,
        )
        decisions = await connection.fetch(
            "SELECT * FROM dataset_review_decisions WHERE review_item_id=$1::uuid ORDER BY created_at",
            review_item_id,
        )
        return self._view_from_rows(item, list(assignments), list(decisions), reviewer_id)

    async def enqueue(self, request: ReviewItemCreate) -> ReviewItemView:
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO dataset_review_items(
                  id,content_hash,source_snapshot_sha256,source_id,data_type,high_value,status,payload
                ) VALUES ($1::uuid,$2,$3,$4,$5,$6,'pending',$7::jsonb)
                ON CONFLICT(content_hash,source_snapshot_sha256)
                DO UPDATE SET updated_at=dataset_review_items.updated_at
                RETURNING id
                """,
                str(uuid.uuid4()),
                request.content_hash,
                request.source_snapshot_sha256,
                request.source_id,
                request.data_type,
                request.high_value,
                _json(request.payload),
            )
            return await self._view(connection, str(row["id"]), None)

    async def list_items(self, *, status: str | None, limit: int, reviewer_id: str | None) -> list[ReviewItemView]:
        if status is not None and status not in REVIEW_STATUSES:
            raise ValueError("invalid review status")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            if status:
                rows = await connection.fetch(
                    "SELECT id FROM dataset_review_items WHERE status=$1 ORDER BY created_at LIMIT $2",
                    status,
                    limit,
                )
            else:
                rows = await connection.fetch("SELECT id FROM dataset_review_items ORDER BY created_at LIMIT $1", limit)
            return [await self._view(connection, str(row["id"]), reviewer_id) for row in rows]

    async def get_item(self, review_item_id: str, *, reviewer_id: str | None) -> ReviewItemView:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            return await self._view(connection, review_item_id, reviewer_id)

    async def claim(self, review_item_id: str, reviewer_id: str, lease_seconds: int) -> ReviewAssignment:
        if not 60 <= lease_seconds <= 86_400:
            raise ValueError("review lease must be between 60 and 86400 seconds")
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            item = await connection.fetchrow(
                "SELECT * FROM dataset_review_items WHERE id=$1::uuid FOR UPDATE",
                review_item_id,
            )
            if item is None:
                raise KeyError(review_item_id)
            await connection.execute(
                "DELETE FROM dataset_review_assignments WHERE review_item_id=$1::uuid AND status='claimed' AND expires_at<now()",
                review_item_id,
            )
            if await connection.fetchval(
                "SELECT 1 FROM dataset_review_assignments WHERE review_item_id=$1::uuid AND reviewer_id=$2",
                review_item_id,
                reviewer_id,
            ):
                raise PermissionError("same reviewer cannot occupy both review slots")
            required = 2 if item["high_value"] else 1
            occupied = {
                row["reviewer_slot"]
                for row in await connection.fetch(
                    "SELECT reviewer_slot FROM dataset_review_assignments WHERE review_item_id=$1::uuid",
                    review_item_id,
                )
            }
            slot = next((candidate for candidate in range(1, required + 1) if candidate not in occupied), None)
            if slot is None:
                raise ValueError("all review slots are already claimed")
            assignment_id = str(uuid.uuid4())
            row = await connection.fetchrow(
                """
                INSERT INTO dataset_review_assignments(
                  id,review_item_id,reviewer_id,reviewer_slot,status,expires_at
                ) VALUES ($1::uuid,$2::uuid,$3,$4,'claimed',now()+($5 || ' seconds')::interval)
                RETURNING *
                """,
                assignment_id,
                review_item_id,
                reviewer_id,
                slot,
                str(lease_seconds),
            )
            await connection.execute(
                "UPDATE dataset_review_items SET status='in_review',version=version+1,updated_at=now() WHERE id=$1::uuid",
                review_item_id,
            )
            return ReviewAssignment(
                assignment_id=str(row["id"]),
                review_item_id=str(row["review_item_id"]),
                reviewer_id=row["reviewer_id"],
                reviewer_slot=row["reviewer_slot"],
                status=row["status"],
                claimed_at=row["claimed_at"].timestamp(),
                expires_at=row["expires_at"].timestamp(),
            )

    async def decide(self, review_item_id: str, reviewer_id: str, request: ReviewDecisionCreate) -> ReviewItemView:
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            item = await connection.fetchrow(
                "SELECT * FROM dataset_review_items WHERE id=$1::uuid FOR UPDATE",
                review_item_id,
            )
            if item is None:
                raise KeyError(review_item_id)
            if item["content_hash"] != request.content_hash or item["source_snapshot_sha256"] != request.source_snapshot_sha256:
                raise ValueError("review evidence no longer matches content/source snapshot")
            assignment = await connection.fetchrow(
                """
                SELECT * FROM dataset_review_assignments
                WHERE review_item_id=$1::uuid AND reviewer_id=$2 AND status='claimed' AND expires_at>=now()
                """,
                review_item_id,
                reviewer_id,
            )
            if assignment is None:
                raise PermissionError("reviewer does not hold an active assignment")
            await connection.execute(
                """
                INSERT INTO dataset_review_decisions(
                  id,review_item_id,reviewer_id,decision,rationale,content_hash,
                  source_snapshot_sha256,evidence
                ) VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8::jsonb)
                """,
                str(uuid.uuid4()),
                review_item_id,
                reviewer_id,
                request.decision,
                request.rationale,
                request.content_hash,
                request.source_snapshot_sha256,
                _json(request.evidence),
            )
            await connection.execute(
                "UPDATE dataset_review_assignments SET status='submitted' WHERE id=$1::uuid",
                str(assignment["id"]),
            )
            decisions = [
                row["decision"]
                for row in await connection.fetch(
                    "SELECT decision FROM dataset_review_decisions WHERE review_item_id=$1::uuid ORDER BY created_at",
                    review_item_id,
                )
            ]
            status = _status_from_decisions(item["high_value"], decisions)
            await connection.execute(
                "UPDATE dataset_review_items SET status=$1,version=version+1,updated_at=now() WHERE id=$2::uuid",
                status,
                review_item_id,
            )
            return await self._view(connection, review_item_id, reviewer_id)

    async def adjudicate(self, review_item_id: str, adjudicator_id: str, request: ReviewAdjudicationCreate) -> ReviewItemView:
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            item = await connection.fetchrow(
                "SELECT * FROM dataset_review_items WHERE id=$1::uuid FOR UPDATE",
                review_item_id,
            )
            if item is None:
                raise KeyError(review_item_id)
            if item["status"] not in {"conflict", "adjudication_required"}:
                raise ValueError("only conflicting reviews can be adjudicated")
            if await connection.fetchval(
                "SELECT 1 FROM dataset_review_decisions WHERE review_item_id=$1::uuid AND reviewer_id=$2",
                review_item_id,
                adjudicator_id,
            ):
                raise PermissionError("a reviewer cannot adjudicate the same record")
            await connection.execute(
                """
                INSERT INTO dataset_review_adjudications(
                  id,review_item_id,adjudicator_id,decision,rationale,evidence
                ) VALUES ($1::uuid,$2::uuid,$3,$4,$5,$6::jsonb)
                """,
                str(uuid.uuid4()),
                review_item_id,
                adjudicator_id,
                request.decision,
                request.rationale,
                _json(request.evidence),
            )
            status = "approved" if request.decision == "approve" else "rejected"
            await connection.execute(
                "UPDATE dataset_review_items SET status=$1,version=version+1,updated_at=now() WHERE id=$2::uuid",
                status,
                review_item_id,
            )
            return await self._view(connection, review_item_id, adjudicator_id)

    async def record_promotion(self, actor_id: str, request: PromotionEvidenceCreate) -> PromotionEvidenceView:
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                """
                INSERT INTO dataset_promotion_events(
                  id,dataset_id,version_id,actor_id,manifest_sha256,policy_sha256,decision,evidence
                ) VALUES ($1::uuid,$2,$3,$4,$5,$6,$7,$8::jsonb)
                ON CONFLICT(dataset_id,version_id,manifest_sha256)
                DO UPDATE SET manifest_sha256=dataset_promotion_events.manifest_sha256
                RETURNING *
                """,
                str(uuid.uuid4()),
                request.dataset_id,
                request.version_id,
                actor_id,
                request.manifest_sha256,
                request.policy_sha256,
                request.decision,
                _json(request.evidence),
            )
            return PromotionEvidenceView(
                promotion_id=str(row["id"]),
                dataset_id=row["dataset_id"],
                version_id=row["version_id"],
                actor_id=row["actor_id"],
                manifest_sha256=row["manifest_sha256"],
                policy_sha256=row["policy_sha256"],
                decision=row["decision"],
                evidence=dict(row["evidence"]),
                created_at=row["created_at"].timestamp(),
            )


class DatasetAuthorityService:
    def __init__(self, store: DatasetAuthorityStore) -> None:
        self.store = store

    @classmethod
    def from_environment(cls) -> "DatasetAuthorityService":
        dsn = os.environ.get("AEITRON_DATABASE_URL", "").strip()
        if dsn.startswith(("postgres://", "postgresql://")):
            return cls(PostgresDatasetAuthorityStore(dsn))
        local_path = os.environ.get("AEITRON_DATA_AUTHORITY_DB", "artifacts/aeitron/dataset-authority.sqlite3")
        return cls(SQLiteDatasetAuthorityStore(local_path))

    async def record_source_snapshot(self, request: SourceSnapshotCreate) -> SourceSnapshotView:
        return await self.store.record_source_snapshot(request)

    async def enqueue(self, request: ReviewItemCreate) -> ReviewItemView:
        return await self.store.enqueue(request)

    async def list_items(self, *, status: str | None, limit: int, reviewer_id: str | None) -> list[ReviewItemView]:
        return await self.store.list_items(status=status, limit=limit, reviewer_id=reviewer_id)

    async def get_item(self, review_item_id: str, *, reviewer_id: str | None) -> ReviewItemView:
        return await self.store.get_item(review_item_id, reviewer_id=reviewer_id)

    async def claim(self, review_item_id: str, reviewer_id: str, lease_seconds: int = 3_600) -> ReviewAssignment:
        return await self.store.claim(review_item_id, reviewer_id, lease_seconds)

    async def decide(self, review_item_id: str, reviewer_id: str, request: ReviewDecisionCreate) -> ReviewItemView:
        return await self.store.decide(review_item_id, reviewer_id, request)

    async def adjudicate(
        self,
        review_item_id: str,
        adjudicator_id: str,
        request: ReviewAdjudicationCreate,
    ) -> ReviewItemView:
        return await self.store.adjudicate(review_item_id, adjudicator_id, request)

    async def record_promotion(self, actor_id: str, request: PromotionEvidenceCreate) -> PromotionEvidenceView:
        return await self.store.record_promotion(actor_id, request)

    async def review_evidence(self) -> ReviewEvidenceReport:
        return await self.store.review_evidence()


def content_snapshot_hash(content: str, source_snapshot_sha256: str) -> str:
    _validate_hash(source_snapshot_sha256, "source_snapshot_sha256")
    return hashlib.sha256(f"{source_snapshot_sha256}:{content}".encode("utf-8", "replace")).hexdigest()


def new_bootstrap_review_token() -> str:
    """Return a high-entropy value for out-of-band reviewer bootstrap workflows."""

    return secrets.token_urlsafe(32)


async def _write_review_evidence_report(output_path: str | Path) -> ReviewEvidenceReport:
    service = DatasetAuthorityService.from_environment()
    report = await service.review_evidence()
    target = Path(output_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export immutable review evidence from the configured Aeitron Dataset Authority.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    report_parser = subparsers.add_parser(
        "review-report",
        help="Export aggregate independent-review evidence for dataset promotion.",
    )
    report_parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.command == "review-report":
        report = asyncio.run(_write_review_evidence_report(args.output))
        print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
