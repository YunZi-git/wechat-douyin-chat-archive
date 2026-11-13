"""对外服务：源数据发现、微信解密、抖音采集。

这一层负责“把外部工具串起来”，业务逻辑都留在导入器里。
所有耗时操作都通过 :mod:`app.jobs` 跑在后台线程，前端轮询日志。

两条硬约束：

* **一次只跑一个重任务**。解密/采集/导入都会大量写 SQLite，并发会把
  「database is locked」甩到用户脸上，所以用一把进程内互斥锁串行化。
* **取消要真的生效**。长任务把自己的 ``job.cancelled`` 传给导入器，
  导入器每处理若干条就检查一次，抛 ``JobCancelled`` 收尾。
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Iterator

from . import asr, db, jobs, paths, settings, wechat_media
from .importers import douyin as douyin_importer
from .importers import wechat as wechat_importer

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# 重任务串行锁：避免两个导入同时写库
_HEAVY_LOCK = threading.Lock()


def _clean(line: str) -> str:
    return _ANSI.sub("", line).rstrip()


def _base_env() -> dict[str, str]:
    env = dict(os.environ)
    # 中文 Windows 上子进程默认 GBK，会让中文输出直接崩
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


@contextlib.contextmanager
def _heavy_slot(job: jobs.Job, label: str) -> Iterator[None]:
    """拿到执行重任务的独占名额；排队时也能被取消。"""
    waited = False
    while not _HEAVY_LOCK.acquire(timeout=0.5):
        if not waited:
            job.write("[i] 已有导入/采集任务在跑，排队等待…（可点取消）")
            waited = True
        if job.cancelled():
            raise jobs.JobCancelled()
    try:
        if waited:
            job.write(f"[i] 轮到执行：{label}")
        yield
    finally:
        _HEAVY_LOCK.release()


def _run_stream(cmd: list[str], *, cwd: Path, env: dict[str, str], job: jobs.Job,
                on_line: Callable[[str], None] | None = None) -> int:
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
            cleaned = _clean(line)
            job.write(cleaned)
            if on_line:
                try:
                    on_line(cleaned)
                except Exception:  # noqa: BLE001 - 进度解析不能影响采集
                    pass
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
    info: dict[str, Any] = {
        # 接口只暴露相对路径，避免把本机用户名带进截图/日志
        "db": f"{paths.display_data_dir()}douyin/chat.db",
        "exists": db_path.exists(),
        "media_dir": f"{paths.display_data_dir()}media/douyin/",
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


EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def _edge_path() -> str | None:
    for candidate in EDGE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _chromium_installed() -> bool:
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    roots = [Path(base)] if base and base not in ("0", "1") else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "ms-playwright")
    for root in roots:
        try:
            if root.is_dir() and any(root.glob("chromium-*")):
                return True
        except OSError:
            continue
    return False


def browser_status() -> dict[str, Any]:
    """抖音采集用哪个浏览器。

    优先系统 Edge（Win10/11 自带，零下载）；没有才用 Playwright 的 Chromium。
    """
    playwright = importlib.util.find_spec("playwright") is not None
    edge = _edge_path()
    chromium = _chromium_installed()
    forced = os.environ.get("CHATWORK_BROWSER_CHANNEL")
    if forced is not None:
        channel = forced.strip()
    else:
        channel = "msedge" if edge else ("chromium" if chromium else "")
    return {
        "playwright": playwright,
        "edge": edge is not None,
        "edge_path": edge,
        "chromium": chromium,
        "channel": channel,
        "ready": playwright and bool(edge or chromium),
    }


def _douyin_unified_counts() -> dict[str, dict[str, Any]]:
    with db.session(readonly=True) as conn:
        try:
            rows = conn.execute(
                """SELECT c.title AS name, c.kind AS kind,
                          COUNT(m.id) AS message_count,
                          COALESCE(MAX(m.ts), 0) AS last_message_time
                     FROM conversations c
                     LEFT JOIN messages m ON m.conversation_id = c.id
                    WHERE c.platform = 'douyin'
                    GROUP BY c.id
                    ORDER BY last_message_time DESC, name"""
            ).fetchall()
        except Exception:  # noqa: BLE001 - 统一库可能还没建好
            rows = []
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = (r["name"] or "").strip()
        if name:
            out[name] = {
                "name": name,
                "message_count": int(r["message_count"] or 0),
                "last_message_time": int(r["last_message_time"] or 0),
                "kind": "group" if r["kind"] == "group" else "private",
            }
    return out


def douyin_conversations() -> dict[str, Any]:
    """给「采集范围」勾选用的抖音会话列表。

    优先级：discovery 拉到的完整名单（最新，含未采集的会话）→ 引擎 chat.db →
    统一库里已导入的会话。discovery 名单里的会话计数会从统一库补齐。
    """
    engine_items: list[dict[str, Any]] = []
    db_path = paths.DOUYIN_DB
    if db_path.exists():
        try:
            import sqlite3

            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            try:
                rows = con.execute(
                    "SELECT name, message_count, last_message_time, conv_type, conv_id"
                    " FROM conversations ORDER BY last_message_time DESC, name"
                ).fetchall()
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 - 库可能正在被采集写入
            return {"items": [], "total": 0, "available": False, "error": str(exc)}
        for row in rows:
            name = (row["name"] or "").strip()
            if name:
                engine_items.append({
                    "name": name,
                    "message_count": int(row["message_count"] or 0),
                    "last_message_time": int(row["last_message_time"] or 0),
                    "kind": "group" if str(row["conv_id"]).isdigit() else "private",
                })

    counts = _douyin_unified_counts()
    engine_by_name = {item["name"]: item for item in engine_items}

    # 最新拉到的完整名单优先；否则采集后会只剩引擎库里已采集的 1 个会话
    discovered = paths.DOUYIN_DIR / "conversations_list.json"
    if discovered.is_file():
        try:
            payload = json.loads(discovered.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        names: list[str] = []
        for item in payload.get("items") or []:
            name = str(item.get("nickname") or item.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
        if names:
            items = []
            for name in names:
                base = counts.get(name) or engine_by_name.get(name) or {}
                items.append({
                    "name": name,
                    "message_count": int(base.get("message_count") or 0),
                    "last_message_time": int(base.get("last_message_time") or 0),
                    "kind": base.get("kind") or "unknown",
                })
            return {"items": items, "total": len(items), "available": True,
                    "source": "discovered"}

    if engine_items:
        for item in engine_items:
            base = counts.get(item["name"])
            if base:
                item["message_count"] = max(item["message_count"], base["message_count"])
        return {"items": engine_items, "total": len(engine_items),
                "available": True, "source": "engine"}

    # 回退：统一库里已经导入的抖音会话
    with db.session(readonly=True) as conn:
        try:
            rows = conn.execute(
                """SELECT c.title AS name, c.kind AS kind,
                          COUNT(m.id) AS message_count,
                          COALESCE(MAX(m.ts), 0) AS last_message_time
                     FROM conversations c
                     LEFT JOIN messages m ON m.conversation_id = c.id
                    WHERE c.platform = 'douyin'
                    GROUP BY c.id
                    ORDER BY last_message_time DESC, name"""
            ).fetchall()
        except Exception:  # noqa: BLE001 - 统一库还没建好
            rows = []
    items = [
        {
            "name": (r["name"] or "").strip(),
            "message_count": int(r["message_count"] or 0),
            "last_message_time": int(r["last_message_time"] or 0),
            "kind": "group" if r["kind"] == "group" else "private",
        }
        for r in rows
        if (r["name"] or "").strip()
    ]
    return {"items": items, "total": len(items), "available": bool(items),
            "source": "unified" if items else "none"}


def _douyin_filter(mode: str, conversations: list[str] | None) -> str | None:
    """把界面上的「范围」翻译成引擎的 --filter（逗号分隔的精确昵称）。

    * all     → None（引擎默认全量）
    * include → 勾选的昵称
    * exclude → 本地已知昵称里去掉勾选的
    """
    selected = [str(n).strip() for n in (conversations or []) if str(n).strip()]
    selected = list(dict.fromkeys(selected))
    if mode == "all":
        return None
    if not selected:
        raise ValueError("请先勾选至少一个会话，或把范围改成「全部会话」")
    if len(selected) > 500:
        raise ValueError("一次最多选择 500 个会话")
    if mode == "include":
        return ",".join(selected)

    if mode == "exclude":
        known = [item["name"] for item in douyin_conversations()["items"]]
        if not known:
            raise ValueError("本地还没有会话列表，无法使用「排除」模式；请先全量采集一次")
        keep = [name for name in known if name not in set(selected)]
        if not keep:
            raise ValueError("排除后没有剩下任何会话")
        return ",".join(keep)
    raise ValueError(f"未知的采集范围：{mode}")


def engine_status() -> dict[str, Any]:
    siwx_run = paths.SIWX_DIR / "run.py"
    douyin_extract = paths.DOUYIN_ENGINE_DIR / "extract.py"
    browser = browser_status()
    return {
        "wechat": {
            "bundled": siwx_run.exists(),
            "path": "engines/siwx",
        },
        "douyin": {
            "bundled": douyin_extract.exists(),
            "path": "engines/douyin",
            **{k: v for k, v in browser.items() if k != "edge_path"},
        },
    }


# ── 微信：解密 + 导入 ────────────────────────────────────────────────

def _siwx_auto(job: jobs.Job, out_dir: Path) -> int:
    run_py = paths.SIWX_DIR / "run.py"
    if not run_py.exists():
        raise FileNotFoundError("内置的 SIWX 解密组件缺失")
    job.write("[i] 需要微信正在运行并已登录")
    return _run_stream(
        [sys.executable, str(run_py), "auto", "--out", str(out_dir)],
        cwd=paths.SIWX_DIR,
        env=_base_env(),
        job=job,
    )


def start_wechat_decrypt(*, out_dir: Path | None = None) -> jobs.Job:
    out = out_dir or paths.WECHAT_DECRYPTED

    def target(job: jobs.Job) -> dict[str, Any]:
        with _heavy_slot(job, "微信解密"):
            code = _siwx_auto(job, out)
            accounts = wechat_importer.detect_accounts(out)
            job.write(f"[i] 解密结束（退出码 {code}），发现 {len(accounts)} 个账号目录")
            return {"exit_code": code, "accounts": [a.name for a in accounts]}

    return jobs.start("wechat-decrypt", "微信解密", target)


def start_wechat_import(account_dir: str | Path | None = None,
                        incremental: bool = False) -> jobs.Job:
    accounts = wechat_importer.detect_accounts(paths.WECHAT_DECRYPTED)
    if account_dir:
        chosen = [Path(account_dir)]
    else:
        if not accounts:
            raise FileNotFoundError("还没有解密好的微信数据，请先点「解密并导入」")
        chosen = accounts

    def target(job: jobs.Job) -> dict[str, Any]:
        with _heavy_slot(job, "导入微信"):
            reports = []
            for account in chosen:
                job.write(f"[i] 导入 {account.name}")
                report = wechat_importer.import_account(
                    account,
                    incremental=incremental,
                    should_cancel=job.cancelled,
                    progress=lambda p: (job.update(p), job.write(
                        f"[+] {p.get('stage', '')} {p.get('shard', '')} "
                        f"已写入 {p.get('messages', 0)} 条"
                    )),
                )
                reports.append(report)
                job.write(
                    f"[✓] {account.name}: 会话 +{report['conversations_added']}, "
                    f"消息 +{report['messages_added']}"
                )
            auto = asr.auto_transcribe(job)
            return {"reports": reports, "auto_transcribe": auto}

    return jobs.start("wechat-import", "导入微信聊天记录", target)


def start_wechat_refresh(incremental: bool = False) -> jobs.Job:
    """一键：先解密，再把解出来的账号导入统一库。"""

    def target(job: jobs.Job) -> dict[str, Any]:
        with _heavy_slot(job, "微信解密并导入"):
            job.write("[i] 第一步：从微信进程取密钥并解密数据库")
            code = _siwx_auto(job, paths.WECHAT_DECRYPTED)
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
                    raise jobs.JobCancelled()
                job.write(f"[i] 导入 {account.name}")
                report = wechat_importer.import_account(
                    account, incremental=incremental,
                    should_cancel=job.cancelled, progress=job.update,
                )
                reports.append(report)
                job.write(
                    f"[✓] {account.name}: 会话 +{report['conversations_added']}, "
                    f"消息 +{report['messages_added']}"
                )
            auto = asr.auto_transcribe(job)
            return {"exit_code": code, "reports": reports, "auto_transcribe": auto}

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
        with _heavy_slot(job, "导入抖音"):
            report = douyin_importer.import_chat_db(
                source,
                media_src=media,
                copy_media=copy_media,
                should_cancel=job.cancelled,
                progress=lambda p: (job.update(p), job.write(
                    f"[+] {p.get('stage', '')} 已写入 {p.get('messages', 0)} 条"
                )),
            )
            job.write(
                f"[✓] 会话 +{report['conversations_added']}, 消息 +{report['messages_added']}, "
                f"跳过 {report['messages_skipped']}"
            )
            auto = asr.auto_transcribe(job)
            return {"report": report, "auto_transcribe": auto}

    return jobs.start("douyin-import", "导入抖音聊天记录", target)


def _douyin_env() -> dict[str, str]:
    env = _base_env()
    env["CHATWORK_DOUYIN_DATA"] = str(paths.DOUYIN_DIR)
    env["CHATWORK_DOUYIN_MEDIA"] = str(paths.DOUYIN_MEDIA)
    return env


class DouyinProgress:
    """从引擎 stdout 里解析「第几个会话 / 失败会话」。"""

    PATTERN = re.compile(r"^\[(\d+)/(\d+)\]\s+(.+?)(?:\s+\(最后活跃|$)")

    def __init__(self, job: jobs.Job) -> None:
        self.job = job
        self.total = 0
        self.done = 0
        self.current: str | None = None
        self.failed: list[str] = []
        self.messages = 0

    def __call__(self, line: str) -> None:
        match = self.PATTERN.match(line.strip())
        if match:
            self.done = int(match.group(1))
            self.total = int(match.group(2))
            self.current = match.group(3).strip()
            self.job.update({
                "stage": "抖音采集",
                "done": self.done,
                "total": self.total,
                "current": self.current,
                "failed": len(self.failed),
            })
            return
        if "错误:" in line and self.current:
            self.failed.append(self.current)
            self.job.update({"failed": len(self.failed)})
        m = re.search(r"会话:\s*(\d+)", line)
        if m:
            self.messages = int(m.group(1))


_last_douyin_failures: list[str] = []


def douyin_last_failures() -> list[str]:
    return list(_last_douyin_failures)


def _require_douyin_browser() -> None:
    status = browser_status()
    if not status["playwright"]:
        raise RuntimeError(
            "运行环境缺少 Playwright，无法启动浏览器采集。"
            "可以改为「导入已有数据库」，或安装依赖后重试。"
        )
    if not status["channel"]:
        raise RuntimeError(
            "没有找到可用的浏览器：请安装 Microsoft Edge，"
            "或执行 playwright install chromium 后重试。"
        )


def _douyin_list_cmd() -> list[str]:
    script = paths.DOUYIN_ENGINE_DIR / "extract.py"
    if not script.exists():
        raise FileNotFoundError("内置的抖音采集组件缺失")
    _require_douyin_browser()
    return [sys.executable, str(script), "--list-conversations"]


def start_douyin_list() -> jobs.Job:
    """第一次采集前，先让引擎把会话列表拉下来（只登录，不采集消息）。"""

    def target(job: jobs.Job) -> dict[str, Any]:
        with _heavy_slot(job, "获取抖音会话列表"):
            cmd = _douyin_list_cmd()
            job.write("[i] 会弹出浏览器，请扫码登录；只读取会话列表，不采集消息")
            code = _run_stream(
                cmd, cwd=paths.DOUYIN_ENGINE_DIR, env=_douyin_env(), job=job
            )
            if code != 0:
                raise RuntimeError(f"获取会话列表失败（退出码 {code}），详见日志")
            source = paths.DOUYIN_ENGINE_DIR / "data" / "conversations_list.json"
            if not source.is_file():
                raise RuntimeError("引擎没有生成会话列表文件")
            payload = json.loads(source.read_text(encoding="utf-8"))
            items = payload.get("items") or []
            paths.DOUYIN_DIR.mkdir(parents=True, exist_ok=True)
            target_file = paths.DOUYIN_DIR / "conversations_list.json"
            target_file.write_text(
                json.dumps({"discovered_at": payload.get("discovered_at"),
                            "items": items}, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            job.write(f"[✓] 已获取 {len(items)} 个会话，可在「采集范围」里勾选")
            return {"count": len(items)}

    return jobs.start("douyin-list", "获取抖音会话列表", target)


def _douyin_collect_cmd(filters: str | None, incremental: bool,
                        download_images: bool = False) -> list[str]:
    script = paths.DOUYIN_ENGINE_DIR / "extract.py"
    if not script.exists():
        raise FileNotFoundError("内置的抖音采集组件缺失")
    _require_douyin_browser()
    cmd = [sys.executable, str(script)]
    if filters:
        cmd += ["--filter", filters]
    if incremental:
        cmd.append("--incremental")
    if download_images:
        cmd.append("--download-images")
    return cmd


def heavy_busy() -> bool:
    """是否有重任务（导入/采集/解密）正在占用独占名额。"""
    return _HEAVY_LOCK.locked()


def start_incremental_update(*, trigger: str = "auto") -> jobs.Job:
    """把本机已有数据里的新消息增量导入统一库。"""

    def target(job: jobs.Job) -> dict[str, Any]:
        with _heavy_slot(job, "增量更新"):
            cfg = settings.load()
            reports: list[dict[str, Any]] = []
            # 先解密微信，再做增量导入；解密失败不影响已有的解密数据
            if cfg.get("autosync_wechat_decrypt", True):
                run_py = paths.SIWX_DIR / "run.py"
                if run_py.is_file():
                    try:
                        job.write("[i] 微信自动解密…（需要微信正在运行并登录）")
                        code = _siwx_auto(job, paths.WECHAT_DECRYPTED)
                        job.write(
                            f"[i] 微信解密结束（退出码 {code}），继续增量导入"
                        )
                    except Exception as exc:  # noqa: BLE001 - 解密失败不阻断导入
                        job.write(f"[!] 微信解密失败：{exc}，继续导入已有数据")
            accounts = wechat_importer.detect_accounts(paths.WECHAT_DECRYPTED)
            if accounts:
                job.write(f"[i] 微信增量：{len(accounts)} 个账号")
                for account in accounts:
                    if job.cancelled():
                        raise jobs.JobCancelled()
                    report = wechat_importer.import_account(
                        account, incremental=True,
                        should_cancel=job.cancelled, progress=job.update,
                    )
                    reports.append(report)
                    job.write(
                        f"[✓] {account.name}: 新消息 {report['messages_added']}"
                    )

            # 抖音：先按保存的范围自动采集，再按同一范围增量导入
            scope_mode = str(cfg.get("douyin_scope_mode") or "all")
            scope_names = [str(n) for n in (cfg.get("douyin_scope_names") or [])]
            collection_code = None
            if cfg.get("autosync_douyin_collect", True):
                if scope_mode == "include" and not scope_names:
                    job.write("[i] 抖音勾选范围为空，跳过自动采集")
                else:
                    try:
                        resolved = _douyin_filter(scope_mode, scope_names)
                        cmd = _douyin_collect_cmd(
                            resolved, incremental=True, download_images=False
                        )
                        job.write("[i] 抖音自动采集：会弹出浏览器，请扫码登录")
                        progress = DouyinProgress(job)
                        collection_code = _run_stream(
                            cmd, cwd=paths.DOUYIN_ENGINE_DIR, env=_douyin_env(),
                            job=job, on_line=progress,
                        )
                        global _last_douyin_failures
                        _last_douyin_failures = list(progress.failed)
                        job.write(
                            f"[i] 抖音采集结束（退出码 {collection_code}），"
                            f"失败会话 {len(progress.failed)} 个"
                        )
                    except Exception as exc:  # noqa: BLE001 - 采集失败仍导入已有库
                        job.write(f"[!] 抖音自动采集失败：{exc}，继续导入已有数据")

            if scope_mode == "include" and not scope_names:
                job.write("[i] 抖音勾选范围为空，跳过增量导入")
            elif paths.DOUYIN_DB.is_file():
                only = scope_names if scope_mode == "include" else None
                excluded = scope_names if scope_mode == "exclude" else None
                job.write(f"[i] 抖音增量：范围 {scope_mode}")
                report = douyin_importer.import_chat_db(
                    paths.DOUYIN_DB,
                    media_src=paths.DOUYIN_MEDIA,
                    copy_media=False,
                    should_cancel=job.cancelled,
                    progress=job.update,
                    only_names=only,
                    exclude_names=excluded,
                )
                reports.append(report)
                job.write(f"[✓] 抖音：新消息 {report['messages_added']}")
            if not reports:
                job.write("[i] 没有可更新的数据（微信解密目录和抖音库都不存在）")
            auto = asr.auto_transcribe(job)
            return {"trigger": trigger, "reports": reports, "auto_transcribe": auto}

    return jobs.start("auto-update", f"增量更新（{trigger}）", target)


def start_media_prewarm(*, thread_id: str | None = None,
                        limit: int = 300) -> jobs.Job:
    """把会话里还没落地的微信图片/表情/视频批量解密到本地缓存。"""

    def target(job: jobs.Job) -> dict[str, Any]:
        with _heavy_slot(job, "媒体缓存"):
            return _prewarm_media(job, thread_id, limit)

    return jobs.start("media-prewarm", "缓存媒体", target)


def _prewarm_media(job: jobs.Job, thread_id: str | None,
                   limit: int) -> dict[str, Any]:
    where = ["m.platform = 'wechat'",
             "m.kind IN ('image','sticker','video')",
             "(m.media_path IS NULL OR m.media_path = '')"]
    args: list[Any] = []
    if thread_id:
        kind, _, raw = thread_id.partition(":")
        if kind == "contact" and raw.isdigit():
            where.append("c.contact_id = ?")
            args.append(int(raw))
        elif kind == "conv" and raw.isdigit():
            where.append("c.id = ?")
            args.append(int(raw))
    limit = max(1, min(int(limit), 2000))
    sql = (
        "SELECT m.id, m.kind FROM messages m"
        " JOIN conversations c ON c.id = m.conversation_id"
        f" WHERE {' AND '.join(where)} ORDER BY m.ts DESC LIMIT ?"
    )
    with db.session(readonly=True) as conn:
        rows = conn.execute(sql, [*args, limit]).fetchall()

    total = len(rows)
    job.write(f"[i] 待缓存媒体：{total} 个")
    job.update({"total": total, "done": 0, "ok": 0, "failed": 0})
    ok = failed = 0
    errors: list[str] = []
    for index, row in enumerate(rows, 1):
        if job.cancelled():
            raise jobs.JobCancelled()
        try:
            if row["kind"] == "video":
                wechat_media.fetch_video(int(row["id"]))
            else:
                wechat_media.fetch_image(int(row["id"]))
            ok += 1
        except wechat_media.MediaUnavailable as exc:
            failed += 1
            if len(errors) < 5:
                errors.append(f"#{row['id']}: {exc}")
        except Exception as exc:  # noqa: BLE001 - 单个失败不中断
            failed += 1
            if len(errors) < 5:
                errors.append(f"#{row['id']}: {exc}")
        if index % 10 == 0 or index == total:
            job.update({"total": total, "done": index, "ok": ok, "failed": failed})
            job.write(f"[i] {index}/{total}（成功 {ok}，失败 {failed}）")
    for line in errors:
        job.write(f"[warn] {line}")
    job.write(f"[✓] 缓存完成：成功 {ok}，失败 {failed}")
    return {"total": total, "ok": ok, "failed": failed, "errors": errors}


def start_transcription(
    *,
    engine: str | None = None,
    scope: str = "pending",
    conversation_id: int | None = None,
    message_ids: list[int] | None = None,
    limit: int | None = None,
) -> jobs.Job:
    """批量语音识别（腾讯云 / 本地 whisper）。"""
    chosen = engine or settings.load().get("asr_engine", "off")
    if chosen not in asr.ENGINES:
        raise ValueError("还没有选择语音识别引擎（设置 → 语音识别）")
    info = asr.engine_info()
    if not info.get(chosen, {}).get("available"):
        raise ValueError(f"{chosen} 当前不可用，请先在设置里配置")

    def target(job: jobs.Job) -> dict[str, Any]:
        with _heavy_slot(job, "语音转写"):
            return asr.run_batch(
                job,
                engine=chosen,
                scope=scope,
                conversation_id=conversation_id,
                message_ids=message_ids,
                limit=limit,
                should_cancel=job.cancelled,
            )

    return jobs.start("asr-transcribe", f"语音转写（{chosen}）", target)


def start_douyin_collect(*, filters: str | None = None, incremental: bool = True,
                         mode: str = "all", download_images: bool = False,
                         conversations: list[str] | None = None) -> jobs.Job:
    # 先同步校验，参数不合法直接 400，不要等到任务线程里才报错
    resolved = _douyin_filter(mode, conversations) if mode != "all" else filters
    scope_text = "全部会话" if not resolved else f"指定 {len(resolved.split(','))} 个会话"

    def target(job: jobs.Job) -> dict[str, Any]:
        with _heavy_slot(job, "抖音采集"):
            job.write(f"[i] 采集范围：{scope_text}")
            cmd = _douyin_collect_cmd(resolved, incremental, download_images)
            progress = DouyinProgress(job)
            job.write("[i] 会弹出浏览器，请在窗口里扫码登录抖音")
            code = _run_stream(
                cmd, cwd=paths.DOUYIN_ENGINE_DIR, env=_douyin_env(), job=job,
                on_line=progress,
            )
            global _last_douyin_failures
            _last_douyin_failures = list(progress.failed)
            job.write(f"[i] 采集结束（退出码 {code}），失败会话 {len(progress.failed)} 个")
            return {"exit_code": code, "failed_conversations": progress.failed}

    return jobs.start("douyin-collect", "采集抖音聊天记录", target)


def start_douyin_refresh(*, filters: str | None = None,
                         incremental: bool = True,
                         mode: str = "all", download_images: bool = False,
                         conversations: list[str] | None = None) -> jobs.Job:
    """采集完顺手导入统一库。"""
    resolved = _douyin_filter(mode, conversations) if mode != "all" else filters
    scope_text = "全部会话" if not resolved else f"指定 {len(resolved.split(','))} 个会话"

    def target(job: jobs.Job) -> dict[str, Any]:
        with _heavy_slot(job, "抖音采集并导入"):
            job.write(f"[i] 采集范围：{scope_text}")
            cmd = _douyin_collect_cmd(resolved, incremental, download_images)
            progress = DouyinProgress(job)
            job.write("[i] 第一步：打开浏览器采集（需要扫码登录）")
            code = _run_stream(
                cmd, cwd=paths.DOUYIN_ENGINE_DIR, env=_douyin_env(), job=job,
                on_line=progress,
            )
            if code != 0:
                raise RuntimeError(f"采集失败（退出码 {code}），详见上方日志")
            if job.cancelled():
                raise jobs.JobCancelled()
            job.write("[i] 第二步：导入统一库")
            report = douyin_importer.import_chat_db(
                paths.DOUYIN_DB,
                media_src=paths.DOUYIN_MEDIA,
                copy_media=False,
                should_cancel=job.cancelled,
                progress=job.update,
            )
            job.write(
                f"[✓] 会话 +{report['conversations_added']}, 消息 +{report['messages_added']}"
            )
            global _last_douyin_failures
            _last_douyin_failures = list(progress.failed)
            auto = asr.auto_transcribe(job)
            return {"exit_code": code, "report": report, "auto_transcribe": auto,
                    "failed_conversations": progress.failed}

    return jobs.start("douyin-refresh", "抖音：采集并导入", target)
