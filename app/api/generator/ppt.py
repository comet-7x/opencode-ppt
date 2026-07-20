"""PPT 全管线路由（多阶段异步任务）：创建/查询/确认/下载/删除。

job 是长任务：POST 立即返回 TaskRef，后台推进；轮询 GET 查 status/stage/review，
到确认门用 POST /confirm 推进。渲染属阶段二，download 在出 pptx 前返回 409。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.dependencies import get_ppt_service, get_task_store
from app.schemas.generator.ppt import (
    ConfirmRequest,
    CreateJobRequest,
    PptResult,
    PptStage,
    PptTaskDetail,
    ReviewArtifact,
)
from app.schemas.generator.task import TaskRef, TaskStatus
from app.services.generator.common.task import Task, TaskStore, base_detail_fields
from app.services.generator.ppt.service import PptService

router = APIRouter(prefix="/ppt", tags=["ppt"])
KIND = "ppt"


def _review_of(task: Task) -> ReviewArtifact | None:
    if task.status != TaskStatus.AWAITING_REVIEW:
        return None
    if task.stage == PptStage.LESSON_PLAN:
        return ReviewArtifact(stage=PptStage.LESSON_PLAN, content=task.context.get("lesson_plan_md"))
    if task.stage == PptStage.SLIDES:
        return ReviewArtifact(stage=PptStage.SLIDES, slides=task.context.get("slides"))
    return None


def _detail(task: Task) -> PptTaskDetail:
    result = None
    if task.result is not None:
        result = task.result if isinstance(task.result, PptResult) else PptResult(**task.result)
    return PptTaskDetail(
        **base_detail_fields(task),
        review=_review_of(task),
        result=result,
        source_markdown=task.context.get("source_markdown"),
    )


@router.post("/create", response_model=TaskRef, status_code=202)
async def create_job(
    req: CreateJobRequest,
    store: TaskStore = Depends(get_task_store),
    service: PptService = Depends(get_ppt_service),
) -> TaskRef:
    # 直接给 source_markdown → 从渲染阶段起步；否则从教案起步
    start_stage = PptStage.RENDER if req.source_markdown else PptStage.LESSON_PLAN
    context = {"source_markdown": req.source_markdown} if req.source_markdown else {}
    task = store.create(KIND, request=req, stage=start_stage, context=context)
    service.start(task, store)  # 后台推进，立即返回
    return TaskRef(task_id=task.task_id, kind=task.kind, status=task.status)


@router.get("/list", response_model=list[PptTaskDetail])
def list_jobs(store: TaskStore = Depends(get_task_store)) -> list[PptTaskDetail]:
    return [_detail(t) for t in store.list(KIND)]


@router.get("/get/{job_id}", response_model=PptTaskDetail)
def get_job(job_id: str, store: TaskStore = Depends(get_task_store)) -> PptTaskDetail:
    task = store.get(job_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"PPT 任务 {job_id} 不存在")
    return _detail(task)


@router.post("/confirm/{job_id}", response_model=PptTaskDetail)
async def confirm_job(
    job_id: str,
    req: ConfirmRequest,
    store: TaskStore = Depends(get_task_store),
    service: PptService = Depends(get_ppt_service),
) -> PptTaskDetail:
    task = store.get(job_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"PPT 任务 {job_id} 不存在")
    try:
        service.confirm(task, store, req)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return _detail(task)


@router.get("/download/{job_id}")
def download_pptx(job_id: str, store: TaskStore = Depends(get_task_store)) -> FileResponse:
    task = store.get(job_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"PPT 任务 {job_id} 不存在")
    result = task.result
    if not result:
        raise HTTPException(409, f"PPT 任务 {job_id} 尚无产物（status={task.status.value}）")
    pptx_path = result.pptx_path if isinstance(result, PptResult) else result["pptx_path"]
    return FileResponse(
        pptx_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{task.request.name}.pptx",
    )


@router.delete("/delete/{job_id}", status_code=204)
def delete_job(job_id: str, store: TaskStore = Depends(get_task_store)) -> None:
    task = store.get(job_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"PPT 任务 {job_id} 不存在")
    store.delete(job_id)
