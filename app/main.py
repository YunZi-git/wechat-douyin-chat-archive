"""FastAPI 应用入口。

桌面模式下由 :mod:`app.desktop` 在同一进程里起 uvicorn；开发时直接
``python -m app.main`` 也能跑起来。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, autosync, db, paths


@asynccontextmanager
async def lifespan(_app: FastAPI):
    paths.ensure_dirs()
    db.init()
    autosync.start_scheduler()
    try:
        yield
    finally:
        autosync.stop_scheduler()


app = FastAPI(title="聊天信息工作台", version=api.VERSION, lifespan=lifespan)

# 只接受本机 Host 头。防的是 DNS rebinding：恶意网页把自己的域名解析到
# 127.0.0.1，就能以同源身份读走本地聊天库。testserver 是 TestClient 用的。
_DEFAULT_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}
_TRUSTED_HOSTS = {
    h.strip().lower()
    for h in (os.environ.get("CHATWORK_TRUSTED_HOSTS") or "").split(",")
    if h.strip()
} or _DEFAULT_HOSTS


@app.middleware("http")
async def _host_guard(request, call_next):
    raw = request.headers.get("host") or ""
    name = raw.rsplit(":", 1)[0].strip("[]").lower() if raw else ""
    if name not in _TRUSTED_HOSTS:
        return JSONResponse({"error": "forbidden host"}, status_code=403)

    # 写接口必须是 JSON：浏览器对「跨源 + JSON」会先发预检，而我们没有 CORS，
    # 于是恶意页面无法用表单直接触发解密/导入这类操作（简单请求绕过预检）。
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return JSONResponse({"error": "content-type must be application/json"},
                                status_code=415)
    return await call_next(request)


app.include_router(api.router)


@app.exception_handler(Exception)
async def _unhandled(_request, exc: Exception):  # pragma: no cover - 兜底
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})


# 媒体文件（图片、语音、视频）
paths.ensure_dirs()
app.mount("/media", StaticFiles(directory=str(paths.MEDIA_DIR)), name="media")

# 前端构建产物
if paths.WEB_DIST.is_dir():
    _assets = paths.WEB_DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    _index_file = paths.WEB_DIST / "index.html"

    @app.get("/favicon.svg", include_in_schema=False)
    def _favicon():
        target = paths.WEB_DIST / "favicon.svg"
        return FileResponse(target) if target.exists() else JSONResponse({}, status_code=404)

    @app.get("/", include_in_schema=False)
    def _index():
        return FileResponse(_index_file)

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        if full_path.startswith(("api/", "media/", "assets/")):
            return JSONResponse({"error": "not found"}, status_code=404)
        candidate = (paths.WEB_DIST / full_path).resolve()
        try:
            if candidate.is_file() and candidate.is_relative_to(paths.WEB_DIST.resolve()):
                return FileResponse(candidate)
        except (OSError, ValueError):
            pass
        return FileResponse(_index_file)


else:  # 前端还没构建：给一句人话，而不是 404
    @app.get("/", include_in_schema=False)
    def _no_frontend():
        return HTMLResponse(
            "<h3>前端还没构建</h3><p>在 <code>web/</code> 目录执行 "
            "<code>npm install &amp;&amp; npm run build</code>，然后重启服务。</p>",
            status_code=503,
        )


def serve(host: str = "127.0.0.1", port: int = 0) -> None:
    """开发用：直接起服务。port=0 时自动挑空闲端口。"""
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    serve(port=int(os.environ.get("CHATWORK_PORT", "8765")))
