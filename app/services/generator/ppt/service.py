"""PPT 全管线服务类（多阶段任务 runner / 状态机）。

组合 LessonPlanService + SlidesService（默认从同一 context 构建，测试可注入假子服务），
不重复实现生成逻辑；只管状态流转、确认门、异常兜底。中间态存 task.context
（lesson_plan_md / slides / source_markdown）。
渲染（RENDER）属阶段二（docker）：本版留桩——组装好 source_markdown 后标 FAILED。
"""

from __future__ import annotations

from app.components.task import Task, TaskStore
from app.core.context import AppContext
from app.core.context import context as app_context
from app.schemas.generator.ppt import (
    ConfirmAction,
    ConfirmRequest,
    CreateJobRequest,
    PptStage,
)
from app.schemas.generator.task import TaskRef, TaskStatus
from app.services.generator.common.knowledge import resolve_knowledge
from app.services.generator.lesson_plan.service import LessonPlanService
from app.services.generator.slides.logic import assemble_source_md
from app.services.generator.slides.service import SlidesService


class PptService:
    def __init__(
        self,
        context: AppContext | None = None,
        *,
        lesson_plan: LessonPlanService | None = None,
        slides: SlidesService | None = None,
    ) -> None:
        self.context = context if context is not None else app_context
        self._lesson_plan = lesson_plan or LessonPlanService(self.context)
        self._slides = slides or SlidesService(self.context)

    def submit(self, request: CreateJobRequest) -> TaskRef:
        """接口层一行调用：建 PPT 任务（按入口选起始阶段）+ 后台推进 + 返回引用。"""
        store = self.context.task_store
        # 直接给 source_markdown → 从渲染阶段起步；否则从教案起步
        start_stage = PptStage.RENDER if request.source_markdown else PptStage.LESSON_PLAN
        ctx = {"source_markdown": request.source_markdown} if request.source_markdown else {}
        task = store.create("ppt", request=request, stage=start_stage, context=ctx)
        self.start(task, store)
        return TaskRef(task_id=task.task_id, kind=task.kind, status=task.status)

    @staticmethod
    def _fail(store: TaskStore, task: Task, exc: Exception) -> None:
        store.update(task, status=TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}")

    # ————————————————————— 启动 ————————————————————— #
    def start(self, task: Task, store: TaskStore) -> None:
        """创建后调用：直接给 source_markdown 则渲染，否则从教案起步（后台推进）。"""
        if task.stage == PptStage.RENDER:
            store.spawn(task, self._run_render(task, store))
        else:
            store.spawn(task, self._run_lesson_plan_stage(task, store))

    # ————————————————————— 阶段：教案 ————————————————————— #
    async def _run_lesson_plan_stage(
        self, task: Task, store: TaskStore, feedback: str | None = None
    ) -> None:
        store.update(task, status=TaskStatus.RUNNING, stage=PptStage.LESSON_PLAN, error=None)
        try:
            req = task.request
            kn = resolve_knowledge(
                self.context.knowledge_source, inline=req.knowledge_json, ref=req.knowledge_path
            )
            _ = feedback  # 预留：后续把 feedback 拼进 prompt 重生成
            resp = await self._lesson_plan.run(
                kn, req.point_ids,
                session_title=req.session_title, periods=req.periods,
                plan_mode=req.plan_mode,
            )
            task.context["lesson_plan_md"] = resp.lesson_plan_md
            if req.confirm_lesson_plan:
                store.update(task, status=TaskStatus.AWAITING_REVIEW)
            else:
                await self._run_slides_stage(task, store)
        except Exception as exc:
            self._fail(store, task, exc)

    # ————————————————————— 阶段：逐页 slides ————————————————————— #
    async def _run_slides_stage(self, task: Task, store: TaskStore) -> None:
        store.update(task, status=TaskStatus.RUNNING, stage=PptStage.SLIDES, error=None)
        try:
            slides = await self._slides.run(
                task.context.get("lesson_plan_md", ""), language=task.request.language
            )
            task.context["slides"] = slides
            if task.request.confirm_slides:
                store.update(task, status=TaskStatus.AWAITING_REVIEW)
            else:
                await self._enter_render(task, store)
        except Exception as exc:
            self._fail(store, task, exc)

    async def _enter_render(self, task: Task, store: TaskStore) -> None:
        """把（确认后的）slides 组装成 source.md，进入渲染。"""
        src = assemble_source_md(
            task.context.get("slides", []), deck_title=task.request.session_title
        )
        task.context["source_markdown"] = src
        await self._run_render(task, store)

    # ————————————————————— 阶段：渲染（阶段二留桩）————————————————————— #
    async def _run_render(self, task: Task, store: TaskStore) -> None:
        store.update(task, status=TaskStatus.RUNNING, stage=PptStage.RENDER)
        if not task.context.get("source_markdown"):
            store.update(task, status=TaskStatus.FAILED, error="渲染阶段缺少 source_markdown")
            return
        # 阶段二：起容器 → 确定性初始化项目 → run_until → chown → 出 pptx。本版未接入。
        store.update(
            task,
            status=TaskStatus.FAILED,
            error="渲染(source.md→pptx)属阶段二，docker 未接入；source_markdown 已就绪",
        )

    # ————————————————————— 确认门推进 ————————————————————— #
    def confirm(self, task: Task, store: TaskStore, req: ConfirmRequest) -> None:
        """在确认门上推进。approve→下一阶段；regenerate→带反馈重生成本阶段。"""
        if task.status != TaskStatus.AWAITING_REVIEW:
            raise ValueError(f"任务当前不在确认门（status={task.status.value}）")

        if task.stage == PptStage.LESSON_PLAN:
            if req.action == ConfirmAction.REGENERATE:
                store.spawn(task, self._run_lesson_plan_stage(task, store, feedback=req.feedback))
            else:  # approve
                if req.edited_content is not None:
                    task.context["lesson_plan_md"] = req.edited_content
                store.spawn(task, self._run_slides_stage(task, store))
            return

        if task.stage == PptStage.SLIDES:
            if req.action == ConfirmAction.REGENERATE:
                store.spawn(task, self._run_slides_stage(task, store))
            else:  # approve
                if req.edited_slides is not None:
                    task.context["slides"] = req.edited_slides
                store.spawn(task, self._enter_render(task, store))
            return

        raise ValueError(f"阶段 {task.stage} 无确认门")


# 模块级单例：接口层直接用
ppt_service = PptService()
