"""全局应用上下文（模块级单例）：集中持有共享的中间件/客户端。

模块本身即单例：本文件底部实例化一次 `context`，各处 `from app.core.context import context`
共用同一个。服务默认用它，测试可注入自定义 context（服务的可选 context 参数）。

生命周期由 lifespan 驱动：启动 initialize()、关停 close()。
"""

from __future__ import annotations

from app.components.llm import ModelProvider
from app.components.task import InMemoryTaskStore, TaskStore
from app.config import config_engine
from app.config.cfg import ConfigEngine
from app.services.generator.common.knowledge import FileKnowledgeSource, KnowledgeSource


class AppContext:
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
        # 进程内任务随进程消失：关停时统一取消，避免协程被打断在半路且无人记录。
        # （换 RedisTaskStore 后不该这么做——别的 worker 还在跑，故只对内存实现生效。）
        if isinstance(self._task_store, InMemoryTaskStore):
            self._task_store.cancel_all()
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


# 模块级单例：全项目共用这一个实例
context = AppContext()
