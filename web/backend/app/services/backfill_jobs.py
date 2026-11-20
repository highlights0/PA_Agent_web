"""In-memory progress snapshots for bounded batch market-data backfills."""
from __future__ import annotations

import copy
import threading
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BackfillJobStore:
    def __init__(self, max_jobs: int = 100) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._max_jobs = max_jobs

    def create(self, owner: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = _now()
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "owner": owner,
                "status": "queued",
                "total": len(items),
                "completed": 0,
                "current": None,
                "results": [],
                "created_at": now,
                "updated_at": now,
                "finished_at": None,
            }
            self._prune()
            return self._public(self._jobs[job_id])

    def _prune(self) -> None:
        if len(self._jobs) <= self._max_jobs:
            return
        finished = sorted(
            (job.get("updated_at", ""), key)
            for key, job in self._jobs.items()
            if job.get("status") in {"completed", "failed"}
        )
        for _, key in finished[: max(0, len(self._jobs) - self._max_jobs)]:
            self._jobs.pop(key, None)

    def set_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["status"] = "running"
            job["updated_at"] = _now()

    def set_current(self, job_id: str, item: dict[str, Any], index: int) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["current"] = {**item, "index": index}
            job["updated_at"] = _now()

    def complete_item(self, job_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job["results"].append(copy.deepcopy(result))
            job["completed"] += 1
            job["updated_at"] = _now()

    def finish(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            successes = sum(1 for item in job["results"] if item.get("status") == "completed")
            if successes == job["total"]:
                job["status"] = "completed"
            elif successes:
                job["status"] = "partial"
            else:
                job["status"] = "failed"
            job["current"] = None
            job["finished_at"] = _now()
            job["updated_at"] = job["finished_at"]

    def get(self, job_id: str, owner: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.get("owner") != owner:
                return None
            return self._public(job)

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(value)
            for key, value in job.items()
            if key != "owner"
        }
