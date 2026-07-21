"""slides / lesson_plan logic 纯函数单测（source.md 组装、教案请求映射、输出解析）。"""

from __future__ import annotations

from app.models.generator.slides import SlideContent, SlideLayout, SlidesResponse
from app.services.generator.lesson_plan.logic import build_request as lp_build
from app.services.generator.slides.logic import assemble_source_md


# ————————————————————— slides 输出解析 ————————————————————— #
def test_slides_parse_from_code_block() -> None:
    text = '```json\n{"slides":[{"index":1,"title":"封面","layout":"cover"}]}\n```'
    parsed = SlidesResponse.parse(text)
    assert parsed is not None
    assert parsed.slides[0].layout is SlideLayout.COVER


def test_slides_parse_rejects_unknown_layout() -> None:
    assert SlidesResponse.parse('{"slides":[{"index":1,"title":"x","layout":"封面"}]}') is None


def test_slides_parse_invalid_returns_none() -> None:
    assert SlidesResponse.parse("not json") is None


# ————————————————————— source.md 组装（一节一页）————————————————————— #
def _slides() -> list[SlideContent]:
    return [
        SlideContent(index=1, title="封面", layout=SlideLayout.COVER),
        SlideContent(index=2, title="概念", body="- 定义\n- 意义", notes="讲两分钟",
                     layout=SlideLayout.CONTENT_TEXT),
    ]


def test_assemble_one_heading_per_slide() -> None:
    md = assemble_source_md(_slides(), deck_title="导论")
    assert md.startswith("# 导论")
    assert md.count("## ") == 2  # 一节 = 一页，页数 = 标题数


def test_assemble_embeds_layout_and_notes() -> None:
    md = assemble_source_md(_slides(), deck_title="导论")
    assert "(layout: cover)" in md
    assert "(layout: content_text)" in md
    assert "<!-- notes: 讲两分钟 -->" in md


def test_assemble_skips_empty_body_and_notes() -> None:
    md = assemble_source_md([SlideContent(index=1, title="空页")])
    assert "## 空页" in md
    assert "notes" not in md
    assert md.endswith("\n")


def test_assemble_without_deck_title_has_no_h1() -> None:
    md = assemble_source_md(_slides())
    assert not md.startswith("# ")


def test_assemble_empty_list() -> None:
    assert assemble_source_md([]) == "\n"


# ————————————————————— 教案请求映射 ————————————————————— #
def test_lesson_plan_build_request_maps_session_fields(knowledge: dict) -> None:
    req = lp_build(knowledge, ["K002", "K001"], session_title="导论", periods=2)
    s = req.session
    assert s.course_title == "海洋管理概论"
    assert s.session_title == "导论"
    assert s.periods == 2
    assert s.minutes_per_period == 45
    assert s.plan_mode == "new_teach"                      # 默认英文键
    assert s.audience == "本科生"                           # 取自 course.profile.授课对象
    assert s.course_objectives == ["理解海洋治理", "掌握资源管理"]
    assert [p.id for p in req.input_points] == ["K002", "K001"]  # 保持传入顺序


def test_lesson_plan_build_request_accepts_plan_mode(knowledge: dict) -> None:
    req = lp_build(knowledge, ["K001"], session_title="复习", periods=1,
                   plan_mode="review_comprehensive", minutes_per_period=50)
    assert req.session.plan_mode == "review_comprehensive"
    assert req.session.minutes_per_period == 50


def test_lesson_plan_build_request_tolerates_sparse_course() -> None:
    req = lp_build({"points": []}, [], session_title="x", periods=1)
    assert req.session.course_title == ""
    assert req.session.audience == ""
    assert req.session.course_objectives == []
