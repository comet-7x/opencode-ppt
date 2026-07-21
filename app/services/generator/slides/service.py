"""逐页 slides 服务类：教案 md → 逐页内容 + 组装 source.md。submit 由基类提供。"""

from __future__ import annotations

from app.components.task import Task, TaskStore
from app.models.generator.slides import SlideContent
from app.schemas.generator.slides import SlidesResult
from app.schemas.generator.task import TaskStatus
from app.services.generator.common.service import BaseGenService
from app.services.generator.slides.logic import assemble_source_md, generate_slides


class SlidesService(BaseGenService):
    KIND = "slides"

    async def run(
        self, lesson_plan_md: str, *, language: str = "中文"
    ) -> list[SlideContent]:
        """纯用例：教案 md → 逐页 slides。"""
        return await generate_slides(
            lesson_plan_md, self.context.model_provider.get(0.2), language=language
        )

    async def run_task(self, task: Task, store: TaskStore) -> None:
        """单阶段 runner：生成 slides + 组装 source.md → 落 result / error。"""
        store.update(task, status=TaskStatus.RUNNING)
        try:
            req = task.request  # SlidesGenRequest
            slides = await self.run(req.lesson_plan_md, language=req.language)
            result = SlidesResult(
                slides=slides,
                source_markdown=assemble_source_md(slides, deck_title=req.deck_title),
            )
            store.update(task, status=TaskStatus.SUCCEEDED, result=result)
        except Exception as exc:  # 后台任务兜底
            store.update(task, status=TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}")


# 模块级单例：接口层直接用
slides_service = SlidesService()
