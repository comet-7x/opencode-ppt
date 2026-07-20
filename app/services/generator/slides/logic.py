"""逐页 slides 生成节点（无状态）：Markdown 教案 → 结构化逐页内容 + 组装 source.md。

把教案切成一页一页的结构化内容，供人逐页审改，确认后组装成一份 source.md 交给
（阶段二）ppt-master 渲染。layout 用英文键（见 SlideLayout）。

关键：组装的 source.md 用「一个 ## 标题 = 一页」的显式结构，配合渲染提示词锁死
「严格一节一页、不重新分页」，避免 skill 的 Strategist 推翻逐页确认结果。
"""

from __future__ import annotations

from typing import Any

from camel.agents import ChatAgent
from camel.responses import ChatAgentResponse

from app.models.generator.slides import SlideContent, SlidesResponse
from app.services.generator.common.errors import ModelError, ParseError

SLIDES_SYSTEM_PROMPT = """\
你是演示文稿设计专家。用户会给你一份 Markdown 教案和目标语言。请把它拆解成**逐页幻灯片内容**。

要求：
1. 一页只讲一个要点/环节，标题精炼；正文用 markdown 要点（- 列表 / 短句），不要整段照抄教案。
2. 第一页是封面(layout=cover)，第二页可为目录(layout=toc)，最后一页是结束页(layout=end)。
3. 中间内容页按教案的教学过程/环节切分，可用的 layout（英文键）：
   content_text（纯文字）、content_image_text（图文）、timeline（时间线）、
   three_pillars（三点并列）、quote（引用）、section（章节过渡）、key_metrics（关键数字）。
4. 每页给 notes（该页演讲要点，1-3 句），供讲稿使用。
5. 语言用用户指定的目标语言。页数控制在 8-16 页之间，视教案体量而定。

**只输出一个 ```json 代码块**，layout 必须用上面的英文键，结构：
{"slides": [
  {"index": 1, "title": "...", "body": "- 要点\\n- 要点", "notes": "讲稿要点", "layout": "cover"},
  ...
]}
index 从 1 连续编号。
"""


async def generate_slides(
    lesson_plan_md: str, model: Any, *, language: str = "中文"
) -> list[SlideContent]:
    """教案 → 逐页 slides。model 由 service 按温度取好传入。"""
    prompt = f"目标语言：{language}\n\n【教案】\n{lesson_plan_md}"
    agent = ChatAgent(model=model, system_message=SLIDES_SYSTEM_PROMPT)
    resp = await agent.astep(prompt)
    if not isinstance(resp, ChatAgentResponse) or not resp.msgs:
        raise ModelError(f"模型返回空/异常响应：{resp!r}")
    parsed = SlidesResponse.parse(resp.msgs[0].content)
    if parsed is None:
        raise ParseError("无法解析 SlidesResponse JSON：\n" + resp.msgs[0].content[:500])
    # 规整 index，保证连续 1..N
    slides = parsed.slides
    for i, s in enumerate(slides, 1):
        s.index = i
    return slides


def assemble_source_md(slides: list[SlideContent], *, deck_title: str = "") -> str:
    """把（人确认后的）逐页 slides 组装成一份 source.md。

    结构：一个 `## <标题>` = 一页。每页附 layout 提示注释和演讲稿注释。
    渲染提示词会据此锁死「一节一页、不重排」。
    """
    lines: list[str] = []
    if deck_title:
        lines += [f"# {deck_title}", ""]
    for s in slides:
        layout = f" (layout: {s.layout.value})" if s.layout else ""
        lines.append(f"## {s.title}{layout}")
        if s.body.strip():
            lines += ["", s.body.rstrip(), ""]
        if s.notes:
            lines += [f"<!-- notes: {s.notes} -->", ""]
    return "\n".join(lines).rstrip() + "\n"
