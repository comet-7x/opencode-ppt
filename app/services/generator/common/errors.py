"""领域异常：让 logic/service 抛出语义清晰的错误，由 API 层统一映射成 HTTP。

分层约定：
- logic/service 只抛这些领域异常（或让上游 openai/httpx 异常自然冒泡）；
- API 层（core/errors.py 注册的 handler）负责把它们翻成 HTTP 状态码 + JSON；
- pipeline 的阶段兜底 except 也会把它们 str() 进 job.error（job 内部错误态）。
"""

from __future__ import annotations


class GenerationError(Exception):
    """本服务领域错误基类。status_code 供 API 层映射。"""

    status_code: int = 500


class KnowledgeError(GenerationError):
    """知识点来源/解析问题（缺来源、路径不存在、JSON 非法）。"""

    status_code = 400


class ParseError(GenerationError):
    """模型输出无法解析成约定结构（英文枚举 JSON 契约不符等）。"""

    status_code = 502


class ModelError(GenerationError):
    """模型调用失败或返回空/不可用响应。"""

    status_code = 502
