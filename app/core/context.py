"""全局应用上下文（单例）：集中持有共享的中间件/客户端。

服务里 `self.context = AppContext()` 即可拿到 model_provider / knowledge_source /
task_store（以及将来的 redis / db）。生命周期由 lifespan 驱动：启动 initialize()、关停 close()。

测试可给服务传入自定义 context（服务的可选 context 参数），绕过全局单例。
"""

from __future__ import annotations

from app.config import config_engine
from app.config.cfg import ConfigEngine
from app.services.generator.common.camel_model import ModelProvider
from app.services.generator.common.knowledge import FileKnowledgeSource, KnowledgeSource
from app.services.generator.common.task import InMemoryTaskStore, TaskStore
from app.utils.common import SingletonMeta


class AppContext(metaclass=SingletonMeta):
    def __init__(self) -> None:
        self._initialized = False
        self._config: ConfigEngine | None = None
        self._model_provider: ModelProvider | None = None
        self._knowledge_source: KnowledgeSource | None = None
        self._task_store: TaskStore | None = None
        # 预留：self._redis_client: Redis | None = None

    def initialize(self) -> None:
        """构建共享对象（lifespan 启动时调，幂等）。"""
        if self._initialized:
            return
        self._config = config_engine
        self._model_provider = ModelProvider.from_config()
        self._knowledge_source = FileKnowledgeSource()
        self._task_store = InMemoryTaskStore()
        # 预留：self._redis_client = Redis(host=..., port=...)
        self._initialized = True

    async def close(self) -> None:
        """清理资源（lifespan 关停时调）。"""
        # 预留：await self._redis_client.close()
        return

    @property
    def initialized(self) -> bool:
        return self._initialized

    @property
    def config(self) -> ConfigEngine:
        if self._config is None:
            raise RuntimeError("AppContext 未初始化")
        return self._config

    @property
    def model_provider(self) -> ModelProvider:
        if self._model_provider is None:
            raise RuntimeError("AppContext 未初始化：model_provider 不可用")
        return self._model_provider

    @property
    def knowledge_source(self) -> KnowledgeSource:
        if self._knowledge_source is None:
            raise RuntimeError("AppContext 未初始化：knowledge_source 不可用")
        return self._knowledge_source

    @property
    def task_store(self) -> TaskStore:
        if self._task_store is None:
            raise RuntimeError("AppContext 未初始化：task_store 不可用")
        return self._task_store
