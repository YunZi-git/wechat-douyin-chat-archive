"""自动增量更新：定时把本机新消息导入统一库。

只处理**已经落到本机**的数据：

* 微信：``data/wechat/decrypted/<账号>`` 下的新消息（按水位线增量）
* 抖音：引擎自己的 ``data/douyin/chat.db``（采集中断后下次导入补齐）

不会自动打开浏览器采集，也不会自动解密微信——那两步都需要用户主动触发。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from . import jobs, paths, services, settings

_stop = threading.Event()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_state: dict[str, Any] = {"job_id": "", "last_result": None, "running": False}


def status() -> dict[str, Any]:
    cfg = settings.load()
    enabled = bool(cfg.get("autosync_enabled"))
    interval = max(1, int(cfg.get("autosync_interval_minutes", 30) or 30))
    last = int(cfg.get("autosync_last_run", 0) or 0)
    return {
        "enabled": enabled,
        "interval_minutes": interval,
        "last_run": last,
        "next_run": (last + interval * 60) if enabled and last else 0,
        "running": _state["running"],
        "job_id": _state["job_id"],
        "last_result": _state["last_result"],
    }


def run_once(*, trigger: str = "auto") -> dict[str, Any]:
    """启动一次增量更新任务（不阻塞），返回任务信息。"""
    with _lock:
        if _state["running"]:
            return {"started": False, "reason": "已有增量更新在跑"}
        job = services.start_incremental_update(trigger=trigger)
        _state.update({"job_id": job.id, "running": True, "last_result": None})
        settings.save({"autosync_last_run": int(time.time())})
        threading.Thread(target=_watch, args=(job,), daemon=True,
                         name="autosync-watch").start()
        return {"started": True, "job": job.to_dict(with_log=False)}


def _watch(job: jobs.Job) -> None:
    while job.status in ("queued", "running"):
        time.sleep(1)
    with _lock:
        _state["running"] = False
        _state["last_result"] = {
            "status": job.status,
            "result": job.result,
            "finished_at": int(time.time()),
        }
        _state["job_id"] = ""


def _loop() -> None:
    while not _stop.wait(5):
        try:
            cfg = settings.load()
            if not cfg.get("autosync_enabled"):
                continue
            interval = max(1, int(cfg.get("autosync_interval_minutes", 30) or 30))
            last = int(cfg.get("autosync_last_run", 0) or 0)
            if last and time.time() - last < interval * 60:
                continue
            if services.heavy_busy():
                continue
            run_once(trigger="auto")
        except Exception:  # noqa: BLE001 - 定时器不能因为一次失败退出
            time.sleep(30)


def start_scheduler() -> None:
    if os.environ.get("CHATWORK_DISABLE_AUTOSYNC") == "1":
        return
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="autosync")
    _thread.start()


def stop_scheduler() -> None:
    _stop.set()


def sources_available() -> bool:
    from .importers import wechat as wechat_importer

    if wechat_importer.detect_accounts(paths.WECHAT_DECRYPTED):
        return True
    return paths.DOUYIN_DB.is_file()
