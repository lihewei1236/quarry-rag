"""应用装配：请求 ID、异常处理、健康检查、路由、启动与关闭。

第一章的 /healthz 也在这里，多加了 Redis 的连通性检查。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from quarry.api.models import code_of, error_payload
from quarry.api.routers import debug, ingest
from quarry.api.routes import router
from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.logging import get_logger
from quarry.infra.settings import get_settings

log = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    await init_pool()
    # 限流计数用的 Redis 客户端，和第三章队列那个各管各的
    app.state.redis = aioredis.from_url(s.redis_url, decode_responses=True)
    log.info("quarry-rag 接口已启动，开关：{}", s.switches())
    try:
        yield
    finally:
        await app.state.redis.aclose()
        await close_pool()


app = FastAPI(title="quarry-rag", version="1.0", lifespan=lifespan)

app.include_router(router)              # 第八章：/v1/search、/v1/collections
app.include_router(ingest.router)       # 第三章：上传与状态查询
app.include_router(debug.router)        # 第四章：检索调试接口（仅开发环境可用）
app.mount("/web", StaticFiles(directory="quarry/web", html=True), name="web")


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    """每个请求一个 ID：响应头、错误体、查询日志里都是它，排查时一串到底。"""
    request_id = request.headers.get("X-Request-Id") or str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    return response


@app.exception_handler(HTTPException)
async def on_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code,
                        content=error_payload(code_of(exc.status_code), exc.detail,
                                              getattr(request.state, "request_id", None)),
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def on_validation_error(request: Request,
                              exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", [])[1:])
    return JSONResponse(
        status_code=422,
        content=error_payload("invalid_request",
                              f"参数有问题：{where} {first.get('msg', '')}".strip(),
                              getattr(request.state, "request_id", None)))


@app.exception_handler(Exception)
async def on_unhandled(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    log.exception("未处理的异常 {}：{!r}", request_id, exc)
    # 对外只给 request_id，不漏堆栈、不漏 SQL
    return JSONResponse(status_code=500,
                        content=error_payload("internal_error", "服务内部错误", request_id))


@app.get("/healthz")
async def healthz() -> JSONResponse:
    s = get_settings()
    checks = {"db": False, "redis": False}
    try:
        async with acquire() as conn:
            checks["db"] = await conn.fetchval("SELECT 1") == 1
    except Exception as e:
        log.warning("healthz 数据库检查失败：{!r}", e)
    try:
        checks["redis"] = bool(await app.state.redis.ping())
    except Exception as e:
        log.warning("healthz Redis 检查失败：{!r}", e)
    ok = all(checks.values())
    # 健康探针只看状态码：依赖不通就返回 503，编排系统才会把这个实例摘掉
    return JSONResponse(status_code=200 if ok else 503,
                        content={"status": "ok" if ok else "degraded",
                                 "checks": checks, "switches": s.switches()})