"""桌面外壳：在本进程里起后端，再用 pywebview 开一个原生窗口。

没有 pywebview / WebView2 时自动退化成「用默认浏览器打开」，保证任何
Windows 上都能用。窗口关掉 = 进程退出，不会留后台服务。
"""
from __future__ import annotations

import io
import socket
import sys
import threading
import time
import urllib.request

from . import paths

APP_TITLE = "聊天信息工作台"


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
                if resp.status == 200:
                    return True
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
    paths.ensure_dirs()
    _ensure_stdio()
    port = _free_port()
    _start_server(port)
    url = f"http://127.0.0.1:{port}"
    if not _wait_ready(f"{url}/api/health"):
        print("[x] 后端没有启动成功", file=sys.stderr)
        return 1

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
