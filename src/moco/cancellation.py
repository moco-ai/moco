"""Simple in-process cancellation management for long-running orchestrator jobs.

This module is intentionally minimal and process-local. It provides a mapping
from job IDs (typically session IDs) to threading.Event instances that can be
used to cooperatively cancel long-running operations.

The design is shared between the Web UI (FastAPI) and CLI so both can use the
same cancellation mechanism without introducing a full job management system.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional


class OperationCancelled(Exception):
    """Raised when an operation is cancelled via a cancel event.

    Callers should catch this exception at the boundary of request handling
    (e.g. FastAPI/CLI handlers) and convert it into a user-visible
    "cancelled" status without treating it as an application error.
    """


# job_id -> threading.Event
_cancel_events: Dict[str, threading.Event] = {}
_lock = threading.Lock()


def create_cancel_event(job_id: str) -> threading.Event:
    """Create and register a cancel event for the given job.

    If an event already exists for the job_id it is returned as-is.
    """
    with _lock:
        event = _cancel_events.get(job_id)
        if event is None:
            event = threading.Event()
            _cancel_events[job_id] = event
        return event


def get_cancel_event(job_id: str) -> Optional[threading.Event]:
    """Get the cancel event for a job if it exists."""
    with _lock:
        return _cancel_events.get(job_id)


def request_cancel(job_id: str) -> bool:
    """Request cancellation for a job.

    Returns True if a cancel event existed and was set, False otherwise.
    """
    with _lock:
        event = _cancel_events.get(job_id)
        if event is None:
            return False
        event.set()
        return True


def clear_cancel_event(job_id: str) -> None:
    """Remove the cancel event for a job.

    This should be called after a job finishes or is cancelled to avoid
    unbounded growth of the internal mapping.
    """
    with _lock:
        _cancel_events.pop(job_id, None)


def check_cancelled(job_id: str) -> None:
    """Raise OperationCancelled if the job has been cancelled."""
    event = get_cancel_event(job_id)
    if event is not None and event.is_set():
        # If the event is set, clear it before raising the exception
        # (To avoid affecting subsequent requests)
        clear_cancel_event(job_id)
        raise OperationCancelled(f"Operation cancelled for job_id={job_id}")
