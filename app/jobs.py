"""后台任务：导入、解密这类耗时操作都放到线程里跑，前端轮询进度。

设计得很土但很稳：一个进程内的任务表 + 线程，不引入任何消息队列。
任务日志只保留最近 N 行，避免长时间运行把内存吃满。
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable

MAX_LOG_LINES = 800
MAX_JOBS = 40


class JobCancelled(Exception):
    """任务体主动抛这个表示「用户取消了」，不再当作失败。"""


class Job:
    def __init__(self, kind: str, title: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.title = title
        self.status = "queued"        # queued | running | ok | failed | cancelled
        self.log: list[str] = []
        self.progress: dict[str, Any] = {}
        self.result: Any = None
        self.error: str | None = None
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.cancel_requested = False
        self._lock = threading.Lock()

    # ── 供任务体调用 ──────────────────────────────────────────────
    def write(self, line: str) -> None:
        text = str(line).rstrip("\n")
        if not text:
            return
        with self._lock:
            self.log.append(f"{time.strftime('%H:%M:%S')} {text}")
            if len(self.log) > MAX_LOG_LINES:
                del self.log[: len(self.log) - MAX_LOG_LINES]

    def update(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.progress = payload

    def cancelled(self) -> bool:
        return self.cancel_requested

    # ── 对外序列化 ────────────────────────────────────────────────
    def to_dict(self, *, with_log: bool = True) -> dict[str, Any]:
        with self._lock:
            data = {
                "id": self.id,
                "kind": self.kind,
                "title": self.title,
                "status": self.status,
                "progress": dict(self.progress),
                "result": self.result,
                "error": self.error,
                "started_at": int(self.started_at),
                "finished_at": int(self.finished_at) if self.finished_at else None,
                "cancel_requested": self.cancel_requested,
            }
            if with_log:
                data["log"] = list(self.log[-200:])
            return data


_jobs: dict[str, Job] = {}
_order: list[str] = []
_lock = threading.Lock()


def _register(job: Job) -> None:
    with _lock:
        _jobs[job.id] = job
        _order.append(job.id)
        while len(_order) > MAX_JOBS:
            oldest = _order.pop(0)
            old = _jobs.get(oldest)
            if old and old.status in ("ok", "failed", "cancelled"):
                _jobs.pop(oldest, None)


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        ids = list(reversed(_order))[:limit]
        return [_jobs[i].to_dict(with_log=False) for i in ids if i in _jobs]


def cancel(job_id: str) -> bool:
    job = get(job_id)
    if not job or job.status not in ("queued", "running"):
        return False
    job.cancel_requested = True
    job.write("[!] 收到取消请求，正在收尾…")
    return True


def start(kind: str, title: str, target: Callable[[Job], Any]) -> Job:
    job = Job(kind, title)
    _register(job)

    def runner() -> None:
        job.status = "running"
        try:
            result = target(job)
            if job.cancel_requested:
                job.status = "cancelled"
            else:
                job.status = "ok"
            job.result = result
        except JobCancelled:
            job.status = "cancelled"
            job.write("[i] 已取消（已完成的部分保留）")
        except Exception as exc:  # noqa: BLE001 - 任务失败要把原因留给前端
            job.error = f"{type(exc).__name__}: {exc}"
            job.status = "failed"
            job.write("[x] " + job.error)
            job.write(traceback.format_exc(limit=6))
        finally:
            job.finished_at = time.time()
            with job._lock:
                job.progress = {**job.progress, "done": True}

    threading.Thread(target=runner, name=f"job-{job.id}", daemon=True).start()
    return job
