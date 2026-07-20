"""应用生命周期：启动时初始化全局 context 单例，关停时清理。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.context import context


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    context.initialize()  # 构建 model_provider / knowledge_source / task_store（+将来 redis 等）
    try:
        yield
    finally:
        await context.close()
