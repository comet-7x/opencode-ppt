"""教案服务类：封装教案产线的用例与任务执行。

依赖从全局 AppContext 取（model_provider / knowledge_source）；构造时可传入自定义
context（测试用），不传则拿全局单例。

- run：纯用例（吃已解析 knowledge → 教案），供独立端点 runner 和 PPT 管线共用。
- run_task：单阶段 runner，包 run 做任务态流转 + 异常兜底进 task.error。
"""

from __future__ import annotations

from typing import Any

from app.core.context import AppContext
from app.models.generator.lesson_plan import LessonPlanResponse
from app.schemas.generator.lesson_plan import LessonPlanResult
from app.schemas.generator.task import TaskStatus
from app.services.generator.common.knowledge import resolve_knowledge
from app.services.generator.common.task import Task, TaskStore
from app.services.generator.lesson_plan.logic import build_request, generate_lesson_plan


class LessonPlanService:
    def __init__(self, context: AppContext | None = None) -> None:
        self.context = context or AppContext()

    async def run(
        self,
        knowledge: dict[str, Any],
        point_ids: list[str],
        *,
        session_title: str,
        periods: int,
        plan_mode: str = "new_teach",
    ) -> LessonPlanResponse:
        """纯用例：knowledge + 选点 + 参数 → 教案。"""
        req = build_request(
            knowledge, point_ids,
            session_title=session_title, periods=periods, plan_mode=plan_mode,
        )
        return await generate_lesson_plan(req, self.context.model_provider.get(0.1))

    async def run_task(self, task: Task, store: TaskStore) -> None:
        """单阶段 runner：解析知识源 → 生成教案 → 落 result / error。"""
        store.update(task, status=TaskStatus.RUNNING)
        try:
            req = task.request  # LessonPlanGenRequest
            kn = resolve_knowledge(
                self.context.knowledge_source, inline=req.knowledge_json, ref=req.knowledge_path
            )
            resp = await self.run(
                kn, req.point_ids,
                session_title=req.session_title, periods=req.periods,
                plan_mode=req.plan_mode,
            )
            result = LessonPlanResult(
                course_title=kn.get("course", {}).get("title", ""),
                session_title=req.session_title,
                lesson_plan_md=resp.lesson_plan_md,
            )
            store.update(task, status=TaskStatus.SUCCEEDED, result=result)
        except Exception as exc:  # 后台任务兜底，落到 task.error
            store.update(task, status=TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
