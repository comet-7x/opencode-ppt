"""教案生成节点（无状态）：选中知识点 + 课次参数 → Markdown 教案。

三段式：数据（选中知识点）+ 参数（SessionParams）+ 指令（system prompt）。
模型由调用方（service）传入，logic 不感知 provider。plan_mode 用英文键
（new_teach / review_comprehensive / unit_summary）。
"""

from __future__ import annotations

from typing import Any

from camel.agents import ChatAgent
from camel.responses import ChatAgentResponse

from app.models.generator.lesson_plan import (
    LessonPlanRequest,
    LessonPlanResponse,
    SessionParams,
)
from app.services.generator.common.errors import ModelError
from app.services.generator.common.knowledge import select_points

LESSON_PLAN_SYSTEM_PROMPT = """\
你是一名教学设计专家。用户会给你一份 JSON，含 session（本次课参数）和 input_points
（选中的知识点，字段见其结构）。请据此生成一份**中文 Markdown 教案**。

session.plan_mode 取值（英文键）：new_teach=新授、review_comprehensive=复习综合、unit_summary=单元总结。

硬性要求：
1. 只依据 input_points 的内容，不虚构课程特有信息（不编造教材页码、考试占比、教师原话）。
2. 【时间分配】总时长 = periods × minutes_per_period 分钟。
   - 导入约占 10-15%，小结约占 10%，其余为讲授预算。
   - 讲授预算按每个知识点的 (importance + difficulty) 加权分配，重要且难的点给更多时间。
   - 若 plan_mode=review_comprehensive 或点数相对时长过多，改为按知识域串讲、只讲重难点，不逐点展开。
   - 各环节分钟数相加（含导入/小结）必须**精确等于**总时长。
3. 【排序】按 prerequisites 排授课顺序：先教被依赖的知识点。
4. 【字段映射】
   - 知识目标 ← 各点 definition
   - 能力目标 ← 由 learning_significance + detailed_explanation 合成，用行为动词（"能运用/能辨析…"）
   - 素养目标 ← 中国实践/思政点 + supports_objectives 对应的 session.course_objectives
   - 教学重点 ← importance 高的点 + key_points_and_difficulties
   - 教学难点 ← difficulty 高的点 + key_points_and_difficulties
   - 教学过程各环节内容 ← detailed_explanation + examples（提炼要点，勿整段照抄）

只输出教案 Markdown 本身，不要任何前言或解释。严格用如下结构（# 标题、| 表格、- 列表、**加粗**）：
# [课程名] 教案 · [课题]
## 基本信息          （表格：课程名称/授课课题/学时/授课对象/涉及知识点）
## 教学目标          （知识目标 / 能力目标 / 素养目标 三维，用 - 列表）
## 教学重点与难点
## 教学方法
## 教学过程（共 M 分钟）
### 导入（约 X 分钟）
### 新课讲授
#### 环节一 · Kxxx [名称]（约 X 分钟）
- **讲授内容**： …
- **师生活动**： …
- **设计意图**： …
（每个选中知识点一个环节，按前置顺序）
### 课堂小结（约 X 分钟）
### 作业 / 思考题
## 板书设计
## 教学反思
（"教学反思"下留空）
"""


def build_request(
    knowledge: dict[str, Any],
    ids: list[str],
    *,
    session_title: str,
    periods: int,
    plan_mode: str = "new_teach",
    minutes_per_period: int = 45,
) -> LessonPlanRequest:
    """从 knowledge dict + 选中 ids 组装教案请求。"""
    points = select_points(knowledge, ids)
    course = knowledge.get("course", {})
    session = SessionParams(
        course_title=course.get("title", ""),
        session_title=session_title,
        periods=periods,
        minutes_per_period=minutes_per_period,
        plan_mode=plan_mode,  # type: ignore[arg-type]
        audience=course.get("profile", {}).get("授课对象", ""),
        course_objectives=[o["text"] for o in course.get("objectives", [])],
    )
    return LessonPlanRequest(session=session, input_points=points)


async def generate_lesson_plan(
    req: LessonPlanRequest, model: Any
) -> LessonPlanResponse:
    """指令进 system_message，数据+参数进 user message。model 由 service 按温度取好传入。"""
    agent = ChatAgent(model=model, system_message=LESSON_PLAN_SYSTEM_PROMPT)
    resp = await agent.astep(req.model_dump_json(indent=2))
    if not isinstance(resp, ChatAgentResponse) or not resp.msgs:
        raise ModelError(f"模型返回空/异常响应：{resp!r}")
    return LessonPlanResponse(lesson_plan_md=resp.msgs[0].content)
