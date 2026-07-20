"""教案路由（异步任务）：POST 创建任务→立即返回 task_id；GET /{id} 轮询取结果。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_lesson_plan_service, get_task_store
from app.schemas.generator.lesson_plan import LessonPlanGenRequest, LessonPlanTaskDetail
from app.schemas.generator.task import TaskRef
from app.services.generator.common.task import TaskStore, base_detail_fields
from app.services.generator.lesson_plan.service import LessonPlanService

router = APIRouter(prefix="/lesson-plan", tags=["lesson_plan"])
KIND = "lesson_plan"


@router.post("/create", response_model=TaskRef, status_code=202)
async def create_lesson_plan(
    req: LessonPlanGenRequest,
    store: TaskStore = Depends(get_task_store),
    service: LessonPlanService = Depends(get_lesson_plan_service),
) -> TaskRef:
    task = store.create(KIND, request=req)
    store.spawn(service.run_task(task, store))
    return TaskRef(task_id=task.task_id, kind=task.kind, status=task.status)


@router.get("/get/{task_id}", response_model=LessonPlanTaskDetail)
def get_lesson_plan(
    task_id: str, store: TaskStore = Depends(get_task_store)
) -> LessonPlanTaskDetail:
    task = store.get(task_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"教案任务 {task_id} 不存在")
    return LessonPlanTaskDetail(**base_detail_fields(task), result=task.result)
