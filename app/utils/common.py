"""通用工具。"""

from __future__ import annotations

from typing import Any, ClassVar


class SingletonMeta(type):
    """单例元类：同一个类只构造一次，之后返回同一实例。

    单线程 asyncio 下够用（不加锁）。
    """

    _instances: ClassVar[dict[type, Any]] = {}

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]
