"""逐页 slides 生成（教案 → 结构化逐页内容）+ 组装 source.md。

这是 draft 里没有的新环节：把 markdown 教案切成一页一页的结构化内容，供人逐页审改，
确认后组装成一份 source.md 交给 ppt-master 渲染。

关键：组装的 source.md 用「一个 ## 标题 = 一页」的显式结构，配合渲染提示词锁死
「严格一节一页、不重新分页」，避免 skill 的 Strategist 推翻逐页确认结果。
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ValidationError


class SlideLayout(StrEnum):
    COVER = "cover"  # 封面
    TOC = "toc"  # 目录
    CONTENT_TEXT = "content_text"  # 纯文字内容页
    CONTENT_IMAGE = "content_image"  # 纯图内容页
    CONTENT_IMAGE_TEXT = "content_image_text"  # 图文页
    TIMELINE = "timeline"  # 时间线
    THREE_PILLARS = "three_pillars"  # 三栏
    QUOTE = "quote"  # 引用
    SECTION = "section"  # 章节过渡
    KEY_METRICS = "key_metrics"  # 关键指标
    END = "end"  # 结束页


class SlideContent(BaseModel):
    index: int  # 页序，1-based
    title: str  # 页标题
    body: str = ""  # 正文（markdown 要点）
    notes: str | None = None  # 演讲稿备注
    layout: SlideLayout | None = None  # 版式提示（帮 skill 选模板页）


class SlidesResponse(BaseModel):
    slides: list[SlideContent]

    @classmethod
    def parse(cls, text: str) -> SlidesResponse | None:
        blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        cand = blocks[-1] if blocks else text.strip()
        try:
            return cls.model_validate_json(cand)
        except ValidationError:
            return None
