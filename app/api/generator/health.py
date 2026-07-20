"""健康检查：确认全局 AppContext 是否已初始化（模型/存储就绪）。"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import config_engine
from app.core.context import context

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str  # "ok" | "degraded"
    model: str
    provider_loaded: bool


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok" if context.initialized else "degraded",
        model=config_engine.base_config.models.llm.model_name,
        provider_loaded=context.initialized,
    )
