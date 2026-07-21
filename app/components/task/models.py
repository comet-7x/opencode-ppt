"""通用任务记录与状态（产品无关）。

任何领域的长任务都可复用：kind 区分业务种类，context 给多阶段 runner 存中间态，
result 放最终产物，review 放确认门产物。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"                  # 已创建，待执行
    RUNNING = "running"                  # 执行中
    AWAITING_REVIEW = "awaiting_review"  # 到确认门，等人确认（多阶段产品用）
    SUCCEEDED = "succeeded"              # 完成，result 就绪
    FAILED = "failed"                    # 出错，error 有原因


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Task:
    task_id: str
    kind: str                                     # 业务种类，如 lesson_plan / homework / ppt
    request: Any = None                           # 原始请求（供 runner 读参数 / 重跑）
    status: TaskStatus = TaskStatus.PENDING
    stage: str | None = None                      # 多阶段产品的当前阶段名
    context: dict[str, Any] = field(default_factory=dict)  # runner 中间态（多阶段用）
    result: Any = None                            # 最终产物
    review: Any = None                            # 确认门产物
    error: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
