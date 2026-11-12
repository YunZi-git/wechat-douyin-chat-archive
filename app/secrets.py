"""云端凭据存储（`data/secrets.json`）。

和 `settings.json` 分开，是因为这里的值**绝不能进仓库**，也不该在日志/界面上
明文出现。`data/` 已经被 `.gitignore` 覆盖，这个文件再单独做一次原子写入。

读取优先级：环境变量 > secrets.json。这样 CI / 命令行可以用环境变量注入，
不用在磁盘上留 key。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import paths

SECRETS_PATH = paths.DATA_DIR / "secrets.json"

TENCENT_ID_ENV = "TENCENT_SECRET_ID"
TENCENT_KEY_ENV = "TENCENT_SECRET_KEY"


def _defaults() -> dict[str, Any]:
    return {"tencent": {"secret_id": "", "secret_key": "", "region": "ap-shanghai"}}


def load() -> dict[str, Any]:
    data = _defaults()
    try:
        raw = SECRETS_PATH.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            tencent = parsed.get("tencent")
            if isinstance(tencent, dict):
                data["tencent"].update(
                    {k: v for k, v in tencent.items() if isinstance(v, str)}
                )
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        pass
    return data


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """合并写入；只接受 tencent 下的已知字段。"""
    current = load()
    tencent = patch.get("tencent") or {}
    for key in ("secret_id", "secret_key", "region"):
        value = tencent.get(key)
        if isinstance(value, str):
            current["tencent"][key] = value.strip()
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SECRETS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SECRETS_PATH)
    try:  # 尽量收紧权限（Windows 上基本是 no-op，Linux/macOS 有效）
        os.chmod(SECRETS_PATH, 0o600)
    except OSError:
        pass
    return current


def tencent_credentials() -> tuple[str, str, str]:
    """返回 (secret_id, secret_key, region)；环境变量优先。"""
    data = load()["tencent"]
    sid = os.environ.get(TENCENT_ID_ENV) or data.get("secret_id", "")
    skey = os.environ.get(TENCENT_KEY_ENV) or data.get("secret_key", "")
    region = data.get("region") or "ap-shanghai"
    return sid, skey, region


def mask(value: str) -> str:
    """只露出前 4 后 4，界面显示用。"""
    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 6}{value[-4:]}"


def public_view() -> dict[str, Any]:
    """给前端的视图：不返回明文 key。"""
    sid, skey, region = tencent_credentials()
    src = "env" if os.environ.get(TENCENT_ID_ENV) else ("file" if sid else "none")
    return {
        "tencent": {
            "secret_id_masked": mask(sid),
            "secret_key_masked": mask(skey),
            "region": region,
            "configured": bool(sid and skey),
            "source": src,
        }
    }


def path() -> Path:
    return SECRETS_PATH
