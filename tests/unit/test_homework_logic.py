"""作业 logic 纯函数单测：选点组装、分值回填、L2 检索、掩码/答案、解析、markdown 渲染。

全部不连模型（这些函数本就是确定性的），覆盖 homework/logic.py 里最核心的算法。
"""

from __future__ import annotations

import pytest

from app.models.generator.homework import (
    HomeworkResponse,
    MCOption,
    Question,
    QuestionSpec,
    VerifyResult,
)
from app.services.generator.common.knowledge import all_points, select_points
from app.services.generator.homework.logic import (
    DIFF_ZH,
    TYPE_ZH,
    _critique,
    _enforce_scores,
    _mask_question,
    _norm,
    _parse_question,
    _render_sources,
    _shingles,
    _true_answer,
    build_request,
    render_answer_key,
    render_worksheet,
    retrieve_l2,
)


def _choice(stem: str = "题干", correct: str = "A", pids: list[str] | None = None) -> Question:
    return Question(
        type="choice", stem=stem, score=0, difficulty="advanced",
        source_point_ids=pids or ["K001"],
        options=[
            MCOption(label=lab, text=f"选项{lab}", is_correct=(lab == correct), analysis=f"{lab}解析")
            for lab in "ABCD"
        ],
    )


def _fill(answers: list[str] | None = None) -> Question:
    return Question(
        type="fill_blank", stem="海洋管理的对象是____。", score=0, difficulty="basic",
        source_point_ids=["K001"], answers=answers or ["事务"], analysis="依据定义",
    )


def _subjective() -> Question:
    return Question(
        type="subjective", stem="论述海洋资源管理体系。", score=0, difficulty="challenge",
        source_point_ids=["K002"], scoring_points=["分类", "制度"], analysis="分点作答",
    )


# ————————————————————— build_request ————————————————————— #
def test_build_request_maps_course_and_specs(knowledge: dict) -> None:
    req = build_request(
        knowledge, ["K002", "K001"], session_title="导论",
        mc=(3, 4), fill=(2, 3), subjective=(1, 10), difficulty="advanced",
    )
    assert req.course_title == "海洋管理概论"
    assert req.session_title == "导论"
    assert [p.id for p in req.points] == ["K002", "K001"]  # 保持传入顺序
    assert req.mc == QuestionSpec(count=3, score_each=4)
    assert req.total_score == 3 * 4 + 2 * 3 + 1 * 10  # 24


def test_build_request_carries_lesson_plan(knowledge: dict) -> None:
    req = build_request(
        knowledge, ["K001"], session_title="导论", mc=(1, 1), fill=(0, 0),
        subjective=(0, 0), difficulty="basic", lesson_plan_md="# 教案",
    )
    assert req.lesson_plan_md == "# 教案"


# ————————————————————— 分值确定性回填 ————————————————————— #
def test_enforce_scores_backfills_by_type_and_total(knowledge: dict) -> None:
    req = build_request(
        knowledge, ["K001"], session_title="导论",
        mc=(2, 4), fill=(1, 3), subjective=(1, 10), difficulty="advanced",
    )
    hw = HomeworkResponse(
        questions=[_choice(), _choice(), _fill(), _subjective()], total_score=999
    )
    _enforce_scores(req, hw)
    assert [q.score for q in hw.questions] == [4, 4, 3, 10]  # 按题型回填，不信模型
    assert hw.total_score == req.total_score == 21


# ————————————————————— 输出解析 ————————————————————— #
def test_homework_response_parse_from_code_block() -> None:
    text = '前言\n```json\n{"questions": [], "total_score": 0}\n```\n后记'
    hw = HomeworkResponse.parse(text)
    assert hw is not None and hw.total_score == 0


def test_homework_response_parse_bare_json() -> None:
    assert HomeworkResponse.parse('{"questions": [], "total_score": 5}') is not None


def test_homework_response_parse_invalid_returns_none() -> None:
    assert HomeworkResponse.parse("这不是 JSON") is None
    assert HomeworkResponse.parse('{"questions": [{"type": "选择"}]}') is None  # 中文键不合法


def test_parse_question_picks_last_code_block() -> None:
    text = (
        '```json\n{"type":"choice","stem":"旧","score":1,"difficulty":"basic"}\n```\n'
        '```json\n{"type":"fill_blank","stem":"新","score":1,"difficulty":"basic"}\n```'
    )
    q = _parse_question(text)
    assert q is not None and q.stem == "新"


def test_parse_question_invalid_returns_none() -> None:
    assert _parse_question("no json here") is None


def test_verifier_out_parse_valid_and_invalid() -> None:
    from app.models.generator.homework import VerifierOut

    ok = VerifierOut.parse('```json\n{"independent_answer":"A","grounded":true,'
                           '"unsupported_claims":[],"notes":"n"}\n```')
    assert ok is not None and ok.independent_answer == "A"
    assert VerifierOut.parse("校验器今天不讲 JSON") is None   # 解析失败 → None（上层降级）


# ————————————————————— L2 检索 ————————————————————— #
def test_retrieve_l2_excludes_cited_and_prefers_neighbors(knowledge: dict) -> None:
    pts = all_points(knowledge)
    l2 = retrieve_l2(_choice(pids=["K001"]), pts, cited_ids={"K001"})
    assert [p.id for p in l2] == ["K002"]  # K001 被引已排除；K002 是其 related 邻居


def test_retrieve_l2_respects_k_limit(knowledge: dict) -> None:
    pts = all_points(knowledge)
    assert len(retrieve_l2(_choice(pids=["K001"]), pts, cited_ids={"K001"}, k=0)) == 0


def test_shingles_basic() -> None:
    assert _shingles("海洋管理") == {"海洋", "洋管", "管理"}
    assert _shingles("海") == {"海"}  # 短于 n 时退化为整串


# ————————————————————— 掩码 / 真答案 / 归一化 ————————————————————— #
def test_mask_question_hides_answer_for_choice() -> None:
    masked = _mask_question(_choice(correct="B"))
    assert masked.startswith("[选择题]")
    assert "选项A" in masked and "选项B" in masked
    assert "is_correct" not in masked and "解析" not in masked  # 不泄漏答案/解析


def test_mask_question_by_type() -> None:
    assert _mask_question(_fill()).startswith("[填空题]")
    assert _mask_question(_subjective()).startswith("[主观题]")


def test_true_answer_per_type() -> None:
    assert _true_answer(_choice(correct="C")) == "C"
    assert _true_answer(_fill(["甲", "乙"])) == "甲；乙"
    assert _true_answer(_subjective()) == ""  # 主观题不做机器比对


def test_norm_strips_separators_and_upcases() -> None:
    assert _norm(" a, c ；b、") == "ACB"


# ————————————————————— 校验意见拼装 ————————————————————— #
def test_critique_reports_answer_conflict_and_claims() -> None:
    r = VerifyResult(
        index=1, type="choice", verdict="fail", generator_answer="A",
        independent_answer="C", answer_match=False, grounded=False,
        unsupported_claims=["某论断无依据"], l1_ids=["K001"], l2_ids=[], notes="需复核",
    )
    text = _critique(r)
    assert "冲突" in text and "「C」" in text and "「A」" in text
    assert "某论断无依据" in text and "需复核" in text


def test_critique_has_fallback_when_nothing_specific() -> None:
    r = VerifyResult(
        index=1, type="subjective", verdict="warn", generator_answer="",
        independent_answer="", answer_match=None, grounded=True,
        unsupported_claims=[], l1_ids=[], l2_ids=[], notes="",
    )
    assert _critique(r).strip().startswith("-")  # 有兜底文案


def test_render_sources_labels_l1_l2(knowledge: dict) -> None:
    pts = all_points(knowledge)
    text = _render_sources([pts[0]], [pts[1]])
    assert "【被引知识点 L1】" in text and "【相关知识点 L2】" in text
    assert "[L1] K001" in text and "[L2] K002" in text


# ————————————————————— markdown 渲染（英文键 → 中文标签）————————————————————— #
@pytest.fixture
def rendered(knowledge: dict) -> tuple[str, str]:
    req = build_request(
        knowledge, ["K001", "K002"], session_title="导论",
        mc=(1, 4), fill=(1, 3), subjective=(1, 10), difficulty="advanced",
    )
    hw = HomeworkResponse(questions=[_choice(correct="B"), _fill(["事务"]), _subjective()], total_score=0)
    _enforce_scores(req, hw)
    return render_worksheet(req, hw), render_answer_key(req, hw)


def test_worksheet_uses_chinese_labels_and_hides_answers(rendered: tuple[str, str]) -> None:
    ws, _ = rendered
    assert "一、选择题" in ws and "二、填空题" in ws and "三、主观题" in ws
    assert "难度：进阶" in ws  # advanced → 进阶
    assert "满分 17 分" in ws  # 4+3+10
    assert "选项B" in ws
    assert "【答案】" not in ws and "解析" not in ws  # 卷子不含答案


def test_answer_key_has_answers_analysis_and_sources(rendered: tuple[str, str]) -> None:
    _, ak = rendered
    assert "【答案】B" in ak
    assert "【答案】事务" in ak
    assert "【得分要点】" in ak and "分类" in ak
    assert "✓" in ak and "✗" in ak  # 逐项对错标记
    assert "考查知识点：K001" in ak


def test_label_maps_cover_all_enum_keys() -> None:
    assert set(TYPE_ZH) == {"choice", "fill_blank", "subjective"}
    assert set(DIFF_ZH) == {"basic", "advanced", "challenge"}


def test_render_skips_absent_question_types(knowledge: dict) -> None:
    """只有选择题时，不应渲染空的填空/主观小节。"""
    req = build_request(
        knowledge, ["K001"], session_title="导论",
        mc=(1, 4), fill=(0, 0), subjective=(0, 0), difficulty="basic",
    )
    hw = HomeworkResponse(questions=[_choice()], total_score=0)
    _enforce_scores(req, hw)
    ws = render_worksheet(req, hw)
    assert "一、选择题" in ws
    assert "填空题" not in ws and "主观题" not in ws


def test_select_points_ignores_unknown_ids(knowledge: dict) -> None:
    assert [p.id for p in select_points(knowledge, ["K002", "不存在"])] == ["K002"]
