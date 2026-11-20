"""SQLite persistence for analysis/search history and leakage-safe market samples."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
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

                CREATE TABLE IF NOT EXISTS market_candles (
                    market TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open_time_ms INTEGER NOT NULL,
                    close_time_ms INTEGER NOT NULL,
                    open TEXT NOT NULL,
                    high TEXT NOT NULL,
                    low TEXT NOT NULL,
                    close TEXT NOT NULL,
                    volume TEXT NOT NULL,
                    quote_volume TEXT NOT NULL DEFAULT '',
                    trades INTEGER,
                    taker_buy_base_volume TEXT NOT NULL DEFAULT '',
                    taker_buy_quote_volume TEXT NOT NULL DEFAULT '',
                    volume_unit TEXT NOT NULL DEFAULT 'base_asset',
                    contract_volume TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (market, exchange, symbol, timeframe, open_time_ms)
                );
                CREATE INDEX IF NOT EXISTS idx_market_candles_instrument_time
                    ON market_candles(market, exchange, symbol, timeframe, open_time_ms);

                CREATE TABLE IF NOT EXISTS derivatives_observations (
                    market TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    period TEXT NOT NULL DEFAULT '',
                    event_time_ms INTEGER NOT NULL,
                    fetched_at_ms INTEGER NOT NULL,
                    available_at_ms INTEGER NOT NULL DEFAULT 0,
                    data_kind TEXT NOT NULL CHECK(data_kind IN ('historical', 'snapshot')),
                    value_json TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    source_endpoint TEXT NOT NULL,
                    quality TEXT NOT NULL DEFAULT 'ok',
                    PRIMARY KEY (
                        market, exchange, symbol, metric, period, event_time_ms, data_kind
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_derivatives_instrument_time
                    ON derivatives_observations(
                        market, exchange, symbol, event_time_ms, metric
                    );
                CREATE INDEX IF NOT EXISTS idx_derivatives_kind_time
                    ON derivatives_observations(data_kind, event_time_ms);

                CREATE TABLE IF NOT EXISTS derivatives_collection_runs (
                    run_id TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    period TEXT NOT NULL,
                    start_time_ms INTEGER NOT NULL,
                    end_time_ms INTEGER NOT NULL,
                    fetched_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_derivatives_runs_instrument
                    ON derivatives_collection_runs(
                        market, exchange, symbol, period, fetched_at_ms DESC
                    );
                """
            )
            derivative_columns = {
                row["name"]
                for row in db.execute("PRAGMA table_info(derivatives_observations)").fetchall()
            }
            if "available_at_ms" not in derivative_columns:
                db.execute(
                    "ALTER TABLE derivatives_observations "
                    "ADD COLUMN available_at_ms INTEGER NOT NULL DEFAULT 0"
                )
                # Old historical imports did not retain publication/availability
                # semantics. Keep them unavailable to strict replay rather than
                # guessing when they first became usable. Old snapshots used the
                # fetch timestamp as their event timestamp and can be retained.
                db.execute(
                    "UPDATE derivatives_observations "
                    "SET available_at_ms=CASE "
                    "WHEN data_kind='snapshot' THEN fetched_at_ms ELSE 0 END"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_derivatives_available_time "
                "ON derivatives_observations(market, exchange, symbol, available_at_ms)"
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

    def upsert_derivatives_observations(
        self,
        observations: list[dict[str, Any]],
    ) -> int:
        if not observations:
            return 0
        rows = []
        for item in observations:
            data_kind = str(item.get("data_kind", "historical"))
            if data_kind not in {"historical", "snapshot"}:
                raise ValueError("data_kind must be historical or snapshot")
            rows.append(
                (
                    str(item["market"]),
                    str(item["exchange"]),
                    str(item["symbol"]).upper(),
                    str(item["metric"]),
                    str(item.get("period") or ""),
                    int(item["event_time_ms"]),
                    int(item["fetched_at_ms"]),
                    int(item.get("available_at_ms", 0)),
                    data_kind,
                    self._json(item.get("value", {})),
                    self._json(item.get("raw", {})),
                    str(item["source_endpoint"]),
                    str(item.get("quality") or "ok"),
                )
            )
        with self._lock, self._session() as db:
            db.executemany(
                """
                INSERT INTO derivatives_observations (
                    market, exchange, symbol, metric, period, event_time_ms,
                    fetched_at_ms, available_at_ms, data_kind, value_json, raw_json,
                    source_endpoint, quality
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(
                    market, exchange, symbol, metric, period, event_time_ms, data_kind
                ) DO UPDATE SET
                    fetched_at_ms=excluded.fetched_at_ms,
                    available_at_ms=excluded.available_at_ms,
                    value_json=excluded.value_json,
                    raw_json=excluded.raw_json,
                    source_endpoint=excluded.source_endpoint,
                    quality=excluded.quality
                """,
                rows,
            )
        return len(rows)

    def save_derivatives_collection_run(self, run: dict[str, Any]) -> None:
        with self._lock, self._session() as db:
            db.execute(
                """
                INSERT INTO derivatives_collection_runs (
                    run_id, market, exchange, symbol, period, start_time_ms,
                    end_time_ms, fetched_at_ms, status, metrics_json, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    fetched_at_ms=excluded.fetched_at_ms,
                    metrics_json=excluded.metrics_json,
                    note=excluded.note
                """,
                (
                    str(run["run_id"]),
                    str(run["market"]),
                    str(run["exchange"]),
                    str(run["symbol"]).upper(),
                    str(run.get("period") or ""),
                    int(run["start_time_ms"]),
                    int(run["end_time_ms"]),
                    int(run["fetched_at_ms"]),
                    str(run["status"]),
                    self._json(run.get("metrics", {})),
                    str(run.get("note") or ""),
                ),
            )

    def list_derivatives_observations(
        self,
        *,
        market: str,
        exchange: str,
        symbol: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        metric: str | None = None,
        period: str | None = None,
        data_kind: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        clauses = ["market=?", "exchange=?", "symbol=?"]
        params: list[Any] = [market, exchange, symbol.upper()]
        for column, value in (
            ("event_time_ms", None),
            ("metric", metric),
            ("period", period),
            ("data_kind", data_kind),
        ):
            if column == "event_time_ms":
                if start_ms is not None:
                    clauses.append("event_time_ms>=?")
                    params.append(int(start_ms))
                if end_ms is not None:
                    clauses.append("event_time_ms<=?")
                    params.append(int(end_ms))
            elif value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        params.append(min(max(int(limit), 1), 50000))
        with self._session() as db:
            rows = db.execute(
                f"""
                SELECT * FROM derivatives_observations
                WHERE {' AND '.join(clauses)}
                ORDER BY event_time_ms ASC, metric ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {
                "market": row["market"],
                "exchange": row["exchange"],
                "symbol": row["symbol"],
                "metric": row["metric"],
                "period": row["period"],
                "event_time_ms": row["event_time_ms"],
                "fetched_at_ms": row["fetched_at_ms"],
                "available_at_ms": row["available_at_ms"],
                "data_kind": row["data_kind"],
                "value": self._decode(row["value_json"], {}),
                "raw": self._decode(row["raw_json"], {}),
                "source_endpoint": row["source_endpoint"],
                "quality": row["quality"],
            }
            for row in rows
        ]

    def list_derivatives_observations_asof(
        self,
        *,
        market: str,
        exchange: str,
        symbol: str,
        decision_time_ms: int,
        period: str | None = None,
        include_snapshots: bool = True,
        limit: int = 10000,
    ) -> dict[str, Any]:
        """Return only observations both occurring and available by the decision cutoff.

        Historical records use exchange-specific publication availability times.
        Snapshots are usable only when their actual fetch time is no later than
        the historical decision time. Legacy historical rows with unknown
        availability (available_at_ms=0) are deliberately excluded.
        """
        clauses = [
            "market=?",
            "exchange=?",
            "symbol=?",
            "event_time_ms<=?",
            "available_at_ms>0",
            "available_at_ms<=?",
        ]
        params: list[Any] = [
            market,
            exchange,
            symbol.upper(),
            int(decision_time_ms),
            int(decision_time_ms),
        ]
        if period is not None:
            clauses.append("(period=? OR metric='funding_rate')")
            params.append(period)
        if not include_snapshots:
            clauses.append("data_kind='historical'")
        params.append(min(max(int(limit), 1), 50000))
        with self._session() as db:
            rows = db.execute(
                f"""
                SELECT * FROM derivatives_observations
                WHERE {' AND '.join(clauses)}
                ORDER BY available_at_ms ASC, event_time_ms ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            total_rows = db.execute(
                f"""
                SELECT COUNT(*) FROM derivatives_observations
                WHERE market=? AND exchange=? AND symbol=?
                  AND event_time_ms<=?
                """,
                (market, exchange, symbol.upper(), int(decision_time_ms)),
            ).fetchone()[0]
        items = [
            {
                "market": row["market"],
                "exchange": row["exchange"],
                "symbol": row["symbol"],
                "metric": row["metric"],
                "period": row["period"],
                "event_time_ms": row["event_time_ms"],
                "fetched_at_ms": row["fetched_at_ms"],
                "available_at_ms": row["available_at_ms"],
                "data_kind": row["data_kind"],
                "value": self._decode(row["value_json"], {}),
                "raw": self._decode(row["raw_json"], {}),
                "source_endpoint": row["source_endpoint"],
                "quality": row["quality"],
            }
            for row in rows
        ]
        return {
            "items": items,
            "eligible_count": len(items),
            "excluded_or_unavailable_count": max(0, int(total_rows) - len(items)),
            "latest_available_at_ms": max(
                (item["available_at_ms"] for item in items),
                default=None,
            ),
        }

    def derivatives_archive_stats(
        self,
        *,
        market: str | None = None,
        exchange: str | None = None,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        for column, value in (
            ("market", market),
            ("exchange", exchange),
            ("symbol", symbol.upper() if symbol else None),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        with self._session() as db:
            rows = db.execute(
                f"""
                SELECT market, exchange, symbol, metric, period, data_kind,
                       COUNT(*) AS record_count,
                       MIN(event_time_ms) AS oldest_event_time_ms,
                       MAX(event_time_ms) AS newest_event_time_ms,
                       MAX(fetched_at_ms) AS last_fetched_at_ms
                FROM derivatives_observations
                WHERE {' AND '.join(clauses)}
                GROUP BY market, exchange, symbol, metric, period, data_kind
                ORDER BY exchange, symbol, metric, period, data_kind
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_derivatives_collection_runs(
        self,
        *,
        market: str,
        exchange: str,
        symbol: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self._session() as db:
            rows = db.execute(
                """
                SELECT * FROM derivatives_collection_runs
                WHERE market=? AND exchange=? AND symbol=?
                ORDER BY fetched_at_ms DESC LIMIT ?
                """,
                (market, exchange, symbol.upper(), min(max(int(limit), 1), 100)),
            ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "period": row["period"],
                "start_time_ms": row["start_time_ms"],
                "end_time_ms": row["end_time_ms"],
                "fetched_at_ms": row["fetched_at_ms"],
                "status": row["status"],
                "metrics": self._decode(row["metrics_json"], {}),
                "note": row["note"],
            }
            for row in rows
        ]

    def save_derivatives_snapshot(
        self,
        *,
        market: str,
        exchange: str,
        symbol: str,
        context: dict[str, Any],
        source_endpoint: str,
    ) -> int:
        fetched_at_ms = int(time.time() * 1000)
        quality = context.get("data_quality") or {}
        excluded = {
            "symbol", "exchange", "data_timestamp", "data_quality",
            "stats_history", "funding_rate_history",
        }
        observations = []
        for metric, value in context.items():
            if metric in excluded or value is None:
                continue
            observations.append(
                {
                    "market": market,
                    "exchange": exchange,
                    "symbol": symbol,
                    "metric": metric,
                    "period": str(context.get("stats_interval") or ""),
                    "event_time_ms": int(context.get("data_timestamp") or fetched_at_ms),
                    "fetched_at_ms": fetched_at_ms,
                    "available_at_ms": fetched_at_ms,
                    "data_kind": "snapshot",
                    "value": {"value": value},
                    "raw": context,
                    "source_endpoint": source_endpoint,
                    "quality": str(quality.get(metric, "snapshot")),
                }
            )
        return self.upsert_derivatives_observations(observations)

    def upsert_market_candles(
        self,
        *,
        market: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        candles: list[Any],
    ) -> int:
        """Persist completed OHLCV candles with source/instrument identity."""
        def value(candle: Any, key: str, default: Any = None) -> Any:
            if isinstance(candle, dict):
                return candle.get(key, default)
            return getattr(candle, key, default)

        rows = []
        for candle in candles:
            if not bool(value(candle, "closed", False)):
                continue
            rows.append(
                (
                    market,
                    exchange,
                    symbol.upper(),
                    timeframe,
                    int(value(candle, "open_time")),
                    int(value(candle, "close_time")),
                    str(value(candle, "open")),
                    str(value(candle, "high")),
                    str(value(candle, "low")),
                    str(value(candle, "close")),
                    str(value(candle, "volume")),
                    str(value(candle, "quote_volume", "") or ""),
                    value(candle, "trades"),
                    str(value(candle, "taker_buy_base_volume", "") or ""),
                    str(value(candle, "taker_buy_quote_volume", "") or ""),
                    str(value(candle, "volume_unit", "base_asset") or "base_asset"),
                    (
                        str(value(candle, "contract_volume"))
                        if value(candle, "contract_volume") is not None
                        else None
                    ),
                    _now(),
                )
            )
        if not rows:
            return 0

        with self._lock, self._session() as db:
            db.executemany(
                """
                INSERT INTO market_candles (
                    market, exchange, symbol, timeframe, open_time_ms, close_time_ms,
                    open, high, low, close, volume, quote_volume, trades,
                    taker_buy_base_volume, taker_buy_quote_volume, volume_unit,
                    contract_volume, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, exchange, symbol, timeframe, open_time_ms)
                DO UPDATE SET
                    close_time_ms=excluded.close_time_ms,
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    quote_volume=excluded.quote_volume,
                    trades=excluded.trades,
                    taker_buy_base_volume=excluded.taker_buy_base_volume,
                    taker_buy_quote_volume=excluded.taker_buy_quote_volume,
                    volume_unit=excluded.volume_unit,
                    contract_volume=excluded.contract_volume,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def market_candle_coverage(
        self,
        *,
        market: str,
        exchange: str,
        symbol: str,
        timeframe: str,
    ) -> dict[str, int | None]:
        with self._session() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS candle_count,
                       MIN(open_time_ms) AS oldest_open_time_ms,
                       MAX(open_time_ms) AS newest_open_time_ms
                FROM market_candles
                WHERE market=? AND exchange=? AND symbol=? AND timeframe=?
                """,
                (market, exchange, symbol.upper(), timeframe),
            ).fetchone()
        return {
            "candle_count": int(row["candle_count"]),
            "oldest_open_time_ms": row["oldest_open_time_ms"],
            "newest_open_time_ms": row["newest_open_time_ms"],
        }

    def list_market_candles(
        self,
        *,
        market: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        limit: int = 30000,
    ) -> list[dict[str, Any]]:
        clauses = [
            "market=?",
            "exchange=?",
            "symbol=?",
            "timeframe=?",
        ]
        params: list[Any] = [market, exchange, symbol.upper(), timeframe]
        if start_ms is not None:
            clauses.append("open_time_ms>=?")
            params.append(int(start_ms))
        if end_ms is not None:
            clauses.append("open_time_ms<=?")
            params.append(int(end_ms))
        params.append(min(max(int(limit), 1), 50000))
        with self._session() as db:
            rows = db.execute(
                f"""
                SELECT * FROM (
                    SELECT * FROM market_candles
                    WHERE {' AND '.join(clauses)}
                    ORDER BY open_time_ms DESC
                    LIMIT ?
                ) ORDER BY open_time_ms ASC
                """,
                params,
            ).fetchall()
        return [
            {
                "open_time": row["open_time_ms"],
                "close_time": row["close_time_ms"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "quote_volume": row["quote_volume"],
                "trades": row["trades"],
                "taker_buy_base_volume": row["taker_buy_base_volume"],
                "taker_buy_quote_volume": row["taker_buy_quote_volume"],
                "closed": True,
                "exchange": row["exchange"],
                "market_type": row["market"],
                "volume_unit": row["volume_unit"],
                "contract_volume": row["contract_volume"],
            }
            for row in rows
        ]

    def market_candle_stats(
        self,
        *,
        market: str | None = None,
        exchange: str | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["1=1"]
        params: list[Any] = []
        for column, value in (
            ("market", market),
            ("exchange", exchange),
            ("symbol", symbol.upper() if symbol else None),
            ("timeframe", timeframe),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        with self._session() as db:
            rows = db.execute(
                f"""
                SELECT market, exchange, symbol, timeframe,
                       COUNT(*) AS candle_count,
                       MIN(open_time_ms) AS oldest_open_time_ms,
                       MAX(open_time_ms) AS newest_open_time_ms
                FROM market_candles
                WHERE {' AND '.join(clauses)}
                GROUP BY market, exchange, symbol, timeframe
                ORDER BY exchange, market, symbol, timeframe
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

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

    def evaluation_samples(
        self,
        *,
        market: str,
        exchange: str,
        symbol: str,
        timeframe: str,
        max_rows: int = 30000,
    ) -> list[dict[str, Any]]:
        """Load chronological labeled samples for an explicitly chosen instrument."""
        with self._session() as db:
            rows = db.execute(
                """
                SELECT * FROM segment_samples
                WHERE market=? AND exchange=? AND symbol=? AND timeframe=?
                ORDER BY decision_time_ms ASC LIMIT ?
                """,
                (market, exchange, symbol.upper(), timeframe, max_rows),
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
        candidates = [
            item
            for item in (self._segment_row(row) for row in rows)
            if 0 < int((item.get("labels") or {}).get("future_end_ms", 0)) <= int(before_ms)
        ]
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
