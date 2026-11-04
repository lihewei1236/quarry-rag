"""CPU 密集任务专用线程池。"""
from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor

_executor: ThreadPoolExecutor | None = None


def get_cpu_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="quarry-cpu")
        atexit.register(shutdown_executor)
    return _executor


def shutdown_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=True)
        _executor = None