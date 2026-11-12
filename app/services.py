"""对外服务：源数据发现、微信解密、抖音采集。

这一层负责“把外部工具串起来”，业务逻辑都留在导入器里。
所有耗时操作都通过 :mod:`app.jobs` 跑在后台线程，前端轮询日志。
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import jobs, paths
from .importers import douyin as douyin_importer
from .importers import wechat as wechat_importer

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _clean(line: str) -> str:
    return _ANSI.sub("", line).rstrip()


def _base_env() -> dict[str, str]:
    env = dict(os.environ)
    # 中文 Windows 上子进程默认 GBK，会让中文输出直接崩
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _run_stream(cmd: list[str], *, cwd: Path, env: dict[str, str], job: jobs.Job) -> int:
    job.write("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            job.write(_clean(line))
            if job.cancelled():
                proc.terminate()
                break
    finally:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.returncode or 0


# ── 环境探测 ────────────────────────────────────────────────────────

def wechat_accounts() -> list[dict[str, Any]]:
    """已经解密好的微信账号目录。"""
    root = paths.WECHAT_DECRYPTED
    items: list[dict[str, Any]] = []
    for account in wechat_importer.detect_accounts(root):
        msg_dir = account / "message"
        dbs = sorted(msg_dir.glob("message_*.db"))
        items.append({
            "path": str(account),
            "name": account.name,
            "self_wxid": wechat_importer.self_wxid_from_dir(account),
            "shards": len(dbs),
            "size": sum(p.stat().st_size for p in dbs),
            "mtime": int(max((p.stat().st_mtime for p in dbs), default=0)),
        })
    return items


def douyin_source() -> dict[str, Any]:
    db_path = paths.DOUYIN_DB
    media = paths.DOUYIN_MEDIA
    info: dict[str, Any] = {
        "db": str(db_path),
        "exists": db_path.exists(),
        "media_dir": str(media),
        "size": 0,
        "conversations": 0,
        "messages": 0,
        "voice": 0,
    }
    if db_path.exists():
        info["size"] = db_path.stat().st_size
        try:
            import sqlite3

            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                info["conversations"] = con.execute(
                    "SELECT COUNT(*) FROM conversations"
                ).fetchone()[0]
                info["messages"] = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                info["voice"] = con.execute(
                    "SELECT COUNT(*) FROM voice_transcriptions WHERE status='success'"
                ).fetchone()[0]
            finally:
                con.close()
        except Exception:  # noqa: BLE001 - 库可能正在被引擎写
            pass
    return info


def engine_status() -> dict[str, Any]:
    siwx_run = paths.SIWX_DIR / "run.py"
    douyin_extract = paths.DOUYIN_ENGINE_DIR / "extract.py"
    return {
        "wechat": {
            "bundled": siwx_run.exists(),
            "path": str(paths.SIWX_DIR),
        },
        "douyin": {
            "bundled": douyin_extract.exists(),
            "path": str(paths.DOUYIN_ENGINE_DIR),
            # 采集需要 Playwright；没装也能导入已有数据库
            "playwright": importlib.util.find_spec("playwright") is not None,
        },
    }


# ── 微信：解密 + 导入 ────────────────────────────────────────────────

def start_wechat_decrypt(*, out_dir: Path | None = None) -> jobs.Job:
    out = out_dir or paths.WECHAT_DECRYPTED

    def target(job: jobs.Job) -> dict[str, Any]:
        run_py = paths.SIWX_DIR / "run.py"
        if not run_py.exists():
            raise FileNotFoundError("内置的 SIWX 解密组件缺失")
        job.write("[i] 需要微信正在运行并已登录")
        code = _run_stream(
            [sys.executable, str(run_py), "auto", "--out", str(out)],
            cwd=paths.SIWX_DIR,
            env=_base_env(),
            job=job,
        )
        accounts = wechat_importer.detect_accounts(out)
        job.write(f"[i] 解密结束（退出码 {code}），发现 {len(accounts)} 个账号目录")
        return {"exit_code": code, "accounts": [a.name for a in accounts]}

    return jobs.start("wechat-decrypt", "微信解密", target)


def start_wechat_import(account_dir: str | Path | None = None) -> jobs.Job:
    accounts = wechat_importer.detect_accounts(paths.WECHAT_DECRYPTED)
    if account_dir:
        chosen = [Path(account_dir)]
    else:
        if not accounts:
            raise FileNotFoundError(
                "还没有解密好的微信数据，请先点「解密并导入」"
            )
        chosen = accounts

    def target(job: jobs.Job) -> dict[str, Any]:
        reports = []
        for account in chosen:
            job.write(f"[i] 导入 {account.name}")
            report = wechat_importer.import_account(
                account,
                progress=lambda p: (job.update(p), job.write(
                    f"[+] {p.get('stage','')} {p.get('shard','')} "
                    f"已写入 {p.get('messages', 0)} 条"
                )),
            )
            reports.append(report)
            job.write(
                f"[✓] {account.name}: 会话 +{report['conversations_added']}, "
                f"消息 +{report['messages_added']}"
            )
        return {"reports": reports}

    return jobs.start("wechat-import", "导入微信聊天记录", target)


def start_wechat_refresh() -> jobs.Job:
    """一键：先解密，再把解出来的账号导入统一库。"""

    def target(job: jobs.Job) -> dict[str, Any]:
        run_py = paths.SIWX_DIR / "run.py"
        if not run_py.exists():
            raise FileNotFoundError("内置的 SIWX 解密组件缺失")
        job.write("[i] 第一步：从微信进程取密钥并解密数据库")
        job.write("[i] 需要微信正在运行并已登录")
        code = _run_stream(
            [sys.executable, str(run_py), "auto", "--out", str(paths.WECHAT_DECRYPTED)],
            cwd=paths.SIWX_DIR,
            env=_base_env(),
            job=job,
        )
        accounts = wechat_importer.detect_accounts(paths.WECHAT_DECRYPTED)
        if not accounts:
            raise RuntimeError(
                f"没有解出任何账号（退出码 {code}）。确认微信已登录，"
                "并查看上面的日志。"
            )
        job.write(f"[i] 第二步：导入 {len(accounts)} 个账号")
        reports = []
        for account in accounts:
            if job.cancelled():
                break
            job.write(f"[i] 导入 {account.name}")
            report = wechat_importer.import_account(
                account,
                progress=lambda p: job.update(p),
            )
            reports.append(report)
            job.write(
                f"[✓] {account.name}: 会话 +{report['conversations_added']}, "
                f"消息 +{report['messages_added']}"
            )
        return {"exit_code": code, "reports": reports}

    return jobs.start("wechat-refresh", "微信：解密并导入", target)


# ── 抖音：采集 + 导入 ────────────────────────────────────────────────

def start_douyin_import(chat_db: str | Path | None = None, *,
                        media_dir: str | Path | None = None,
                        copy_media: bool = True) -> jobs.Job:
    source = Path(chat_db) if chat_db else paths.DOUYIN_DB
    if media_dir:
        media = Path(media_dir)
    else:
        # 外部 chat.db 通常把媒体放在同级 media/；引擎自己的库则统一在 data/media/douyin
        sibling = source.parent / "media"
        media = sibling if sibling.is_dir() and source != paths.DOUYIN_DB else paths.DOUYIN_MEDIA

    def target(job: jobs.Job) -> dict[str, Any]:
        report = douyin_importer.import_chat_db(
            source,
            media_src=media,
            copy_media=copy_media,
            progress=lambda p: (job.update(p), job.write(
                f"[+] {p.get('stage','')} 已写入 {p.get('messages', 0)} 条"
            )),
        )
        job.write(
            f"[✓] 会话 +{report['conversations_added']}, 消息 +{report['messages_added']}, "
            f"跳过 {report['messages_skipped']}"
        )
        return {"report": report}

    return jobs.start("douyin-import", "导入抖音聊天记录", target)


def start_douyin_collect(*, filters: str | None = None, incremental: bool = True) -> jobs.Job:
    def target(job: jobs.Job) -> dict[str, Any]:
        script = paths.DOUYIN_ENGINE_DIR / "extract.py"
        if not script.exists():
            raise FileNotFoundError("内置的抖音采集组件缺失")
        if importlib.util.find_spec("playwright") is None:
            raise RuntimeError(
                "没有安装 Playwright，无法启动浏览器采集。"
                "可以改为「导入已有数据库」，或安装采集依赖后重试。"
            )
        env = _base_env()
        env["CHATWORK_DOUYIN_DATA"] = str(paths.DOUYIN_DIR)
        env["CHATWORK_DOUYIN_MEDIA"] = str(paths.DOUYIN_MEDIA)
        env["HEADLESS"] = "false"
        cmd = [sys.executable, str(script)]
        if filters:
            cmd += ["--filter", filters]
        if incremental:
            cmd.append("--incremental")
        job.write("[i] 会弹出浏览器，请在窗口里扫码登录抖音")
        code = _run_stream(cmd, cwd=paths.DOUYIN_ENGINE_DIR, env=env, job=job)
        job.write(f"[i] 采集结束（退出码 {code}）")
        return {"exit_code": code}

    return jobs.start("douyin-collect", "采集抖音聊天记录", target)


def start_douyin_refresh(*, filters: str | None = None,
                         incremental: bool = True) -> jobs.Job:
    """采集完顺手导入统一库。"""

    def target(job: jobs.Job) -> dict[str, Any]:
        script = paths.DOUYIN_ENGINE_DIR / "extract.py"
        if importlib.util.find_spec("playwright") is None:
            raise RuntimeError("没有安装 Playwright，无法启动浏览器采集")
        env = _base_env()
        env["CHATWORK_DOUYIN_DATA"] = str(paths.DOUYIN_DIR)
        env["CHATWORK_DOUYIN_MEDIA"] = str(paths.DOUYIN_MEDIA)
        job.write("[i] 第一步：打开浏览器采集（需要扫码登录）")
        cmd = [sys.executable, str(script)]
        if filters:
            cmd += ["--filter", filters]
        if incremental:
            cmd.append("--incremental")
        code = _run_stream(cmd, cwd=paths.DOUYIN_ENGINE_DIR, env=env, job=job)
        if code != 0:
            raise RuntimeError(f"采集失败（退出码 {code}），详见上方日志")
        job.write("[i] 第二步：导入统一库")
        report = douyin_importer.import_chat_db(
            paths.DOUYIN_DB,
            media_src=paths.DOUYIN_MEDIA,
            copy_media=False,
            progress=lambda p: job.update(p),
        )
        job.write(
            f"[✓] 会话 +{report['conversations_added']}, 消息 +{report['messages_added']}"
        )
        return {"exit_code": code, "report": report}

    return jobs.start("douyin-refresh", "抖音：采集并导入", target)
