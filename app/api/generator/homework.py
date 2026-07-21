"""作业路由（异步任务）：POST 一行提交；GET /{id} 轮询取结果。全局单例，不用 DI。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.core.context import context
from app.schemas.generator.homework import HomeworkGenRequest, HomeworkTaskDetail
from app.schemas.generator.task import TaskRef, base_detail_fields
from app.services.generator.homework.service import homework_service

router = APIRouter(prefix="/homework", tags=["homework"])
KIND = "homework"


@router.post("/create", response_model=TaskRef, status_code=202)
async def create_homework(req: HomeworkGenRequest) -> TaskRef:
    return homework_service.submit(req)


@router.get("/get/{task_id}", response_model=HomeworkTaskDetail)
def get_homework(task_id: str) -> HomeworkTaskDetail:
    task = context.task_store.get(task_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"作业任务 {task_id} 不存在")
    return HomeworkTaskDetail(**base_detail_fields(task), result=task.result)
