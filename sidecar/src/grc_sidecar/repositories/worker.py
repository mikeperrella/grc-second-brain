"""The one thread that owns the SQLite connection (ADR-009, concurrency model).

A SQLite connection is created on this thread and, with `check_same_thread` left on, any other
thread that touches it gets an error instead of silently corrupting state. One worker thread also
serializes every write, so the repository needs no locks.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")

THREAD_NAME_PREFIX = "grc-index-db"


class SqliteWorker:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=THREAD_NAME_PREFIX)

    async def run(self, call: Callable[[], T]) -> T:
        """Run `call` on the worker thread and wait for it without blocking the event loop.

        If the awaiting task is cancelled, the thread still finishes the call, so a transaction
        either commits or rolls back whole. Nothing is left half applied.
        """
        return await asyncio.get_running_loop().run_in_executor(self._executor, call)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)
