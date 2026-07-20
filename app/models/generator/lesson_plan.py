"""教案生成（适配自 draft/test_camel_lession_plan.py）。

三段式：数据（选中知识点）+ 参数（SessionParams）+ 指令（system prompt）。
模型从 settings 注入，函数化以便服务调用。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .knowledge import KnowledgePoint


class SessionParams(BaseModel):
    course_title: str
    session_title: str  # 课题
    periods: int  # 学时数
    minutes_per_period: int = 45
    # 教学模式: 新授、复习综合、单元小结
    plan_mode: Literal["new_teach", "review_comprehensive", "unit_summary"] = "new_teach"
    audience: str = ""  # 授课对象
    course_objectives: list[str] = []  # 课程目标文本，供“素养目标”回溯


class LessonPlanRequest(BaseModel):
    session: SessionParams
    input_points: list[KnowledgePoint]  # Pydantic v2 可直接容纳 dataclass


class LessonPlanResponse(BaseModel):
    lesson_plan_md: str
