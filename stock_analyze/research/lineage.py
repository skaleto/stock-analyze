"""Append-only, rebuildable lineage projection for research decisions.

The SQLite database is an audit/query projection. Formal account CSV files
remain the paper-trading source of truth and are never read or written here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class LineageConflictError(RuntimeError):
    """Raised when a stable primary key is reused with different content."""


@dataclass(frozen=True)
class _TableSpec:
    primary_key: str
    columns: tuple[str, ...]
    required: tuple[str, ...]
    filters: tuple[str, ...]


_TABLE_SPECS = {
    "decision_runs": _TableSpec(
        primary_key="decision_run_id",
        columns=(
            "decision_run_id",
            "agent_id",
            "market",
            "strategy_id",
            "as_of",
            "account_state_hash",
            "feature_snapshot_id",
        ),
        required=("decision_run_id",),
        filters=(
            "decision_run_id",
            "agent_id",
            "market",
            "strategy_id",
            "as_of",
            "account_state_hash",
            "feature_snapshot_id",
        ),
    ),
    "candidate_evaluations": _TableSpec(
        primary_key="candidate_evaluation_id",
        columns=(
            "candidate_evaluation_id",
            "decision_run_id",
            "security_code",
            "eligible",
            "rejection_reason",
            "model_version",
            "horizon",
        ),
        required=(
            "candidate_evaluation_id",
            "decision_run_id",
            "security_code",
        ),
        filters=(
            "candidate_evaluation_id",
            "decision_run_id",
            "security_code",
            "eligible",
            "rejection_reason",
            "model_version",
            "horizon",
        ),
    ),
    "target_allocations": _TableSpec(
        primary_key="target_allocation_id",
        columns=(
            "target_allocation_id",
            "decision_run_id",
            "candidate_evaluation_id",
            "security_code",
        ),
        required=(
            "target_allocation_id",
            "decision_run_id",
            "candidate_evaluation_id",
            "security_code",
        ),
        filters=(
            "target_allocation_id",
            "decision_run_id",
            "candidate_evaluation_id",
            "security_code",
        ),
    ),
    "orders": _TableSpec(
        primary_key="order_id",
        columns=(
            "order_id",
            "decision_run_id",
            "target_allocation_id",
            "security_code",
            "side",
        ),
        required=(
            "order_id",
            "decision_run_id",
            "target_allocation_id",
            "security_code",
        ),
        filters=(
            "order_id",
            "decision_run_id",
            "target_allocation_id",
            "security_code",
            "side",
        ),
    ),
    "fills": _TableSpec(
        primary_key="fill_id",
        columns=("fill_id", "order_id", "decision_run_id"),
        required=("fill_id", "order_id"),
        filters=("fill_id", "order_id", "decision_run_id"),
    ),
    "pnl_attributions": _TableSpec(
        primary_key="pnl_attribution_id",
        columns=(
            "pnl_attribution_id",
            "decision_run_id",
            "fill_id",
            "security_code",
            "as_of",
        ),
        required=(
            "pnl_attribution_id",
            "decision_run_id",
            "security_code",
        ),
        filters=(
            "pnl_attribution_id",
            "decision_run_id",
            "fill_id",
            "security_code",
            "as_of",
        ),
    ),
    "experiment_trials": _TableSpec(
        primary_key="trial_id",
        columns=(
            "trial_id",
            "experiment_id",
            "market",
            "horizon",
            "model_version",
        ),
        required=("trial_id", "experiment_id"),
        filters=(
            "trial_id",
            "experiment_id",
            "market",
            "horizon",
            "model_version",
        ),
    ),
}


_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_runs (
    decision_run_id TEXT PRIMARY KEY,
    agent_id TEXT,
    market TEXT,
    strategy_id TEXT,
    as_of TEXT,
    account_state_hash TEXT,
    feature_snapshot_id TEXT,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    inserted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_evaluations (
    candidate_evaluation_id TEXT PRIMARY KEY,
    decision_run_id TEXT NOT NULL,
    security_code TEXT NOT NULL,
    eligible INTEGER,
    rejection_reason TEXT,
    model_version TEXT,
    horizon INTEGER,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    inserted_at TEXT NOT NULL,
    UNIQUE(candidate_evaluation_id, decision_run_id),
    FOREIGN KEY(decision_run_id) REFERENCES decision_runs(decision_run_id)
);

CREATE TABLE IF NOT EXISTS target_allocations (
    target_allocation_id TEXT PRIMARY KEY,
    decision_run_id TEXT NOT NULL,
    candidate_evaluation_id TEXT NOT NULL,
    security_code TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    inserted_at TEXT NOT NULL,
    UNIQUE(target_allocation_id, decision_run_id),
    FOREIGN KEY(decision_run_id) REFERENCES decision_runs(decision_run_id),
    FOREIGN KEY(candidate_evaluation_id, decision_run_id)
        REFERENCES candidate_evaluations(candidate_evaluation_id, decision_run_id)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    decision_run_id TEXT NOT NULL,
    target_allocation_id TEXT NOT NULL,
    security_code TEXT NOT NULL,
    side TEXT,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    inserted_at TEXT NOT NULL,
    UNIQUE(order_id, decision_run_id),
    FOREIGN KEY(decision_run_id) REFERENCES decision_runs(decision_run_id),
    FOREIGN KEY(target_allocation_id, decision_run_id)
        REFERENCES target_allocations(target_allocation_id, decision_run_id)
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    decision_run_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    inserted_at TEXT NOT NULL,
    UNIQUE(fill_id, decision_run_id),
    FOREIGN KEY(order_id, decision_run_id)
        REFERENCES orders(order_id, decision_run_id)
);

CREATE TABLE IF NOT EXISTS pnl_attributions (
    pnl_attribution_id TEXT PRIMARY KEY,
    decision_run_id TEXT NOT NULL,
    fill_id TEXT,
    security_code TEXT NOT NULL,
    as_of TEXT,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    inserted_at TEXT NOT NULL,
    FOREIGN KEY(decision_run_id) REFERENCES decision_runs(decision_run_id),
    FOREIGN KEY(fill_id, decision_run_id)
        REFERENCES fills(fill_id, decision_run_id)
);

CREATE TABLE IF NOT EXISTS experiment_trials (
    trial_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL,
    market TEXT,
    horizon INTEGER,
    model_version TEXT,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    inserted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_run
    ON candidate_evaluations(decision_run_id, security_code);
CREATE INDEX IF NOT EXISTS idx_allocation_run
    ON target_allocations(decision_run_id, security_code);
CREATE INDEX IF NOT EXISTS idx_order_run
    ON orders(decision_run_id, security_code);
CREATE INDEX IF NOT EXISTS idx_fill_order
    ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_pnl_run
    ON pnl_attributions(decision_run_id, security_code);
CREATE INDEX IF NOT EXISTS idx_trial_experiment
    ON experiment_trials(experiment_id, market, horizon);
"""


def _append_only_triggers() -> str:
    statements: list[str] = []
    for table in _TABLE_SPECS:
        statements.extend(
            [
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_reject_update
                BEFORE UPDATE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'lineage_append_only');
                END;
                """,
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_reject_delete
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, 'lineage_append_only');
                END;
                """,
            ]
        )
    return "\n".join(statements)


class ResearchLineageStore:
    """SQLite projection with immutable rows and deterministic replay."""

    TABLES = tuple(_TABLE_SPECS)
    SCHEMA_VERSION = 1

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _migrate(self) -> None:
        with closing(self.connect()) as connection:
            connection.executescript(_SCHEMA)
            connection.executescript(_append_only_triggers())
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta(version, applied_at) VALUES(?, ?)",
                (self.SCHEMA_VERSION, _utc_now()),
            )
            connection.commit()

    def append(
        self,
        table: str,
        records: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> int:
        """Append one or more immutable records and return the inserted count."""

        spec = self._spec(table)
        rows = _normalise_records(records)
        if not rows:
            return 0

        inserted = 0
        with closing(self.connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                for row in rows:
                    self._validate_row(table, spec, row)
                    payload_json = _canonical_json(row)
                    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                    primary_key = str(row[spec.primary_key])
                    existing = connection.execute(
                        f"SELECT payload_hash FROM {table} "
                        f"WHERE {spec.primary_key}=?",
                        (primary_key,),
                    ).fetchone()
                    if existing is not None:
                        if str(existing["payload_hash"]) != payload_hash:
                            raise LineageConflictError(f"{table}:{primary_key}")
                        continue

                    projected = self._projected_values(connection, table, spec, row)
                    columns = [*spec.columns, "payload_hash", "payload_json", "inserted_at"]
                    placeholders = ",".join("?" for _ in columns)
                    connection.execute(
                        f"INSERT INTO {table}({','.join(columns)}) "
                        f"VALUES({placeholders})",
                        (
                            *projected,
                            payload_hash,
                            payload_json,
                            _utc_now(),
                        ),
                    )
                    inserted += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return inserted

    def append_decision_runs(
        self,
        records: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> int:
        return self.append("decision_runs", records)

    def append_candidate_evaluations(
        self,
        records: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> int:
        return self.append("candidate_evaluations", records)

    def append_target_allocations(
        self,
        records: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> int:
        return self.append("target_allocations", records)

    def append_orders(
        self,
        records: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> int:
        return self.append("orders", records)

    def append_fills(
        self,
        records: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> int:
        return self.append("fills", records)

    def append_pnl_attributions(
        self,
        records: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> int:
        return self.append("pnl_attributions", records)

    def append_experiment_trials(
        self,
        records: Mapping[str, Any] | list[Mapping[str, Any]],
    ) -> int:
        return self.append("experiment_trials", records)

    def query(
        self,
        table: str,
        filters: Mapping[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return original payloads matching indexed equality filters."""

        spec = self._spec(table)
        conditions: list[str] = []
        values: list[Any] = []
        for column, value in (filters or {}).items():
            if column not in spec.filters:
                raise ValueError(f"lineage_filter_invalid:{table}:{column}")
            if value is None:
                conditions.append(f"{column} IS NULL")
            else:
                conditions.append(f"{column}=?")
                values.append(value)

        query = f"SELECT payload_json FROM {table}"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY {spec.primary_key}"
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError("lineage_limit_invalid")
            query += " LIMIT ?"
            values.append(limit)

        with closing(self.connect()) as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return [json.loads(str(row["payload_json"])) for row in rows]

    def count(self, table: str) -> int:
        self._spec(table)
        with closing(self.connect()) as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])

    def project(
        self,
        *,
        decision_run_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Project the complete database or one decision-to-P&L graph."""

        if decision_run_id is None:
            return {table: self.query(table) for table in self.TABLES}

        projection = {
            "decision_runs": self.query(
                "decision_runs",
                {"decision_run_id": decision_run_id},
            ),
            "candidate_evaluations": self.query(
                "candidate_evaluations",
                {"decision_run_id": decision_run_id},
            ),
            "target_allocations": self.query(
                "target_allocations",
                {"decision_run_id": decision_run_id},
            ),
            "orders": self.query(
                "orders",
                {"decision_run_id": decision_run_id},
            ),
            "fills": [],
            "pnl_attributions": self.query(
                "pnl_attributions",
                {"decision_run_id": decision_run_id},
            ),
            "experiment_trials": [],
        }
        order_ids = {
            str(order["order_id"])
            for order in projection["orders"]
        }
        projection["fills"] = [
            fill
            for fill in self.query("fills")
            if str(fill.get("order_id") or "") in order_ids
        ]
        return projection

    def rebuild(
        self,
        projection: Mapping[str, list[Mapping[str, Any]]],
    ) -> int:
        """Atomically rebuild this query projection from immutable records."""

        unknown = set(projection) - set(self.TABLES)
        if unknown:
            raise ValueError(f"lineage_table_invalid:{sorted(unknown)[0]}")

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.db_path.name}.",
            suffix=".rebuild",
            dir=self.db_path.parent,
        )
        os.close(file_descriptor)
        temporary_path = Path(temporary_name)
        temporary_path.unlink(missing_ok=True)

        try:
            replacement = ResearchLineageStore(temporary_path)
            inserted = 0
            for table in self.TABLES:
                inserted += replacement.append(
                    table,
                    list(projection.get(table, [])),
                )
            if replacement.integrity_check() != "ok":
                raise sqlite3.DatabaseError("lineage_rebuild_integrity_failed")
            violations = replacement.foreign_key_violations()
            if violations:
                raise sqlite3.IntegrityError(
                    f"lineage_rebuild_foreign_key_failed:{violations[0]}"
                )

            replacement._checkpoint()
            self._checkpoint()
            _remove_sidecars(temporary_path)
            _remove_sidecars(self.db_path)
            os.replace(temporary_path, self.db_path)
            _fsync_directory(self.db_path.parent)
            return inserted
        except Exception:
            temporary_path.unlink(missing_ok=True)
            _remove_sidecars(temporary_path)
            raise

    def integrity_check(self) -> str:
        with closing(self.connect()) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        return str(row[0])

    def foreign_key_violations(self) -> list[tuple[Any, ...]]:
        with closing(self.connect()) as connection:
            rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        return [tuple(row) for row in rows]

    def _checkpoint(self) -> None:
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    @staticmethod
    def _spec(table: str) -> _TableSpec:
        try:
            return _TABLE_SPECS[table]
        except KeyError as exc:
            raise ValueError(f"lineage_table_invalid:{table}") from exc

    @staticmethod
    def _validate_row(
        table: str,
        spec: _TableSpec,
        row: Mapping[str, Any],
    ) -> None:
        for field in spec.required:
            value = row.get(field)
            if value is None or value == "":
                raise ValueError(f"lineage_field_required:{table}:{field}")
        primary_key = row[spec.primary_key]
        if not isinstance(primary_key, str):
            raise TypeError(f"lineage_primary_key_must_be_text:{table}")

    @staticmethod
    def _projected_values(
        connection: sqlite3.Connection,
        table: str,
        spec: _TableSpec,
        row: Mapping[str, Any],
    ) -> tuple[Any, ...]:
        derived: dict[str, Any] = {}
        if table == "fills" and not row.get("decision_run_id"):
            parent = connection.execute(
                "SELECT decision_run_id FROM orders WHERE order_id=?",
                (row.get("order_id"),),
            ).fetchone()
            derived["decision_run_id"] = (
                str(parent["decision_run_id"]) if parent is not None else None
            )
        return tuple(
            derived.get(column, row.get(column))
            for column in spec.columns
        )


def _normalise_records(
    records: Mapping[str, Any] | list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(records, Mapping):
        return [dict(records)]
    if not isinstance(records, list):
        raise TypeError("lineage_records_must_be_dict_or_list")
    normalised: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("lineage_record_must_be_dict")
        normalised.append(dict(record))
    return normalised


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remove_sidecars(db_path: Path) -> None:
    Path(f"{db_path}-wal").unlink(missing_ok=True)
    Path(f"{db_path}-shm").unlink(missing_ok=True)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
