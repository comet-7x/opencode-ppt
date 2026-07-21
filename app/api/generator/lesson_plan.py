"""教案路由（异步任务）：POST 一行提交；GET /{id} 轮询取结果。

统一返回 BaseResponse 信封（HTTP 恒 200，业务码在 code）。
不用依赖注入，直接用全局单例（`lesson_plan_service` / `context`）。
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.context import context
from app.schemas.generator.lesson_plan import LessonPlanGenRequest, LessonPlanTaskDetail
from app.schemas.generator.task import TaskRef, base_detail_fields
from app.schemas.response import ApiResult, BaseResponse
from app.services.generator.lesson_plan.service import lesson_plan_service

router = APIRouter(prefix="/lesson-plan", tags=["lesson_plan"])
KIND = "lesson_plan"


@router.post("/create", response_model=BaseResponse[TaskRef])
async def create_lesson_plan(req: LessonPlanGenRequest) -> BaseResponse[TaskRef]:
    return ApiResult.data(lesson_plan_service.submit(req))


@router.get("/get/{task_id}", response_model=BaseResponse[LessonPlanTaskDetail])
def get_lesson_plan(task_id: str) -> BaseResponse[LessonPlanTaskDetail]:
    task = context.task_store.get(task_id)
    if task is None or task.kind != KIND:
        return ApiResult.error(status.HTTP_404_NOT_FOUND, f"教案任务 {task_id} 不存在")
    return ApiResult.data(
        LessonPlanTaskDetail(**base_detail_fields(task), result=task.result)
    )
