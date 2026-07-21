"""PPT 全管线路由（多阶段异步任务）：创建/查询/确认/下载/删除。全局单例，不用 DI。

job 是长任务：POST 一行提交，后台推进；轮询 GET 查 status/stage/review，到确认门 POST 确认。
渲染属阶段二，download 在出 pptx 前返回 409。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.components.task import Task
from app.core.context import context
from app.schemas.generator.ppt import (
    ConfirmRequest,
    CreateJobRequest,
    PptResult,
    PptStage,
    PptTaskDetail,
    ReviewArtifact,
)
from app.schemas.generator.task import TaskRef, TaskStatus, base_detail_fields
from app.services.generator.ppt.service import ppt_service

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


def _require(job_id: str) -> Task:
    task = context.task_store.get(job_id)
    if task is None or task.kind != KIND:
        raise HTTPException(404, f"PPT 任务 {job_id} 不存在")
    return task


@router.post("/create", response_model=TaskRef, status_code=202)
async def create_job(req: CreateJobRequest) -> TaskRef:
    return ppt_service.submit(req)


@router.get("/list", response_model=list[PptTaskDetail])
def list_jobs() -> list[PptTaskDetail]:
    return [_detail(t) for t in context.task_store.list(KIND)]


@router.get("/get/{job_id}", response_model=PptTaskDetail)
def get_job(job_id: str) -> PptTaskDetail:
    return _detail(_require(job_id))


@router.post("/confirm/{job_id}", response_model=PptTaskDetail)
async def confirm_job(job_id: str, req: ConfirmRequest) -> PptTaskDetail:
    task = _require(job_id)
    try:
        ppt_service.confirm(task, context.task_store, req)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return _detail(task)


@router.get("/download/{job_id}")
def download_pptx(job_id: str) -> FileResponse:
    task = _require(job_id)
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
def delete_job(job_id: str) -> None:
    _require(job_id)
    context.task_store.delete(job_id)
