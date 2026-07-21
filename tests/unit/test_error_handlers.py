"""HTTP 异常处理器 + lifespan 单测。

异步任务化后领域异常大多落进 task.error，但**同步路径**（校验、未来的同步端点）仍靠这些
handler 把异常翻成干净 JSON。用一个迷你 app 直接验证映射，避免依赖具体业务端点。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.core.context import context
from app.core.errors import register_exception_handlers
from app.core.lifespan import lifespan
from app.services.generator.common.errors import (
    GenerationError,
    KnowledgeError,
    ModelError,
    ParseError,
)


@pytest.fixture
def mini_app() -> FastAPI:
    """一个只用来触发各类异常的迷你 app。"""
    application = FastAPI()
    register_exception_handlers(application)

    @application.get("/raise/{which}")
    async def _raise(which: str) -> None:
        from openai import APIConnectionError

        match which:
            case "knowledge":
                raise KnowledgeError("知识源不可用")
            case "parse":
                raise ParseError("解析失败")
            case "model":
                raise ModelError("模型空响应")
            case "generic":
                raise GenerationError("说不清的领域错误")
            case _:
                raise APIConnectionError(request=httpx.Request("POST", "http://model"))

    return application


def _client(application: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=application), base_url="http://t")


@pytest.mark.parametrize(
    ("which", "status", "error"),
    [
        ("knowledge", 400, "KnowledgeError"),   # 客户端输入问题
        ("parse", 502, "ParseError"),           # 上游输出不合约
        ("model", 502, "ModelError"),           # 上游不可用
        ("generic", 500, "GenerationError"),    # 基类兜底
    ],
)
async def test_domain_errors_map_to_status_and_json(
    mini_app: FastAPI, which: str, status: int, error: str
) -> None:
    async with _client(mini_app) as c:
        r = await c.get(f"/raise/{which}")
    assert r.status_code == status
    body = r.json()
    assert body["error"] == error
    assert body["detail"]          # 带可读原因


async def test_openai_error_maps_to_502(mini_app: FastAPI) -> None:
    async with _client(mini_app) as c:
        r = await c.get("/raise/upstream")
    assert r.status_code == 502
    assert r.json()["error"] == "ModelUpstreamError"
    assert "模型调用失败" in r.json()["detail"]


# ————————————————————— lifespan ————————————————————— #
async def test_lifespan_initializes_context_and_closes() -> None:
    """启动跑 initialize（幂等），关停跑 close。"""
    was_initialized = context.initialized
    original_store = context._task_store
    # 强制回到未初始化，验证 lifespan 真的会去建
    context._initialized = False
    try:
        async with lifespan(FastAPI()):
            assert context.initialized is True
            assert context.model_provider is not None
            assert context.knowledge_source is not None
            assert context.task_store is not None
    finally:
        context._initialized = was_initialized
        context._task_store = original_store


async def test_initialize_is_idempotent() -> None:
    context.initialize()
    first = context.task_store
    context.initialize()          # 第二次应是 no-op，不重建
    assert context.task_store is first


def test_context_properties_raise_before_initialize() -> None:
    """未初始化时访问依赖要明确报错，而不是返回 None 让调用方莫名其妙。"""
    from app.core.context import AppContext

    fresh = AppContext()
    assert fresh.initialized is False
    for attr in ("config", "model_provider", "knowledge_source", "task_store"):
        with pytest.raises(RuntimeError, match="未初始化"):
            getattr(fresh, attr)
