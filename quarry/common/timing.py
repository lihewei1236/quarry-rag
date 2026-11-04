"""分段计时。全项目只有这一份。"""
from __future__ import annotations

import time
from contextlib import contextmanager


class StageTimer:
    """记录链路各阶段的耗时，单位毫秒。

        t = StageTimer()
        with t.stage("recall"):
            ...
        t.as_dict()        # {"recall_ms": 71.3, "total_ms": 72.0}
    """

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self.stages: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            # 同名阶段进入多次就累加；中间抛异常也照样记下耗时
            cost = (time.perf_counter() - start) * 1000
            self.stages[name] = self.stages.get(name, 0.0) + cost

    def mark_total(self) -> None:
        # total 单独测，不从各段相加：并发执行的段加起来会比实际耗时长
        self.stages["total"] = (time.perf_counter() - self._t0) * 1000

    def as_dict(self) -> dict[str, float]:
        if "total" not in self.stages:
            self.mark_total()
        return {f"{k}_ms": round(v, 1) for k, v in self.stages.items()}