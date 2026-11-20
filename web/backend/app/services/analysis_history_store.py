"""SQLite persistence for analysis/search history and leakage-safe market samples."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def owner_key(owner: str) -> str:
    return hashlib.sha256(("pa-history-v1:" + (owner or "unknown")).encode("utf-8")).hexdigest()


class AnalysisHistoryStore:
    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        return connection

    @contextmanager
    def _session(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._lock, self._session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS analysis_history (
                    id TEXT PRIMARY KEY,
                    owner_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    market TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    model_name TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    trace_json TEXT,
                    details_json TEXT,
                    provenance_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_analysis_owner_created
                    ON analysis_history(owner_key, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_analysis_owner_symbol
                    ON analysis_history(owner_key, symbol, created_at DESC);

                CREATE TABLE IF NOT EXISTS search_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_key TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    market TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_search_owner_created
                    ON search_history(owner_key, created_at DESC);

                CREATE TABLE IF NOT EXISTS segment_samples (
                    sample_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    decision_time_ms INTEGER NOT NULL,
                    segment_start_ms INTEGER NOT NULL,
                    lookback_bars INTEGER NOT NULL,
                    horizon_bars INTEGER NOT NULL,
                    feature_schema_version TEXT NOT NULL,
                    label_schema_version TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    split TEXT NOT NULL DEFAULT 'unassigned',
                    embargo_bars INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(market, exchange, symbol, timeframe, decision_time_ms,
                           feature_schema_version, label_schema_version)
                );
                CREATE INDEX IF NOT EXISTS idx_segments_instrument_time
                    ON segment_samples(market, exchange, symbol, timeframe, decision_time_ms);
                CREATE INDEX IF NOT EXISTS idx_segments_split
                    ON segment_samples(split, regime);
                """
            )
            # A process-local job cannot survive a backend restart. Mark any
            # unfinished persisted record as interrupted instead of leaving it queued forever.
            db.execute(
                "UPDATE analysis_history SET status='interrupted', finished_at=? "
                "WHERE status IN ('queued', 'running')",
                (_now(),),
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    @staticmethod
    def _decode(value: str | None, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    def save_analysis(
        self,
        *,
        analysis_id: str,
        owner: str,
        request_data: dict[str, Any],
        status: str,
        result: dict[str, Any] | None = None,
        trace: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
        created_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        owner_hash = owner_key(owner)
        model_name = ((details or {}).get("provider") or {}).get("model")
        provenance = (details or {}).get("provenance") or {}
        with self._lock, self._session() as db:
            db.execute(
                """
                INSERT INTO analysis_history (
                    id, owner_key, status, market, exchange, symbol, timeframe,
                    model_name, created_at, finished_at, request_json, result_json,
                    trace_json, details_json, provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    model_name=excluded.model_name,
                    finished_at=excluded.finished_at,
                    result_json=excluded.result_json,
                    trace_json=excluded.trace_json,
                    details_json=excluded.details_json,
                    provenance_json=excluded.provenance_json
                """,
                (
                    analysis_id,
                    owner_hash,
                    status,
                    str(request_data.get("market", "spot")),
                    str(request_data.get("exchange", "unknown")),
                    str(request_data.get("symbol", "")),
                    str(request_data.get("timeframe", "")),
                    model_name,
                    created_at or _now(),
                    finished_at,
                    self._json(request_data),
                    self._json(result) if result is not None else None,
                    self._json(trace) if trace is not None else None,
                    self._json(details) if details is not None else None,
                    self._json(provenance),
                ),
            )

    def list_analyses(
        self,
        owner: str,
        *,
        limit: int = 50,
        symbol: str | None = None,
        exchange: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["owner_key=?"]
        params: list[Any] = [owner_key(owner)]
        if symbol:
            clauses.append("symbol=?")
            params.append(symbol.upper())
        if exchange:
            clauses.append("exchange=?")
            params.append(exchange)
        params.append(min(max(int(limit), 1), 200))
        with self._session() as db:
            rows = db.execute(
                f"SELECT id, status, market, exchange, symbol, timeframe, model_name, created_at, finished_at, result_json "
                f"FROM analysis_history WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        result = []
        for row in rows:
            payload = self._decode(row["result_json"], {}) or {}
            diagnosis = payload.get("stage1_diagnosis") or {}
            decision_root = payload.get("stage2_decision") or {}
            decision = decision_root.get("decision", decision_root) if isinstance(decision_root, dict) else {}
            result.append(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "market": row["market"],
                    "exchange": row["exchange"],
                    "symbol": row["symbol"],
                    "timeframe": row["timeframe"],
                    "model_name": row["model_name"],
                    "created_at": row["created_at"],
                    "finished_at": row["finished_at"],
                    "cycle_position": diagnosis.get("cycle_position"),
                    "direction": diagnosis.get("direction"),
                    "gate_result": diagnosis.get("gate_result"),
                    "order_type": decision.get("order_type"),
                    "order_direction": decision.get("order_direction"),
                    "entry_price": decision.get("entry_price"),
                    "total_tokens": (payload.get("usage") or {}).get("total_tokens"),
                }
            )
        return result

    def get_analysis(self, owner: str, analysis_id: str) -> dict[str, Any] | None:
        with self._session() as db:
            row = db.execute(
                "SELECT * FROM analysis_history WHERE id=? AND owner_key=?",
                (analysis_id, owner_key(owner)),
            ).fetchone()
        if row is None:
            return None
        return {
            "analysis_id": row["id"],
            "status": row["status"],
            "request": self._decode(row["request_json"], {}),
            "result": self._decode(row["result_json"], None),
            "trace": self._decode(row["trace_json"], []),
            "details": self._decode(row["details_json"], {}),
            "provenance": self._decode(row["provenance_json"], {}),
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
        }

    def compare_analyses(self, owner: str, left_id: str, right_id: str) -> dict[str, Any]:
        left = self.get_analysis(owner, left_id)
        right = self.get_analysis(owner, right_id)
        if left is None or right is None:
            raise KeyError("历史分析记录不存在或无权访问")
        def structured(record: dict[str, Any]) -> dict[str, Any]:
            result = record.get("result") or {}
            diagnosis = result.get("stage1_diagnosis") or {}
            root = result.get("stage2_decision") or {}
            decision = root.get("decision", root) if isinstance(root, dict) else {}
            return {
                "cycle_position": diagnosis.get("cycle_position"),
                "direction": diagnosis.get("direction"),
                "gate_result": diagnosis.get("gate_result"),
                "support_levels": diagnosis.get("support_levels", []),
                "resistance_levels": diagnosis.get("resistance_levels", []),
                "detected_patterns": diagnosis.get("detected_patterns", []),
                "order_type": decision.get("order_type"),
                "order_direction": decision.get("order_direction"),
                "entry_price": decision.get("entry_price"),
                "stop_loss_price": decision.get("stop_loss_price"),
                "take_profit_price": decision.get("take_profit_price"),
                "trade_confidence": decision.get("trade_confidence"),
                "estimated_win_rate": decision.get("estimated_win_rate"),
            }
        left_values, right_values = structured(left), structured(right)
        differences = {
            key: {"left": left_values[key], "right": right_values[key]}
            for key in left_values
            if left_values[key] != right_values[key]
        }
        return {
            "left": {"analysis_id": left_id, "created_at": left["created_at"], "values": left_values},
            "right": {"analysis_id": right_id, "created_at": right["created_at"], "values": right_values},
            "differences": differences,
        }

    def save_search(
        self,
        owner: str,
        *,
        query_text: str,
        market: str,
        exchange: str,
        symbol: str,
        timeframe: str,
    ) -> None:
        query_text = (query_text or symbol).strip()[:200]
        with self._lock, self._session() as db:
            db.execute(
                """
                INSERT INTO search_history(owner_key, query_text, market, exchange, symbol, timeframe, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_key(owner), query_text, market, exchange, symbol.upper(), timeframe, _now()),
            )
            db.execute(
                "DELETE FROM search_history WHERE owner_key=? AND id NOT IN "
                "(SELECT id FROM search_history WHERE owner_key=? ORDER BY created_at DESC LIMIT 100)",
                (owner_key(owner), owner_key(owner)),
            )

    def list_searches(self, owner: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._session() as db:
            rows = db.execute(
                "SELECT id, query_text, market, exchange, symbol, timeframe, created_at "
                "FROM search_history WHERE owner_key=? ORDER BY created_at DESC LIMIT ?",
                (owner_key(owner), min(max(int(limit), 1), 200)),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_analysis(self, owner: str, analysis_id: str) -> bool:
        with self._session() as db:
            cursor = db.execute(
                "DELETE FROM analysis_history WHERE id=? AND owner_key=?",
                (analysis_id, owner_key(owner)),
            )
        return cursor.rowcount > 0

    def delete_search(self, owner: str, search_id: int) -> bool:
        with self._session() as db:
            cursor = db.execute(
                "DELETE FROM search_history WHERE id=? AND owner_key=?",
                (search_id, owner_key(owner)),
            )
        return cursor.rowcount > 0

    def upsert_segments(self, samples: list[dict[str, Any]]) -> int:
        if not samples:
            return 0
        from app.services.segmenter import assign_temporal_splits
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for item in samples:
            key = (item["market"], item["exchange"], item["symbol"], item["timeframe"])
            groups.setdefault(key, []).append(item)
        inserted = 0
        with self._lock, self._session() as db:
            for key, group in groups.items():
                changes_before = db.total_changes
                db.executemany(
                    """
                    INSERT INTO segment_samples (
                        sample_id, market, exchange, symbol, timeframe, regime,
                        decision_time_ms, segment_start_ms, lookback_bars, horizon_bars,
                        feature_schema_version, label_schema_version, features_json,
                        labels_json, split, embargo_bars, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unassigned', 0, ?)
                    ON CONFLICT(sample_id) DO NOTHING
                    """,
                    [
                        (
                            item["sample_id"], item["market"], item["exchange"], item["symbol"],
                            item["timeframe"], item["regime"], int(item["decision_time_ms"]),
                            int(item["segment_start_ms"]), int(item["lookback_bars"]),
                            int(item["horizon_bars"]), item["feature_schema_version"],
                            item["label_schema_version"], self._json(item["features"]),
                            self._json(item["labels"]), _now(),
                        )
                        for item in group
                    ],
                )
                inserted += db.total_changes - changes_before
                all_rows = db.execute(
                    """
                    SELECT sample_id, decision_time_ms, horizon_bars, regime,
                           features_json, labels_json, market, exchange, symbol, timeframe,
                           segment_start_ms, lookback_bars, feature_schema_version, label_schema_version
                    FROM segment_samples
                    WHERE market=? AND exchange=? AND symbol=? AND timeframe=?
                    ORDER BY decision_time_ms
                    """,
                    key,
                ).fetchall()
                split_inputs = [
                    {
                        "sample_id": row["sample_id"],
                        "decision_time_ms": row["decision_time_ms"],
                        "horizon_bars": row["horizon_bars"],
                    }
                    for row in all_rows
                ]
                split_rows = assign_temporal_splits(split_inputs)
                split_map = {item["sample_id"]: (item["split"], item["embargo_bars"]) for item in split_rows}
                db.executemany(
                    "UPDATE segment_samples SET split=?, embargo_bars=? WHERE sample_id=?",
                    [(split, embargo, sample_id) for sample_id, (split, embargo) in split_map.items()],
                )
        return inserted

    def list_segments(
        self,
        *,
        market: str | None = None,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        split: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        for column, value in (
            ("market", market), ("exchange", exchange), ("symbol", symbol.upper() if symbol else None),
            ("timeframe", timeframe), ("split", split),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        params.append(min(max(int(limit), 1), 500))
        with self._session() as db:
            rows = db.execute(
                f"SELECT * FROM segment_samples WHERE {' AND '.join(clauses)} "
                "ORDER BY decision_time_ms DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._segment_row(row) for row in rows]

    def similar_segments(
        self,
        *,
        market: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        features: dict[str, Any],
        before_ms: int,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        from app.services.segmenter import similarity_score
        with self._session() as db:
            rows = db.execute(
                """
                SELECT * FROM segment_samples
                WHERE market=? AND exchange=? AND symbol=? AND timeframe=?
                  AND decision_time_ms < ? AND labels_json IS NOT NULL
                ORDER BY decision_time_ms DESC LIMIT 500
                """,
                (market, exchange, symbol.upper(), timeframe, int(before_ms)),
            ).fetchall()
        candidates = [self._segment_row(row) for row in rows]
        for item in candidates:
            item["similarity"] = similarity_score(
                {"regime": features.get("regime"), "features": features.get("features", {})},
                {"regime": item["regime"], "features": item["features"]},
            )
        candidates.sort(key=lambda item: item["similarity"], reverse=True)
        return candidates[: min(max(int(limit), 1), 20)]

    def dataset_stats(
        self,
        *,
        market: str | None = None,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        for column, value in (
            ("market", market), ("exchange", exchange),
            ("symbol", symbol.upper() if symbol else None), ("timeframe", timeframe),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        with self._session() as db:
            rows = db.execute(
                f"SELECT labels_json, split, regime, feature_schema_version, label_schema_version "
                f"FROM segment_samples WHERE {' AND '.join(clauses)}",
                params,
            ).fetchall()
        labels: dict[str, int] = {}
        splits: dict[str, int] = {}
        regimes: dict[str, int] = {}
        for row in rows:
            label_data = self._decode(row["labels_json"], {})
            label = label_data.get("forward_direction", "unknown")
            labels[label] = labels.get(label, 0) + 1
            splits[row["split"]] = splits.get(row["split"], 0) + 1
            regimes[row["regime"]] = regimes.get(row["regime"], 0) + 1
        return {
            "sample_count": len(rows),
            "label_distribution": labels,
            "split_distribution": splits,
            "regime_distribution": regimes,
            "feature_schema_version": rows[0]["feature_schema_version"] if rows else "pa-segment-features-v1",
            "label_schema_version": rows[0]["label_schema_version"] if rows else "forward-outcome-v1",
            "note": "这里只统计历史结果标签分布，不代表任何模型预测准确率。",
        }

    def _segment_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "sample_id": row["sample_id"],
            "market": row["market"],
            "exchange": row["exchange"],
            "symbol": row["symbol"],
            "timeframe": row["timeframe"],
            "regime": row["regime"],
            "decision_time_ms": row["decision_time_ms"],
            "segment_start_ms": row["segment_start_ms"],
            "lookback_bars": row["lookback_bars"],
            "horizon_bars": row["horizon_bars"],
            "feature_schema_version": row["feature_schema_version"],
            "label_schema_version": row["label_schema_version"],
            "features": self._decode(row["features_json"], {}),
            "labels": self._decode(row["labels_json"], {}),
            "split": row["split"],
            "embargo_bars": row["embargo_bars"],
        }
