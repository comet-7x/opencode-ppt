"""逐页 slides 路由（异步任务）：POST 一行提交；GET /{id} 轮询取结果。

统一返回 BaseResponse 信封（HTTP 恒 200，业务码在 code）。全局单例，不用 DI。
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.core.context import context
from app.schemas.generator.slides import SlidesGenRequest, SlidesTaskDetail
from app.schemas.generator.task import TaskRef, base_detail_fields
from app.schemas.response import ApiResult, BaseResponse
from app.services.generator.slides.service import slides_service

router = APIRouter(prefix="/slides", tags=["slides"])
KIND = "slides"


@router.post("/create", response_model=BaseResponse[TaskRef])
async def create_slides(req: SlidesGenRequest) -> BaseResponse[TaskRef]:
    return ApiResult.data(slides_service.submit(req))


@router.get("/get/{task_id}", response_model=BaseResponse[SlidesTaskDetail])
def get_slides(task_id: str) -> BaseResponse[SlidesTaskDetail]:
    task = context.task_store.get(task_id)
    if task is None or task.kind != KIND:
        return ApiResult.error(status.HTTP_404_NOT_FOUND, f"slides 任务 {task_id} 不存在")
    return ApiResult.data(SlidesTaskDetail(**base_detail_fields(task), result=task.result))


@router.post("/cancel/{task_id}", response_model=BaseResponse[SlidesTaskDetail])
async def cancel_slides(task_id: str) -> BaseResponse[SlidesTaskDetail]:
    task = context.task_store.get(task_id)
    if task is None or task.kind != KIND:
        return ApiResult.error(status.HTTP_404_NOT_FOUND, f"slides 任务 {task_id} 不存在")
    if not context.task_store.cancel(task_id):
        return ApiResult.error(
            status.HTTP_409_CONFLICT, f"任务已是终态（status={task.status.value}），无法取消"
        )
    return ApiResult.data(SlidesTaskDetail(**base_detail_fields(task), result=task.result))
