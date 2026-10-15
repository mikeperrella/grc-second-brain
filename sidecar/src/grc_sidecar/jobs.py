"""The one-heavy-job slot, shared by every job that holds the model (ADR-014, ADR-019 decision 11).

A questionnaire run and a contradiction run both ask a local model one question after another, and
a CPU-only machine can do one such job at a time. They share one `JobSlot`: whoever calls `begin()`
first holds it until `end()`, and a second `begin()` is refused with `RunInProgressError` (HTTP 409)
instead of queueing, so nothing waits unseen.

The slot is a flag, not a lock: `begin()` and `end()` never wait, and all callers run on the one
event loop, so a plain bool cannot be raced. The caller must call `end()` in a `finally`, so a
refusal, an exception and a cancelled request all release the slot.
"""

from grc_sidecar.errors import RunInProgressError


class JobSlot:
    def __init__(self) -> None:
        self._busy = False

    @property
    def busy(self) -> bool:
        return self._busy

    def begin(self) -> None:
        """Take the slot, or raise RunInProgressError if a job holds it."""
        if self._busy:
            raise RunInProgressError
        self._busy = True

    def end(self) -> None:
        """Release the slot. Releasing a free slot is harmless."""
        self._busy = False
