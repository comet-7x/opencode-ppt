"""教案路由（异步任务）：POST 一行提交；GET /{id} 轮询取结果。

不用依赖注入，直接用全局单例（`lesson_plan_service` / `context`）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.context import context
from app.schemas.generator.lesson_plan import LessonPlanGenRequest, LessonPlanTaskDetail
from app.schemas.generator.task import TaskRef
from app.services.generator.common.task import base_detail_fields
from app.services.generator.lesson_plan.service import lesson_plan_service

router = APIRouter(prefix="/lesson-plan", tags=["lesson_plan"])
KIND = "lesson_plan"


@router.post("/create", response_model=TaskRef, status_code=202)
async def create_lesson_plan(req: LessonPlanGenRequest) -> TaskRef:
    return lesson_plan_service.submit(req)


@router.get("/get/{task_id}", response_model=LessonPlanTaskDetail)
def get_lesson_plan(task_id: str) -> LessonPlanTaskDetail:
    task = context.task_store.get(task_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"教案任务 {task_id} 不存在")
    return LessonPlanTaskDetail(**base_detail_fields(task), result=task.result)
