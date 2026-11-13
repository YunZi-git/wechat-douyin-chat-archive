"""端到端冒烟：真浏览器 + 真服务 + 合成数据。

默认跳过；需要 ``CHATWORK_E2E=1`` 且本机有 Edge（Windows 自带）。
CI 的 e2e job 会先构建 web/dist 再跑。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    os.environ.get("CHATWORK_E2E") != "1",
    reason="设置 CHATWORK_E2E=1 才跑端到端（需要浏览器）",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("e2e-data")
    demo = tmp_path_factory.mktemp("e2e-demo")
    env = {**os.environ, "CHATWORK_DATA_DIR": str(data), "PYTHONIOENCODING": "utf-8"}
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "make_demo_data.py"),
         "--out", str(demo), "--import", "--data", str(data)],
        check=True, cwd=ROOT, env=env, timeout=180,
    )
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        import urllib.request

        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/api/health", timeout=2):
                    break
            except Exception:
                time.sleep(0.4)
        else:
            raise RuntimeError("服务没有在 40 秒内启动")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="msedge", headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"没有可用的 Edge：{exc}")
        yield browser
        browser.close()


def test_search_page_works(server, browser):
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(server + "/?view=search", wait_until="networkidle")
    page.fill('input[placeholder*="搜索消息内容"]', "周末")
    page.press('input[placeholder*="搜索消息内容"]', "Enter")
    page.wait_for_selector("text=条结果", timeout=15000)
    assert page.locator("text=条结果").first.is_visible()
    page.close()


def test_appearance_switch_changes_css(server, browser):
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(server + "/?view=appearance", wait_until="networkidle")
    page.get_by_text("新野兽派", exact=True).first.click()
    page.wait_for_timeout(800)
    radius = page.evaluate(
        "() => getComputedStyle(document.documentElement).getPropertyValue('--radius').trim()"
    )
    assert radius == "0px"
    page.close()


def test_language_switch(server, browser):
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(server + "/?view=settings", wait_until="networkidle")
    page.get_by_role("button", name="English").click()
    page.wait_for_timeout(600)
    page.goto(server + "/?view=search", wait_until="networkidle")
    assert page.locator("h1").first.inner_text().strip() == "Search"
    # 切回中文，避免影响其他用例
    page.goto(server + "/?view=settings", wait_until="networkidle")
    page.get_by_role("button", name="中文").click()
    page.close()


def test_settings_loads(server, browser):
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(server + "/?view=settings", wait_until="networkidle")
    assert page.get_by_text("设置", exact=True).first.is_visible()
    assert page.get_by_text("检查更新").first.is_visible()
    page.close()
