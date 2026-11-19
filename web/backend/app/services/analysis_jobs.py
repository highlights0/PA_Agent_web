"""In-memory analysis job snapshots for real-time UI polling.

The PA orchestrator remains synchronous and unchanged. Its callbacks update this
thread-safe store while the worker thread runs. Job snapshots are intentionally
ephemeral (process memory); durable history is a separate future feature.
"""
from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalysisJobStore:
    def __init__(self, max_jobs: int = 200, max_events_per_job: int = 600) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._max_jobs = max_jobs
        self._max_events = max_events_per_job

    def create(self, owner_ip: str, request_data: dict[str, Any]) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = _now()
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "owner_ip": owner_ip,
                "status": "queued",
                "request": dict(request_data),
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "finished_at": None,
                "trace": [],
                "result": None,
                "details": None,
                "error": None,
            }
            self._prune()
            return self._public(self._jobs[job_id])

    def _prune(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        completed = sorted(
            (
                (job.get("updated_at") or "", job_id)
                for job_id, job in self._jobs.items()
                if job.get("status") in {"completed", "failed"}
            )
        )
        for _, job_id in completed[: max(0, len(self._jobs) - self._max_jobs)]:
            self._jobs.pop(job_id, None)

    def set_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            now = _now()
            job["status"] = "running"
            job["started_at"] = job["started_at"] or now
            job["updated_at"] = now

    def append_event(self, job_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["trace"].append(copy.deepcopy(event))
            if len(job["trace"]) > self._max_events:
                del job["trace"][: len(job["trace"]) - self._max_events]
            job["updated_at"] = _now()

    def complete(
        self,
        job_id: str,
        *,
        result: dict[str, Any],
        details: dict[str, Any],
        trace: list[dict[str, Any]],
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = result.get("status", "completed")
            job["result"] = copy.deepcopy(result)
            job["details"] = copy.deepcopy(details)
            # Use the authoritative final collector snapshot, avoiding a race
            # between the final event callback and task completion.
            job["trace"] = copy.deepcopy(trace[-self._max_events :])
            job["finished_at"] = _now()
            job["updated_at"] = job["finished_at"]

    def fail(self, job_id: str, error: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "failed"
            job["error"] = copy.deepcopy(error)
            job["finished_at"] = _now()
            job["updated_at"] = job["finished_at"]

    def get(self, job_id: str, owner_ip: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.get("owner_ip") != owner_ip:
                return None
            return self._public(job)

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in job.items()
            if key != "owner_ip"
        }
