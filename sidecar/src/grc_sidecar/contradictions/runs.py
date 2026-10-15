"""One contradiction run at a time, in the shared one-heavy-job slot (ADR-019 decision 11).

A run asks a local model one question per pair, which on a CPU-only machine takes minutes, so it
shares the slot with questionnaire runs (`jobs.py`): while either holds it, the other is refused
with `RunInProgressError` (HTTP 409). The slot is released in a `finally`, so a refusal inside the
run, an exception and a cancelled request (the caller went away) all free it.
"""

from typing import Protocol

from grc_sidecar.contradictions.models import DetectionReport
from grc_sidecar.jobs import JobSlot


class Detecting(Protocol):
    """The part of `ContradictionDetector` a run uses (a held or failing fake in the tests)."""

    async def detect(self, *, scope: str | None, cap: int) -> DetectionReport: ...


class ContradictionRuns:
    def __init__(self, detector: Detecting, slot: JobSlot, model_name: str) -> None:
        self._detector = detector
        self._slot = slot
        self.model_name = model_name  # the configured generation model, shown in the response

    async def run(self, *, scope: str | None, cap: int) -> DetectionReport:
        """Take the slot (or raise RunInProgressError), detect, and always give the slot back."""
        self._slot.begin()
        try:
            return await self._detector.detect(scope=scope, cap=cap)
        finally:
            self._slot.end()
