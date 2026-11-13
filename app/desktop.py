"""桌面外壳：在本进程里起后端，再用 pywebview 开一个原生窗口。

没有 pywebview / WebView2 时自动退化成「用默认浏览器打开」，保证任何
Windows 上都能用。窗口关掉 = 进程退出，不会留后台服务。
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import threading
import time
import urllib.request

from . import paths
from .version import VERSION

APP_TITLE = "聊天信息工作台"
_instance_handle = None


def _single_instance() -> bool:
    """Windows 命名互斥量：防止双击两次开出两个实例抢同一个 SQLite。"""
    global _instance_handle
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        _instance_handle = kernel32.CreateMutexW(
            None, False, "Local\\ChatWorkbench_SingleInstance"
        )
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            ctypes.windll.user32.MessageBoxW(
                None,
                "聊天信息工作台已经在运行，请先关闭已打开的窗口。",
                APP_TITLE,
                0x40,
            )
            return False
    except Exception:  # noqa: BLE001 - 互斥量不可用时不要阻止启动
        return True
    return True


def _ensure_stdio() -> None:
    """pythonw.exe 没有控制台，sys.stdout/stderr 是 None。

    uvicorn 的日志格式化会调用 ``sys.stdout.isatty()``，不补上就直接崩；
    顺手把窗口模式的输出写进日志文件，出问题时能查。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        paths.LOG_DIR.mkdir(parents=True, exist_ok=True)
        stream = open(  # noqa: SIM115 - 进程生命周期内一直用
            paths.LOG_DIR / "app.log", "a", encoding="utf-8", buffering=1
        )
    except OSError:
        stream = open(io.devnull, "w", encoding="utf-8")  # type: ignore[assignment]
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, timeout: float = 40.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status != 200:
                    time.sleep(0.15)
                    continue
                # 只认自己这个版本的健康检查，避免端口被旧进程占用时误判
                payload = json.loads(resp.read().decode("utf-8"))
                return payload.get("version") == VERSION
        except Exception:  # noqa: BLE001 - 服务还没起来
            time.sleep(0.15)
    return False


def _start_server(port: int) -> "threading.Thread":
    import uvicorn

    from .main import app

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    return thread


def _serve_in_browser(url: str) -> int:
    """没有可用 WebView 时的降级方案：用默认浏览器打开，进程挂着。"""
    import webbrowser

    webbrowser.open(url)
    print(f"[i] 已用默认浏览器打开 {url}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


def run() -> int:
    if not _single_instance():
        return 0
    try:
        paths.ensure_dirs()
    except OSError as exc:
        raise RuntimeError(
            "无法写入数据目录，请把程序解压到桌面、文档等可写目录后再启动："
            f"{paths.DATA_DIR}（{exc}）"
        ) from exc
    _ensure_stdio()
    port = _free_port()
    _start_server(port)
    base_url = f"http://127.0.0.1:{port}"
    # 带版本号：升级后 WebView2 不会继续用旧 index.html 的缓存
    url = f"{base_url}/?v={VERSION}"
    if not _wait_ready(f"{base_url}/api/health"):
        # launcher.py 会把异常弹窗；只 return 1 在 pythonw 下是“双击没反应”
        raise RuntimeError(
            "后端没有启动成功。请查看 data/logs/app.log；"
            "若程序放在 Program Files 等只读目录，请解压到桌面后再试。"
        )

    try:
        import webview
    except Exception:  # noqa: BLE001 - 缺依赖就走浏览器
        return _serve_in_browser(url)

    try:
        webview.create_window(
            APP_TITLE,
            url,
            width=1360,
            height=860,
            min_size=(1000, 640),
            background_color="#12141a",
            text_select=True,
        )
        webview.start()
    except Exception as exc:  # noqa: BLE001 - WebView2 缺失 / 初始化失败
        print(f"[!] 原生窗口启动失败，改用浏览器：{exc}", file=sys.stderr)
        return _serve_in_browser(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
