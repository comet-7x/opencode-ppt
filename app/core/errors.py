"""把领域异常 + 上游模型异常统一翻译成干净的 HTTP JSON 响应。

在 main 里 `register_exception_handlers(app)` 注册。所有端点（同步端点与 job 端点）
共享同一套映射：
- GenerationError 子类 → 各自 status_code（KnowledgeError 400 / Parse|ModelError 502）
- openai APIError（含 503/连接/超时）→ 502，避免模型不可用时裸奔 500 + 堆栈
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.generator.common.errors import GenerationError

logger = logging.getLogger("app.errors")


def _payload(error: str, detail: str) -> dict[str, str]:
    return {"error": error, "detail": detail}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(GenerationError)
    async def _handle_generation(request: Request, exc: GenerationError) -> JSONResponse:
        logger.warning("%s: %s", type(exc).__name__, exc)
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(type(exc).__name__, str(exc)),
        )

    # 上游模型（openai 兼容端点）异常：503/连接/超时等统一成 502
    try:
        from openai import APIError
    except ImportError:  # 无 openai 时跳过（不应发生）
        return

    @app.exception_handler(APIError)
    async def _handle_model(request: Request, exc: APIError) -> JSONResponse:
        logger.warning("upstream model error: %s", exc)
        return JSONResponse(
            status_code=502,
            content=_payload("ModelUpstreamError", f"模型调用失败：{exc}"),
        )
