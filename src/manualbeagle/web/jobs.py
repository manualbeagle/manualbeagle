"""Background jobs for the web UI: `batch` and `sync` run a row at a time in a thread while the page polls.

One job runs at a time, because every job shares the library and the polite client's per-site turns, and a
second batch would only wait behind the first. Jobs live in memory and are gone when the server stops.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any


class Busy(Exception):
    """Another job is still running."""


class Cancelled(Exception):
    """The user asked the job to stop."""


@dataclass
class Job:
    """One background job: its rows so far, its state, and what it ended with."""

    id: str
    kind: str  # batch | sync | attach
    total: int = 0
    rows: list[dict] = field(default_factory=list)
    state: str = "running"  # running | done | failed | cancelled
    error: str = ""
    summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)  # shown to the page
    data: dict[str, Any] = field(default_factory=dict, repr=False)  # kept on the server (sync's plans)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict:
        """The job as JSON for the page."""
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "total": self.total,
            "done": len(self.rows),
            "rows": self.rows,
            "error": self.error,
            "summary": self.summary,
            "started": self.started,
            "finished": self.finished,
            **self.extra,
        }


# A job's work: given the job, yield one JSON-ready dict per row.
Work = Callable[[Job], Iterable[dict]]


class Jobs:
    """The jobs this server has run, at most one of them running."""

    def __init__(self, keep: int = 20) -> None:
        self.keep = keep
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        """The job with this id, or None."""
        return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        """Every job remembered, oldest first."""
        return list(self._jobs.values())

    def running(self) -> Job | None:
        """The job that is still running, or None."""
        return next((j for j in self._jobs.values() if j.state == "running"), None)

    def start(self, kind: str, total: int, work: Work, summarise: Callable[[Job], str] | None = None) -> Job:
        """Start `work` in a thread and return its Job. Raises Busy while another job runs.

        `work` yields rows; between rows the job stops if it was cancelled. `summarise` makes the summary line
        from the finished job.
        """
        with self._lock:
            if self.running():
                raise Busy("another job is still running; wait for it or cancel it")
            job = Job(id=secrets.token_hex(6), kind=kind, total=total)
            self._jobs[job.id] = job
            for old in list(self._jobs)[: -self.keep]:
                if self._jobs[old].state != "running":
                    del self._jobs[old]

        def go() -> None:
            state = "done"
            try:
                for row in work(job):
                    job.rows.append(row)
                    if job.cancel.is_set():
                        raise Cancelled()
            except Cancelled:
                state = "cancelled"
            except Exception as e:  # the page shows it; the server keeps running
                state, job.error = "failed", f"{type(e).__name__}: {e}"
            if summarise:
                try:
                    job.summary = summarise(job)
                except Exception:  # a summary never hides the rows
                    job.summary = ""
            job.finished = time.time()
            job.state = state  # last, so a page that sees the end also sees the summary

        threading.Thread(target=go, name=f"manualbeagle-{kind}-{job.id}", daemon=True).start()
        return job
