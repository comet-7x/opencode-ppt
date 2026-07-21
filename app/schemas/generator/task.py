"""通用任务 API 契约（所有产线共用）。

`TaskStatus` 的规范定义在 `components/task`（任务运行时），这里 import 过来供 DTO 使用，
外部仍可 `from app.schemas.generator.task import TaskStatus`。
各产线返回自己的 TaskDetail 子类（result 字段类型不同）；共同字段在此。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.components.task import Task, TaskStatus

__all__ = ["TaskDetailBase", "TaskRef", "TaskStatus", "base_detail_fields"]


class TaskRef(BaseModel):
    """POST 创建任务后立即返回。"""

    task_id: str
    kind: str
    status: TaskStatus


class TaskDetailBase(BaseModel):
    """轮询返回的公共字段；各产线子类补 result/review。"""

    task_id: str
    kind: str
    status: TaskStatus
    stage: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


def base_detail_fields(task: Task) -> dict[str, Any]:
    """抽出 TaskDetailBase 的公共字段，供各产线端点拼自己的 TaskDetail。"""
    return {
        "task_id": task.task_id,
        "kind": task.kind,
        "status": task.status,
        "stage": task.stage,
        "error": task.error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }
