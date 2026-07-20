"""作业生成 + L1/L2 溯源校验 + 重生成闭环（合并适配自 draft 的两份 homework POC）。

模型从 settings 注入。渲染保留 markdown 两份（作业卷 / 参考答案与解析）；docx 写法
需要 python-docx，作为可选（未装则跳过 docx，只出 markdown/json）。

含大量中文标点，豁免歧义字符 lint（pyproject 已全局 ignore RUF001-003，这里冗余标注保险）。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ValidationError

from .knowledge import KnowledgePoint

# 难度: 1. 基础 2. 进阶 3. 挑战
Difficulty = Literal["basic", "advanced", "challenge"]


class QuestionSpec(BaseModel):
    count: int
    score_each: int


class HomeworkRequest(BaseModel):
    course_title: str
    session_title: str
    points: list[KnowledgePoint]
    lesson_plan_md: str | None = None
    mc: QuestionSpec
    fill: QuestionSpec
    subjective: QuestionSpec
    difficulty: Difficulty

    @property
    def total_score(self) -> int:
        return (
            self.mc.count * self.mc.score_each
            + self.fill.count * self.fill.score_each
            + self.subjective.count * self.subjective.score_each
        )


class MCOption(BaseModel):
    label: str
    text: str
    is_correct: bool
    analysis: str


class Question(BaseModel):
    # 题目类型: 选择、填空、主观题
    type: Literal["choice", "fill_blank", "subjective"]
    stem: str
    score: int
    difficulty: str
    source_point_ids: list[str] = []
    options: list[MCOption] = []
    answers: list[str] = []
    scoring_points: list[str] = []
    analysis: str = ""


class HomeworkResponse(BaseModel):
    questions: list[Question]
    total_score: int

    @classmethod
    def parse(cls, text: str) -> HomeworkResponse | None:
        blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = blocks[-1] if blocks else text.strip()
        try:
            return cls.model_validate_json(candidate)
        except ValidationError:
            return None


class VerifierOut(BaseModel):
    independent_answer: str = ""
    grounded: bool = True
    unsupported_claims: list[str] = []
    notes: str = ""

    @classmethod
    def parse(cls, text: str) -> VerifierOut | None:
        blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        cand = blocks[-1] if blocks else text.strip()
        try:
            return cls.model_validate_json(cand)
        except ValidationError:
            return None


class VerifyResult(BaseModel):
    index: int
    type: str
    verdict: Literal["pass", "warn", "fail"]
    generator_answer: str
    independent_answer: str
    answer_match: bool | None
    grounded: bool
    unsupported_claims: list[str]
    l1_ids: list[str]
    l2_ids: list[str]
    notes: str
