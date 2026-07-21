"""逐页 slides 路由（异步任务）：POST 一行提交；GET /{id} 轮询取结果。全局单例，不用 DI。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.context import context
from app.schemas.generator.slides import SlidesGenRequest, SlidesTaskDetail
from app.schemas.generator.task import TaskRef, base_detail_fields
from app.services.generator.slides.service import slides_service

router = APIRouter(prefix="/slides", tags=["slides"])
KIND = "slides"


@router.post("/create", response_model=TaskRef, status_code=202)
async def create_slides(req: SlidesGenRequest) -> TaskRef:
    return slides_service.submit(req)


@router.get("/get/{task_id}", response_model=SlidesTaskDetail)
def get_slides(task_id: str) -> SlidesTaskDetail:
    task = context.task_store.get(task_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"slides 任务 {task_id} 不存在")
    return SlidesTaskDetail(**base_detail_fields(task), result=task.result)
