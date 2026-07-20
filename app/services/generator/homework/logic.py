"""作业生成节点（无状态）：命题 → L1/L2 溯源校验 → 重生成闭环 → 渲染 markdown。

内部枚举全英文键（type: choice/fill_blank/subjective；difficulty: basic/advanced/challenge），
prompt 明确要求模型输出英文键，给人看的 markdown 用 TYPE_ZH / DIFF_ZH 转中文标签。

模型不在 logic 里构建：调用方传入 `get_model(temperature)` 回调（即 ModelProvider.get），
让不同环节用不同温度（命题 0.2 / 校验 0.1 / 重生成 0.2），logic 不感知 provider。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from camel.agents import ChatAgent
from camel.responses import ChatAgentResponse

from app.models.generator.homework import (
    HomeworkRequest,
    HomeworkResponse,
    Question,
    QuestionSpec,
    VerifierOut,
    VerifyResult,
)
from app.models.generator.knowledge import KnowledgePoint
from app.services.generator.common.errors import ModelError, ParseError
from app.services.generator.common.knowledge import select_points

# get_model(temperature) -> CAMEL model（即 ModelProvider.get）
ModelFn = Callable[[float], Any]

# 英文键 → 中文展示标签
TYPE_ZH = {"choice": "选择题", "fill_blank": "填空题", "subjective": "主观题"}
DIFF_ZH = {"basic": "基础", "advanced": "进阶", "challenge": "挑战"}

# 渲染分节顺序：(英文键, 中文标签)
_SECTIONS = [("choice", "选择题"), ("fill_blank", "填空题"), ("subjective", "主观题")]


HOMEWORK_SYSTEM_PROMPT = """\
你是命题专家。根据用户 JSON（题量参数 + 选中知识点 points + 可选 lesson_plan_md）出一套课后作业。
只考 points/教案里出现过的内容，不超纲、不虚构。

题型 type 用英文键：choice=选择题、fill_blank=填空题、subjective=主观题。
难度 difficulty 用英文键：basic=基础、advanced=进阶、challenge=挑战。

【题型匹配】
- choice（选择题）：考概念/理论/制度/术语的辨析；**干扰项必须取自该知识点 key_points_and_difficulties 里的易错/易混点**，让错误项看起来都合理。每题 4 个选项。
- fill_blank（填空题）：考术语/关键事实/法律文件名/数字，答案唯一明确。
- subjective（主观题）：考高 importance/高 difficulty 的点，优先 应用实践/热点问题/中国实践。

【难度】
- basic → 记忆/理解：直接考定义与事实。
- advanced → 应用/分析：辨析、代入情境，干扰项更微妙。
- challenge → 评价/综合：用 related/prerequisites 把多个知识点串成一题，用**新情境**（不要照抄教案里的原例）考迁移。

【分配与配分】
- 题目按 importance 摊到不同知识点，别都压在一个点上；覆盖教案的教学重点难点。
- **每种题型的题数必须与参数严格一致**（choice mc.count 道、fill_blank fill.count 道、subjective subjective.count 道）——这是硬约束。
- 分值不用你算：score/total_score 会由程序按参数确定性回填，你照常填上即可。

【每题必给】
- choice：options（4 个，每个都写 analysis 说明为什么对/错，正确项 is_correct=true）。
- fill_blank：answers（逐空）+ analysis。
- subjective：scoring_points（得分要点，可对应分值分解）+ analysis（详细分析过程）。
- 每题标 type、score、difficulty、source_point_ids（考查的知识点 ID）。

最后**只输出一个 ```json 代码块**，严格符合结构（type/difficulty 用英文键）：
{"questions": [{"type": "choice|fill_blank|subjective", "stem": "...", "score": N, "difficulty": "basic|advanced|challenge",
  "source_point_ids": ["K001"], "options": [{"label":"A","text":"...","is_correct":false,"analysis":"..."}],
  "answers": ["..."], "scoring_points": ["..."], "analysis": "..."}], "total_score": N}
其中 options 只 choice 用，answers 只 fill_blank 用，scoring_points 只 subjective 用。
"""


def build_request(
    knowledge: dict[str, Any],
    ids: list[str],
    *,
    session_title: str,
    mc: tuple[int, int],
    fill: tuple[int, int],
    subjective: tuple[int, int],
    difficulty: str,
    lesson_plan_md: str | None = None,
) -> HomeworkRequest:
    """从 knowledge dict 选点，组装命题请求。mc/fill/subjective = (数量, 每题分)。"""
    points = select_points(knowledge, ids)
    return HomeworkRequest(
        course_title=knowledge.get("course", {}).get("title", ""),
        session_title=session_title,
        points=points,
        lesson_plan_md=lesson_plan_md,
        mc=QuestionSpec(count=mc[0], score_each=mc[1]),
        fill=QuestionSpec(count=fill[0], score_each=fill[1]),
        subjective=QuestionSpec(count=subjective[0], score_each=subjective[1]),
        difficulty=difficulty,  # type: ignore[arg-type]
    )


def _enforce_scores(req: HomeworkRequest, hw: HomeworkResponse) -> HomeworkResponse:
    """分值确定性回填（不信模型算）。"""
    spec = {"choice": req.mc, "fill_blank": req.fill, "subjective": req.subjective}
    for q in hw.questions:
        q.score = spec[q.type].score_each
    hw.total_score = req.total_score
    return hw


async def generate_homework(req: HomeworkRequest, get_model: ModelFn) -> HomeworkResponse:
    agent = ChatAgent(model=get_model(0.2), system_message=HOMEWORK_SYSTEM_PROMPT)
    resp = await agent.astep(req.model_dump_json(indent=2))
    if not isinstance(resp, ChatAgentResponse) or not resp.msgs:
        raise ModelError(f"模型返回空/异常响应：{resp!r}")
    hw = HomeworkResponse.parse(resp.msgs[0].content)
    if hw is None:
        raise ParseError("无法解析 HomeworkResponse JSON：\n" + resp.msgs[0].content[:500])
    return _enforce_scores(req, hw)


# ————————————————————— L2 词法检索（无 embedding）————————————————————— #
def _shingles(text: str, n: int = 2) -> set[str]:
    t = re.sub(r"\s+", "", text or "")
    return {t[i : i + n] for i in range(len(t) - n + 1)} if len(t) >= n else {t}


def _point_text(p: KnowledgePoint) -> str:
    return f"{p.name} {p.definition} {p.key_points_and_difficulties}"


def _question_text(q: Question) -> str:
    parts = [q.stem, *[o.text for o in q.options], *q.answers, q.analysis]
    return " ".join(parts)


def retrieve_l2(
    q: Question, points: list[KnowledgePoint], cited_ids: set[str], k: int = 3
) -> list[KnowledgePoint]:
    by_id = {p.id: p for p in points}
    neighbor_ids: set[str] = set()
    for cid in cited_ids:
        p = by_id.get(cid)
        if p:
            neighbor_ids |= set(p.prerequisites) | set(p.successors) | set(p.related)
    neighbor_ids -= cited_ids

    q_sh = _shingles(_question_text(q))
    scored: list[tuple[int, KnowledgePoint]] = []
    for p in points:
        if p.id in cited_ids:
            continue
        overlap = len(q_sh & _shingles(_point_text(p)))
        bonus = 100 if p.id in neighbor_ids else 0
        if overlap or bonus:
            scored.append((bonus + overlap, p))
    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:k]]


# ————————————————————— verifier ————————————————————— #
VERIFIER_SYSTEM_PROMPT = """\
你是命题校验专家。用户会给你【参考资料】（若干知识点摘录）和【待校验题目】。
严格只依据参考资料判断，不要用资料外的知识补全。

任务：
1. 若是选择题/填空题：**在不参考任何已给答案的前提下，仅凭参考资料独立作答**——
   选择题给出正确选项字母（可多选则拼接，如 "AC"）；填空题按空给出答案（用"；"分隔）。
   主观题不作答，independent_answer 留空。
2. 核对题干、选项说明、解析里的每一处事实性论断，是否能在参考资料中找到依据；
   把找不到依据的写进 unsupported_claims。
3. grounded：题目整体是否被参考资料支撑（有关键论断无依据则为 false）。

只输出一个 ```json 代码块：
{"independent_answer": "...", "grounded": true, "unsupported_claims": ["..."], "notes": "一句话说明"}
"""


def _mask_question(q: Question) -> str:
    if q.type == "choice":
        opts = "\n".join(f"{o.label}. {o.text}" for o in q.options)
        return f"[选择题] {q.stem}\n{opts}"
    if q.type == "fill_blank":
        return f"[填空题] {q.stem}"
    return f"[主观题] {q.stem}"


def _true_answer(q: Question) -> str:
    if q.type == "choice":
        return "".join(o.label for o in q.options if o.is_correct)
    if q.type == "fill_blank":
        return "；".join(q.answers)
    return ""


def _norm(ans: str) -> str:
    return re.sub(r"[\s，,、;；]+", "", ans).upper()


def _render_sources(l1: list[KnowledgePoint], l2: list[KnowledgePoint]) -> str:
    def block(tag: str, pts: list[KnowledgePoint]) -> str:
        out = []
        for p in pts:
            out.append(
                f"[{tag}] {p.id} {p.name}（{p.knowledge_domain}·{p.point_type}）\n"
                f"定义：{p.definition}\n详解：{p.detailed_explanation}\n"
                f"重难点：{p.key_points_and_difficulties}\n示例：{p.examples}"
            )
        return "\n\n".join(out)

    return "【被引知识点 L1】\n" + block("L1", l1) + "\n\n【相关知识点 L2】\n" + block("L2", l2)


async def verify_question(
    idx: int, q: Question, points: list[KnowledgePoint], get_model: ModelFn
) -> VerifyResult:
    by_id = {p.id: p for p in points}
    cited = set(q.source_point_ids)
    l1 = [by_id[i] for i in q.source_point_ids if i in by_id]
    l2 = retrieve_l2(q, points, cited)

    prompt = (
        "【参考资料】\n" + _render_sources(l1, l2) + "\n\n【待校验题目】\n" + _mask_question(q)
    )
    agent = ChatAgent(model=get_model(0.1), system_message=VERIFIER_SYSTEM_PROMPT)
    resp = await agent.astep(prompt)
    out = (
        VerifierOut.parse(resp.msgs[0].content)
        if isinstance(resp, ChatAgentResponse) and resp.msgs
        else None
    ) or VerifierOut(grounded=False, notes="verifier 无法解析输出")

    true_ans = _true_answer(q)
    match = (
        _norm(out.independent_answer) == _norm(true_ans)
        if q.type in ("choice", "fill_blank")
        else None
    )
    if match is False:
        verdict = "fail"
    elif not out.grounded or out.unsupported_claims:
        verdict = "warn"
    else:
        verdict = "pass"

    return VerifyResult(
        index=idx, type=q.type, verdict=verdict,
        generator_answer=true_ans, independent_answer=out.independent_answer,
        answer_match=match, grounded=out.grounded,
        unsupported_claims=out.unsupported_claims,
        l1_ids=[p.id for p in l1], l2_ids=[p.id for p in l2], notes=out.notes,
    )


async def verify_homework(
    hw: HomeworkResponse, points: list[KnowledgePoint], get_model: ModelFn
) -> list[VerifyResult]:
    return await asyncio.gather(
        *(verify_question(i, q, points, get_model) for i, q in enumerate(hw.questions, 1))
    )


# ————————————————————— 重生成闭环 ————————————————————— #
REGEN_SYSTEM_PROMPT = """\
你是命题专家。用户会给你【知识点资料】、一道【待修正题目】和对它的【校验意见】。
请**只重出这一道题**，针对校验意见改正错误（答案冲突说明原答案或选项有误；无依据论断要删掉或改成资料支持的说法）。
题型、分值、考查的知识点(source_point_ids)保持不变，只依据资料、不虚构。type/difficulty 用英文键。

只输出一个 ```json 代码块，符合单题结构：
{"type":"choice|fill_blank|subjective","stem":"...","score":N,"difficulty":"basic|advanced|challenge","source_point_ids":["K001"],
 "options":[{"label":"A","text":"...","is_correct":false,"analysis":"..."}],
 "answers":["..."],"scoring_points":["..."],"analysis":"..."}
"""


def _parse_question(text: str) -> Question | None:
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    cand = blocks[-1] if blocks else text.strip()
    try:
        return Question.model_validate_json(cand)
    except Exception:
        return None


def _critique(r: VerifyResult) -> str:
    lines = []
    if r.answer_match is False:
        lines.append(
            f"独立作答得到「{r.independent_answer}」，与原答案「{r.generator_answer}」冲突——原答案/选项判定有误。"
        )
    lines += [f"无依据论断：{c}" for c in r.unsupported_claims]
    if r.notes:
        lines.append(r.notes)
    return "\n".join(f"- {x}" for x in lines) or "- 存在问题，请复核并改正。"


async def regenerate_question(
    q: Question, r: VerifyResult, points: list[KnowledgePoint], get_model: ModelFn
) -> Question:
    by_id = {p.id: p for p in points}
    l1 = [by_id[i] for i in q.source_point_ids if i in by_id]
    prompt = (
        "【知识点资料】\n" + _render_sources(l1, [])
        + "\n\n【待修正题目】\n" + q.model_dump_json(indent=2)
        + "\n\n【校验意见】\n" + _critique(r)
        + "\n\n请修正并只重出这一道题。"
    )
    agent = ChatAgent(model=get_model(0.2), system_message=REGEN_SYSTEM_PROMPT)
    resp = await agent.astep(prompt)
    newq = (
        _parse_question(resp.msgs[0].content)
        if isinstance(resp, ChatAgentResponse) and resp.msgs
        else None
    )
    return newq or q


async def generate_verified_homework(
    req: HomeworkRequest, points: list[KnowledgePoint], get_model: ModelFn,
    max_retries: int = 2,
) -> tuple[HomeworkResponse, list[VerifyResult]]:
    """generator → verifier → (warn/fail 重生成 ≤max_retries) → 最终 hw + 校验结论。"""
    hw = await generate_homework(req, get_model)
    results = await verify_homework(hw, points, get_model)
    for _attempt in range(max_retries):
        bad = [r for r in results if r.verdict != "pass"]
        if not bad:
            break
        for r in bad:
            hw.questions[r.index - 1] = await regenerate_question(
                hw.questions[r.index - 1], r, points, get_model
            )
        _enforce_scores(req, hw)
        results = await verify_homework(hw, points, get_model)
    return hw, results


# ————————————————————— 渲染 markdown ————————————————————— #
def render_worksheet(req: HomeworkRequest, hw: HomeworkResponse) -> str:
    """作业卷：只有题干/选项，无答案。"""
    out = [f"# {req.course_title} 课后作业 · {req.session_title}", ""]
    out.append(f"*满分 {hw.total_score} 分 · 难度：{DIFF_ZH.get(req.difficulty, req.difficulty)}*")
    for cn, (typ, label) in enumerate(_SECTIONS, 1):
        qs = [q for q in hw.questions if q.type == typ]
        if not qs:
            continue
        each = qs[0].score
        out += ["", f"## {'一二三'[cn - 1]}、{label}（每题 {each} 分，共 {each * len(qs)} 分）", ""]
        for i, q in enumerate(qs, 1):
            out.append(f"{i}. {q.stem}")
            for opt in q.options:
                out.append(f"　　{opt.label}. {opt.text}")
            out.append("")
    return "\n".join(out)


def render_answer_key(req: HomeworkRequest, hw: HomeworkResponse) -> str:
    """参考答案与解析：答案 + 逐项分析 / 得分要点。"""
    out = [f"# {req.course_title} 课后作业 参考答案与解析 · {req.session_title}", ""]
    for cn, (typ, label) in enumerate(_SECTIONS, 1):
        qs = [q for q in hw.questions if q.type == typ]
        if not qs:
            continue
        out += ["", f"## {'一二三'[cn - 1]}、{label}", ""]
        for i, q in enumerate(qs, 1):
            src = "、".join(q.source_point_ids)
            if typ == "choice":
                ans = "".join(o.label for o in q.options if o.is_correct)
                out += [f"**{i}. {q.stem}**", "", f"【答案】{ans}", "", "【解析】"]
                for opt in q.options:
                    mark = "✓" if opt.is_correct else "✗"
                    out.append(f"- {opt.label}. {mark} {opt.analysis}")
            elif typ == "fill_blank":
                out += [f"**{i}. {q.stem}**", "", f"【答案】{'；'.join(q.answers)}", "",
                        f"【解析】{q.analysis}"]
            else:
                out += [f"**{i}. {q.stem}**", "", "【得分要点】"]
                out += [f"- {p}" for p in q.scoring_points]
                out += ["", f"【分析】{q.analysis}"]
            out += ["", f"*（考查知识点：{src}）*", ""]
    return "\n".join(out)
