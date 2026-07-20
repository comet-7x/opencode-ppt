"""逐页 slides 路由（异步任务）：POST 创建→返回 task_id；GET /{id} 轮询取结果。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_slides_service, get_task_store
from app.schemas.generator.slides import SlidesGenRequest, SlidesTaskDetail
from app.schemas.generator.task import TaskRef
from app.services.generator.common.task import TaskStore, base_detail_fields
from app.services.generator.slides.service import SlidesService

router = APIRouter(prefix="/slides", tags=["slides"])
KIND = "slides"


@router.post("/create", response_model=TaskRef, status_code=202)
async def create_slides(
    req: SlidesGenRequest,
    store: TaskStore = Depends(get_task_store),
    service: SlidesService = Depends(get_slides_service),
) -> TaskRef:
    task = store.create(KIND, request=req)
    store.spawn(service.run_task(task, store))
    return TaskRef(task_id=task.task_id, kind=task.kind, status=task.status)


@router.get("/get/{task_id}", response_model=SlidesTaskDetail)
def get_slides(
    task_id: str, store: TaskStore = Depends(get_task_store)
) -> SlidesTaskDetail:
    task = store.get(task_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"slides 任务 {task_id} 不存在")
    return SlidesTaskDetail(**base_detail_fields(task), result=task.result)
