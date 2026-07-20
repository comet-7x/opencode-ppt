"""聚合 generator 下所有子路由，供 main 一次性挂载。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.generator import health, homework, lesson_plan, ppt, slides

router = APIRouter()
router.include_router(health.router)
router.include_router(lesson_plan.router)
router.include_router(slides.router)
router.include_router(homework.router)
router.include_router(ppt.router)

__all__ = ["router"]
