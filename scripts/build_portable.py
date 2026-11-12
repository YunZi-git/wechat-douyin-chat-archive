#!/usr/bin/env python3
"""把应用打包成「解压即用」的便携目录。

产物结构::

    release/聊天信息工作台/
      runtime/            内置 Python 3.11（官方 embeddable）+ 依赖
      app/                后端与桌面外壳
      engines/            微信解密、抖音采集
      web/dist/           前端构建产物
      demo/               合成演示数据
      scripts/           导入/打包等脚本
      启动.cmd            双击启动（原生窗口）
      启动-调试.cmd        带控制台，排错用
      使用说明.txt

用法::

    python scripts/build_portable.py                # 默认 release/
    python scripts/build_portable.py --zip           # 额外压成 zip
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY_VERSION = "3.11.9"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
APP_NAME = "聊天信息工作台"

# 这些包只是开发期用的，不进分发包
EXCLUDE_SITE = {
    "pip", "setuptools", "pkg_resources", "_distutils_hack", "wheel",
    "pytest", "_pytest", "iniconfig", "pluggy", "py.py", "pytest.ini",
    "distutils-precedence.pth", "easy-install.pth",
}
EXCLUDE_PATTERNS = ("__pycache__", "*.pyc", "*.pyo", "*.dist-info/RECORD")


def log(msg: str) -> None:
    print(msg, flush=True)


def download_python(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"python-{PY_VERSION}-embed-amd64.zip"
    if target.exists() and target.stat().st_size > 1_000_000:
        log(f"[i] 复用已下载的运行时: {target}")
        return target
    log(f"[i] 下载 Python {PY_VERSION} embeddable …")
    tmp = target.with_suffix(".part")
    with urllib.request.urlopen(PY_URL, timeout=120) as resp, tmp.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    tmp.replace(target)
    log(f"[✓] 运行时 {target.stat().st_size / 1024 / 1024:.1f} MB")
    return target


def copy_site_packages(venv_site: Path, dest: Path) -> int:
    log(f"[i] 复制依赖 {venv_site} → {dest}")
    count = 0
    dest.mkdir(parents=True, exist_ok=True)
    for entry in venv_site.iterdir():
        name = entry.name
        base = name.split("-")[0].lower()
        if name in EXCLUDE_SITE or base in {n.lower() for n in EXCLUDE_SITE}:
            continue
        if name.endswith((".dist-info", ".egg-info")) and base in EXCLUDE_SITE:
            continue
        target = dest / name
        if entry.is_dir():
            if target.exists():
                continue
            shutil.copytree(
                entry, target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", "tests", "test"),
            )
        else:
            shutil.copy2(entry, target)
        count += 1
    return count


def copy_project(dest: Path) -> None:
    log("[i] 复制程序文件")
    ignore = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.pyo", ".git", ".venv", "node_modules",
        "*.db-wal", "*.db-shm", "data", "release", "_scratch", "logs",
    )
    shutil.copytree(ROOT / "app", dest / "app", ignore=ignore)
    shutil.copytree(ROOT / "engines", dest / "engines", ignore=ignore)
    shutil.copytree(ROOT / "scripts", dest / "scripts", ignore=ignore)
    shutil.copytree(ROOT / "web" / "dist", dest / "web" / "dist")
    if (ROOT / "demo").is_dir():
        # demo/ 是合成数据，需要连同 .db 一起带上，否则新用户没法体验
        shutil.copytree(
            ROOT / "demo", dest / "demo",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for name in ("LICENSE", "NOTICE", "README.md", "requirements.txt"):
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, dest / name)
    shutil.copy2(ROOT / "scripts" / "launcher.py", dest / "launcher.py")


START_CMD = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
start "" "%~dp0runtime\\pythonw.exe" "%~dp0launcher.py"
"""

DEBUG_CMD = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo 调试模式：关掉这个窗口即退出应用。
"%~dp0runtime\\python.exe" "%~dp0launcher.py"
pause
"""

USAGE = """聊天信息工作台
==============

双击「启动.cmd」即可打开应用窗口。
如果窗口没有出现，双击「启动-调试.cmd」，窗口里会显示具体错误。

数据放在本目录的 data/ 下：
  data/unified.db     统一数据库
  data/media/         图片、语音等
  data/exports/       导出的文件
  data/wechat/        微信解密产物
  data/douyin/        抖音采集产物

想先看看界面长什么样：打开应用 → 导入数据 → 抖音「导入本地数据库」，
选择 demo/douyin/chat.db 即可（这是完全合成的示例数据）。

备份：直接复制整个 data/ 目录。
卸载：删掉整个文件夹，不会残留注册表项。

许可证：AGPL-3.0，见 LICENSE；第三方组件见 NOTICE。
"""


def write_launchers(dest: Path) -> None:
    (dest / "启动.cmd").write_text(START_CMD, encoding="utf-8")
    (dest / "启动-调试.cmd").write_text(DEBUG_CMD, encoding="utf-8")
    (dest / "使用说明.txt").write_text(USAGE, encoding="utf-8")


def patch_pth(runtime: Path) -> None:
    pth = runtime / "python311._pth"
    content = (
        "python311.zip\n"
        ".\n"
        "Lib\\site-packages\n"
        "..\n"
        "import site\n"
    )
    pth.write_text(content, encoding="ascii")
    log("[i] 已配置 python311._pth（含 Lib\\site-packages 与项目根目录）")


def build(out_dir: Path, python_zip: Path, venv_site: Path) -> Path:
    if out_dir.exists():
        log(f"[i] 清空旧产物 {out_dir}")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    runtime = out_dir / "runtime"
    log("[i] 解压运行时")
    with zipfile.ZipFile(python_zip) as zf:
        zf.extractall(runtime)
    patch_pth(runtime)

    copied = copy_site_packages(venv_site, runtime / "Lib" / "site-packages")
    log(f"[✓] 依赖条目 {copied}")

    copy_project(out_dir)
    write_launchers(out_dir)
    return out_dir


def make_zip(folder: Path) -> Path:
    target = folder.parent / f"{folder.name}.zip"
    log(f"[i] 压缩 → {target}")
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in folder.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(folder.parent))
    log(f"[✓] {target.name}  {target.stat().st_size / 1024 / 1024:.1f} MB")
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description="打包便携版")
    ap.add_argument("--out", default=str(ROOT / "release"))
    ap.add_argument("--venv-site", default=str(ROOT / ".venv" / "Lib" / "site-packages"))
    ap.add_argument("--python-zip", default=None)
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--no-build-web", action="store_true")
    args = ap.parse_args()

    if not args.no_build_web:
        web = ROOT / "web"
        if (web / "package.json").exists():
            log("[i] 构建前端")
            npm = "npm.cmd" if os.name == "nt" else "npm"
            subprocess.run([npm, "run", "build"], cwd=web, check=True)

    python_zip = Path(args.python_zip) if args.python_zip else download_python(
        ROOT / ".cache"
    )
    out_dir = Path(args.out) / APP_NAME
    build(out_dir, python_zip, Path(args.venv_site))

    total = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    log(f"[✓] 完成：{out_dir}")
    log(f"[✓] 体积 {total / 1024 / 1024:.1f} MB")
    if args.zip:
        make_zip(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
