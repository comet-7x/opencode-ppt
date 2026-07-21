"""HTTP 异常处理器 + lifespan 单测。

异步任务化后领域异常大多落进 task.error，但**同步路径**（校验、未来的同步端点）仍靠这些
handler 把异常翻成统一信封。用一个迷你 app 直接验证映射，避免依赖具体业务端点。

信封约定：HTTP 恒 200，真实状态码在 body 的 `code`，原因在 `msg`，`data` 为 null。
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from pydantic import BaseModel

from app.core.context import context
from app.core.errors import register_exception_handlers
from app.core.lifespan import lifespan
from app.services.generator.common.errors import (
    GenerationError,
    KnowledgeError,
    ModelError,
    ParseError,
)
from tests.conftest import err_msg


class EchoBody(BaseModel):
    """必须定义在模块级：局部类在 `from __future__ import annotations` 下 FastAPI 解析不到。"""

    n: int


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
    ("which", "code", "error"),
    [
        ("knowledge", 400, "KnowledgeError"),   # 客户端输入问题
        ("parse", 502, "ParseError"),           # 上游输出不合约
        ("model", 502, "ModelError"),           # 上游不可用
        ("generic", 500, "GenerationError"),    # 基类兜底
    ],
)
async def test_domain_errors_map_to_envelope(
    mini_app: FastAPI, which: str, code: int, error: str
) -> None:
    async with _client(mini_app) as c:
        r = await c.get(f"/raise/{which}")
    assert err_msg(r, code).startswith(f"{error}: ")   # 类名 + 可读原因


async def test_openai_error_maps_to_502(mini_app: FastAPI) -> None:
    async with _client(mini_app) as c:
        r = await c.get("/raise/upstream")
    assert "模型调用失败" in err_msg(r, 502)


async def test_validation_error_maps_to_422_envelope() -> None:
    """请求体不合法也走信封：HTTP 200 + code 422 + 逐字段可读文案。"""
    application = FastAPI()
    register_exception_handlers(application)

    @application.post("/echo")
    async def _echo(body: EchoBody) -> int:
        return body.n

    async with _client(application) as c:
        r = await c.post("/echo", json={"n": "不是数字"})
    msg = err_msg(r, 422)
    assert "请求参数校验失败" in msg
    assert "n" in msg


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


async def test_close_cancels_inflight_tasks() -> None:
    """关停时不留悬空协程：内存 store 里未终态的任务统一取消。"""
    from app.components.task import InMemoryTaskStore, TaskStatus

    original = context._task_store
    store = InMemoryTaskStore()
    context._task_store = store
    task = store.create("k")
    try:
        await context.close()
    finally:
        context._task_store = original
    assert task.status is TaskStatus.CANCELLED
