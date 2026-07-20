"""作业服务类：命题(+可选 L1/L2 校验闭环) → 结构化题目 + 卷子/答案 markdown。submit 由基类提供。"""

from __future__ import annotations

from typing import Any

from app.schemas.generator.homework import HomeworkResult
from app.schemas.generator.task import TaskStatus
from app.services.generator.common.knowledge import all_points, resolve_knowledge
from app.services.generator.common.service import BaseGenService
from app.services.generator.common.task import Task, TaskStore
from app.services.generator.homework.logic import (
    build_request,
    generate_homework,
    generate_verified_homework,
    render_answer_key,
    render_worksheet,
)


class HomeworkService(BaseGenService):
    KIND = "homework"

    async def run(
        self,
        knowledge: dict[str, Any],
        point_ids: list[str],
        *,
        session_title: str,
        mc: tuple[int, int] = (3, 4),
        fill: tuple[int, int] = (2, 3),
        subjective: tuple[int, int] = (1, 10),
        difficulty: str = "advanced",
        lesson_plan_md: str | None = None,
        verify: bool = True,
        max_retries: int = 2,
    ) -> HomeworkResult:
        """纯用例：knowledge + 题型配额 → 一套作业。"""
        req = build_request(
            knowledge, point_ids,
            session_title=session_title, mc=mc, fill=fill, subjective=subjective,
            difficulty=difficulty, lesson_plan_md=lesson_plan_md,
        )
        get_model = self.context.model_provider.get
        if verify:
            hw, results = await generate_verified_homework(
                req, all_points(knowledge), get_model, max_retries=max_retries
            )
        else:
            hw, results = await generate_homework(req, get_model), None

        return HomeworkResult(
            course_title=req.course_title,
            session_title=req.session_title,
            total_score=hw.total_score,
            difficulty=req.difficulty,
            questions=hw.questions,
            worksheet_md=render_worksheet(req, hw),
            answer_key_md=render_answer_key(req, hw),
            verify_results=results,
        )

    async def run_task(self, task: Task, store: TaskStore) -> None:
        """单阶段 runner：解析知识源 → 命题闭环 → 落 result / error。"""
        store.update(task, status=TaskStatus.RUNNING)
        try:
            req = task.request  # HomeworkGenRequest
            kn = resolve_knowledge(
                self.context.knowledge_source, inline=req.knowledge_json, ref=req.knowledge_path
            )
            result = await self.run(
                kn, req.point_ids,
                session_title=req.session_title,
                mc=req.mc, fill=req.fill, subjective=req.subjective,
                difficulty=req.difficulty, lesson_plan_md=req.lesson_plan_md,
                verify=req.verify,
            )
            store.update(task, status=TaskStatus.SUCCEEDED, result=result)
        except Exception as exc:  # 后台任务兜底
            store.update(task, status=TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}")


# 模块级单例：接口层直接用
homework_service = HomeworkService()
