"""作业路由（异步任务）：POST 一行提交；GET /{id} 轮询取结果。

统一返回 BaseResponse 信封（HTTP 恒 200，业务码在 code）。全局单例，不用 DI。
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.context import context
from app.schemas.generator.homework import HomeworkGenRequest, HomeworkTaskDetail
from app.schemas.generator.task import TaskRef, base_detail_fields
from app.schemas.response import ApiResult, BaseResponse
from app.services.generator.homework.service import homework_service

router = APIRouter(prefix="/homework", tags=["homework"])
KIND = "homework"


@router.post("/create", response_model=BaseResponse[TaskRef])
async def create_homework(req: HomeworkGenRequest) -> BaseResponse[TaskRef]:
    return ApiResult.data(homework_service.submit(req))


@router.get("/get/{task_id}", response_model=BaseResponse[HomeworkTaskDetail])
def get_homework(task_id: str) -> BaseResponse[HomeworkTaskDetail]:
    task = context.task_store.get(task_id)
    if task is None or task.kind != KIND:
        return ApiResult.error(status.HTTP_404_NOT_FOUND, f"作业任务 {task_id} 不存在")
    return ApiResult.data(
        HomeworkTaskDetail(**base_detail_fields(task), result=task.result)
    )
