"""One contradiction run at a time, in the shared one-heavy-job slot (ADR-019 decision 11).

A run asks a local model one question per pair, which on a CPU-only machine takes minutes, so it
shares the slot with questionnaire runs (`jobs.py`): while either holds it, the other is refused
with `RunInProgressError` (HTTP 409). The slot is released in a `finally`, so a refusal inside the
run, an exception and a cancelled request (the caller went away) all free it.
"""

from grc_sidecar.contradictions.models import DetectionReport
from grc_sidecar.contradictions.run_jobs import ContradictionRunJobs, Detector, RunStatus
from grc_sidecar.jobs import JobSlot


class ContradictionRuns:
    """A run held open until it ends (`run`), or started and polled (`start`, `get`).

    Both ways share the one slot. Only `run` is the original one-response route; `start` and `get`
    are the start-then-poll pair (ADR-019 addendum, G40).
    """

    def __init__(self, detector: Detector, slot: JobSlot, model_name: str) -> None:
        self._detector = detector
        self._slot = slot
        self.model_name = model_name  # the configured generation model, shown in the response
        self._jobs = ContradictionRunJobs(detector, slot)

    async def start(self, *, scope: str | None, cap: int) -> str:
        """Take the slot and begin a run in a task. Returns its id; see `ContradictionRunJobs`."""
        return await self._jobs.start(scope=scope, cap=cap)

    def get(self, run_id: str) -> RunStatus:
        return self._jobs.get(run_id)

    async def aclose(self) -> None:
        """Stop a started run that is still going (the app is shutting down)."""
        await self._jobs.aclose()

    async def run(self, *, scope: str | None, cap: int) -> DetectionReport:
        """Take the slot (or raise RunInProgressError), detect, and always give the slot back."""
        self._slot.begin()
        try:
            return await self._detector.detect(scope=scope, cap=cap)
        finally:
            self._slot.end()
