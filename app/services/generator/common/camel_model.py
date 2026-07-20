"""从配置构建 CAMEL 模型（OpenAI 兼容端点），按温度缓存。

原 lifespan 把温度写死 0.1 并分 stream/unstream 两个实例；这里改成一个 `ModelProvider`：
持有 LLM 连接参数，`get(temperature)` 按需构建并缓存。不同环节用不同温度
（教案 0.1 / slides 0.2 / 作业 0.2 / 校验 0.1），各自缓存一个实例。

lifespan 在启动时建一个 ModelProvider 存进 app.state；logic 节点通过 service 拿到
对应温度的模型来调 CAMEL。配置来自唯一真相 ConfigEngine。
"""

from __future__ import annotations

from typing import Any

from camel.models import ModelFactory
from camel.types import ModelPlatformType

from app.config import config_engine


class ModelProvider:
    """按温度缓存 CAMEL 模型。无状态可共享（进程内单例，放 app.state）。"""

    def __init__(self, *, model_name: str, base_url: str, api_key: str) -> None:
        self._model_name = model_name
        self._base_url = base_url
        self._api_key = api_key
        self._cache: dict[float, Any] = {}

    def get(self, temperature: float = 0.1) -> Any:
        """取（并缓存）一个指定温度的模型。"""
        model = self._cache.get(temperature)
        if model is None:
            model = ModelFactory.create(
                model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
                model_type=self._model_name,
                api_key=self._api_key,
                url=self._base_url,
                model_config_dict={
                    "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
                    "stream": False,
                    "temperature": temperature,
                },
            )
            self._cache[temperature] = model
        return model

    @classmethod
    def from_config(cls) -> ModelProvider:
        """从 ConfigEngine 的 LLM 配置构建。"""
        llm = config_engine.base_config.models.llm
        return cls(model_name=llm.model_name, base_url=llm.url, api_key=llm.api_key)
