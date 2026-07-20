"""FastAPI 依赖注入 providers。

依赖对象（model_provider / knowledge_source / task_store）都收在全局 AppContext；
服务自行从 context 取，故这里的工厂很薄：建服务 / 取 task_store。保留 Depends 工厂
是为了 testability（测试用 app.dependency_overrides 换假服务/假 store）。
"""

from __future__ import annotations

from app.core.context import AppContext
from app.services.generator.common.task import TaskStore
from app.services.generator.homework.service import HomeworkService
from app.services.generator.lesson_plan.service import LessonPlanService
from app.services.generator.ppt.service import PptService
from app.services.generator.slides.service import SlidesService


def get_task_store() -> TaskStore:
    """全局任务表（来自 AppContext）。"""
    return AppContext().task_store


def get_lesson_plan_service() -> LessonPlanService:
    return LessonPlanService()


def get_slides_service() -> SlidesService:
    return SlidesService()


def get_homework_service() -> HomeworkService:
    return HomeworkService()


def get_ppt_service() -> PptService:
    return PptService()
