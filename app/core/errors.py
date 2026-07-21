"""把领域异常 / 上游模型异常 / 请求校验错误统一翻译成 BaseResponse 信封。

响应约定：**HTTP 恒 200**，真正的状态码放在 body 的 `code` 里，客户端只解析一种形状。
在 main 里 `register_exception_handlers(app)` 注册。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.schemas.response import BaseResponse
from app.services.generator.common.errors import GenerationError

logger = logging.getLogger("app.errors")


def envelope(code: int, msg: str) -> JSONResponse:
    """统一信封响应：HTTP 200 + body 里的业务 code。"""
    body = BaseResponse[Any](code=code, msg=msg, data=None)
    return JSONResponse(status_code=status.HTTP_200_OK, content=body.model_dump())


def _format_validation(exc: RequestValidationError) -> str:
    """把 pydantic 的错误列表压成一行可读文案。"""
    parts = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", ()) if x != "body")
        parts.append(f"{loc}: {e.get('msg', '')}" if loc else str(e.get("msg", "")))
    return "请求参数校验失败 — " + "；".join(parts)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(GenerationError)
    async def _handle_generation(request: Request, exc: GenerationError) -> JSONResponse:
        logger.warning("%s: %s", type(exc).__name__, exc)
        return envelope(exc.status_code, f"{type(exc).__name__}: {exc}")

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return envelope(status.HTTP_422_UNPROCESSABLE_CONTENT, _format_validation(exc))

    # 上游模型（openai 兼容端点）异常：503/连接/超时等统一成 502
    try:
        from openai import APIError
    except ImportError:  # 无 openai 时跳过（不应发生）
        return

    @app.exception_handler(APIError)
    async def _handle_model(request: Request, exc: APIError) -> JSONResponse:
        logger.warning("upstream model error: %s", exc)
        return envelope(status.HTTP_502_BAD_GATEWAY, f"模型调用失败：{exc}")
