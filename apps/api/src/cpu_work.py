"""Bound admission to CPU work shared by public API routes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

CPU_WORK_CONCURRENCY = 2
CPU_WORK_WAIT_SECONDS = 5.0

_cpu_semaphore = asyncio.Semaphore(CPU_WORK_CONCURRENCY)
_cpu_tasks: set[asyncio.Task[Any]] = set()
_T = TypeVar("_T")


class CpuWorkBusy(Exception):
    """No CPU worker slot became available within the admission deadline."""


async def run_cpu_bound(fn: Callable[..., _T], *args: Any) -> _T:
    semaphore = _cpu_semaphore
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=CPU_WORK_WAIT_SECONDS)
    except TimeoutError as exc:
        raise CpuWorkBusy from exc

    # Threads are deliberate: Pillow and most rate_all work release the GIL
    # often enough for the event loop to keep serving between thread switches.
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    _cpu_tasks.add(task)

    def finished(completed: asyncio.Task[_T]) -> None:
        _cpu_tasks.discard(completed)
        semaphore.release()
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(finished)
    # A cancelled request must retain its slot until the running thread exits.
    return await asyncio.shield(task)
