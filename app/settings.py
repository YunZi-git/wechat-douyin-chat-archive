"""应用设置（不含任何密钥 / 账号信息）。

设置写在 ``data/settings.json``，该目录不进版本库。开源仓库里不会有任何
真实配置：第一次启动时这里只会生成默认值。
"""
from __future__ import annotations

import json
import os
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    "theme": "dark",  # 内置：dark/light/warm/purple/system；其它为 StyleKit 的 slug
    "update_url": "",  # 更新清单地址（GitHub Release API 或自定义 JSON）
    "language": "zh",  # zh | en
    "autosync_enabled": False,        # 定时增量更新
    "autosync_interval_minutes": 30,
    "autosync_last_run": 0,
    "autosync_wechat_decrypt": True,  # 定时任务先跑一次微信解密
    "autosync_douyin_collect": True,  # 定时任务先打开浏览器采集抖音
    "douyin_scope_mode": "all",       # all | include | exclude
    "douyin_scope_names": [],
    "density": "comfortable",
    "page_size": 60,
    "timezone_offset": 8,
    # 微信侧：本人 wxid 由导入器自动推断，一般不用手填
    "wechat_self_wxid": "",
    # 抖音侧：本人 uid 由导入器自动推断
    "douyin_self_uid": "",
    "douyin_self_confirmed": False,
    # 导入时是否把音频/图片一并带过来（后续版本使用）
    "import_media": True,
    # ── 语音识别 ──
    "asr_engine": "off",              # off | tencent | local
    "asr_auto_after_import": False,
    "asr_douyin_fallback": True,   # 抖音语音 native 识别失败时，用所选引擎兜底   # 导入完成后自动识别新语音
    "asr_concurrency": 10,             # 云端并发（本地会强制为 1）
    "whisper_model": "small",         # base | small | medium
    "whisper_model_dir": "",          # 可指定模型下载/缓存目录
    "whisper_endpoint": "",           # HuggingFace 镜像；国内建议 https://hf-mirror.com
}


def load() -> dict[str, Any]:
    try:
        raw = paths.SETTINGS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return dict(DEFAULTS)
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def save(patch: dict[str, Any]) -> dict[str, Any]:
    """合并写入并原子落盘。"""
    current = load()
    for k, v in patch.items():
        if k in DEFAULTS:
            current[k] = v
    paths.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = paths.SETTINGS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, paths.SETTINGS_PATH)
    return current
