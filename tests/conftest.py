"""测试夹具：用合成演示数据建一个临时统一库。

注意：``CHATWORK_DATA_DIR`` 必须在导入 app 之前设置好，因为
``app.paths`` 在导入时就把路径算出来了。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="chatwork-test-"))
os.environ["CHATWORK_DATA_DIR"] = str(_TMP)

import pytest  # noqa: E402

from app import db, paths  # noqa: E402
from app.importers import douyin, wechat  # noqa: E402


def _load_demo_module():
    spec = importlib.util.spec_from_file_location(
        "make_demo_data", ROOT / "scripts" / "make_demo_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def demo_sources():
    out = _TMP / "sources"
    gen = _load_demo_module()
    return {
        "root": out,
        "wechat": gen.build_wechat(out),
        "douyin": gen.build_douyin(out),
    }


@pytest.fixture()
def library(demo_sources):
    """每个测试一份干净的统一库。"""
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(paths.UNIFIED_DB) + suffix)
        if target.exists():
            target.unlink()
    # 每个测试从干净配置开始：避免密钥/设置串到别的用例
    for cfg in (paths.SETTINGS_PATH, paths.DATA_DIR / "secrets.json"):
        if cfg.exists():
            cfg.unlink()
    db.init()
    r_wx = wechat.import_account(demo_sources["wechat"])
    r_dy = douyin.import_chat_db(
        demo_sources["douyin"] / "chat.db",
        media_src=demo_sources["douyin"] / "media",
        copy_media=True,
    )
    yield {"wechat": r_wx, "douyin": r_dy}


@pytest.fixture()
def client(library):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
