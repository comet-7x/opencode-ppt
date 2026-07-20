"""通用任务 API 契约（所有产线共用）。

各产线返回自己的 TaskDetail 子类（result 字段类型不同）；共同字段在此。
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class TaskStatus(StrEnum):
    PENDING = "pending"                  # 已创建，待执行
    RUNNING = "running"                  # 执行中
    AWAITING_REVIEW = "awaiting_review"  # 到确认门，等人确认（多阶段产品用）
    SUCCEEDED = "succeeded"              # 完成，result 就绪
    FAILED = "failed"                    # 出错，error 有原因


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
