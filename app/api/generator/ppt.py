"""PPT 全管线路由（多阶段异步任务）：创建/查询/确认/下载/删除。全局单例，不用 DI。

job 是长任务：POST 一行提交，后台推进；轮询 GET 查 status/stage/review，到确认门 POST 确认。
统一返回 BaseResponse 信封（HTTP 恒 200，业务码在 code）；唯一例外是 download 成功时返回二进制文件流。
渲染属阶段二，download 在出 pptx 前返回 code=409。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
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
from app.schemas.response import ApiResult, BaseResponse
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


def _find(job_id: str) -> Task | None:
    """按 kind 隔离地取任务；取不到返回 None（由端点转成 404 信封）。"""
    task = context.task_store.get(job_id)
    return task if task is not None and task.kind == KIND else None


def _not_found(job_id: str) -> BaseResponse[Any]:
    return ApiResult.error(status.HTTP_404_NOT_FOUND, f"PPT 任务 {job_id} 不存在")


@router.post("/create", response_model=BaseResponse[TaskRef])
async def create_job(req: CreateJobRequest) -> BaseResponse[TaskRef]:
    return ApiResult.data(ppt_service.submit(req))


@router.get("/list", response_model=BaseResponse[list[PptTaskDetail]])
def list_jobs() -> BaseResponse[list[PptTaskDetail]]:
    return ApiResult.data([_detail(t) for t in context.task_store.list(KIND)])


@router.get("/get/{job_id}", response_model=BaseResponse[PptTaskDetail])
def get_job(job_id: str) -> BaseResponse[PptTaskDetail]:
    task = _find(job_id)
    if task is None:
        return _not_found(job_id)
    return ApiResult.data(_detail(task))


@router.post("/confirm/{job_id}", response_model=BaseResponse[PptTaskDetail])
async def confirm_job(job_id: str, req: ConfirmRequest) -> BaseResponse[PptTaskDetail]:
    task = _find(job_id)
    if task is None:
        return _not_found(job_id)
    try:
        ppt_service.confirm(task, context.task_store, req)
    except ValueError as e:
        return ApiResult.error(status.HTTP_409_CONFLICT, str(e))
    return ApiResult.data(_detail(task))


@router.post("/cancel/{job_id}", response_model=BaseResponse[PptTaskDetail])
def cancel_job(job_id: str) -> BaseResponse[PptTaskDetail]:
    """主动中止：跑到一半则掐掉后台协程；停在确认门则直接作废（confirm 随后会拒绝）。"""
    task = _find(job_id)
    if task is None:
        return _not_found(job_id)
    if not context.task_store.cancel(job_id):
        return ApiResult.error(
            status.HTTP_409_CONFLICT, f"任务已是终态（status={task.status.value}），无法取消"
        )
    return ApiResult.data(_detail(task))


@router.get("/download/{job_id}", response_model=None)
def download_pptx(job_id: str) -> FileResponse | BaseResponse[Any]:
    """成功时直接回二进制文件流（无法套信封）；失败时回信封。"""
    task = _find(job_id)
    if task is None:
        return _not_found(job_id)
    result = task.result
    if not result:
        return ApiResult.error(
            status.HTTP_409_CONFLICT,
            f"PPT 任务 {job_id} 尚无产物（status={task.status.value}）",
        )
    pptx_path = result.pptx_path if isinstance(result, PptResult) else result["pptx_path"]
    return FileResponse(
        pptx_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{task.request.name}.pptx",
    )


@router.delete("/delete/{job_id}", response_model=BaseResponse[Any])
def delete_job(job_id: str) -> BaseResponse[Any]:
    if _find(job_id) is None:
        return _not_found(job_id)
    context.task_store.delete(job_id)
    return ApiResult.ok(msg=f"PPT 任务 {job_id} 已删除")
