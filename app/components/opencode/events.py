"""事件模型 + 分发器：把 opencode 的事件流按 type 路由到不同回调。

opencode 的事件流是「整个服务端」的一条流（`client.event.list()`）。本模块做两件事：
  1. 把原始事件包成好用的 `ChatEvent`（type / properties / 常用快捷字段 / 原始 dict）
  2. `EventDispatcher` 按 `type` 把事件分发给注册的回调；回调可以是同步或 async

回调签名统一为：  handler(event: ChatEvent) -> None | Awaitable[None]

注册方式：
    dispatcher.on(EventType.MESSAGE_PART_DELTA, on_delta)   # 只收某一种
    dispatcher.on_any(logger)                               # 收所有种类
也可以用装饰器：
    @dispatcher.handle(EventType.SESSION_IDLE)
    async def _(ev): ...
"""

from __future__ import annotations

import inspect
import sys
import traceback
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# 回调可同步可异步
Handler = Callable[["ChatEvent"], None | Awaitable[None]]


class EventType:
    """常用事件类型常量（字符串即 opencode 事件的 type 字段）。

    聊天最关心的是前几个；其余按需使用。用常量而非裸字符串，避免拼写错误。
    """

    # 聊天核心
    MESSAGE_UPDATED = "message.updated"  # 一条消息（整体）更新
    MESSAGE_PART_UPDATED = "message.part.updated"  # 消息里某个内容块更新（全量）
    MESSAGE_PART_DELTA = "message.part.delta"  # 流式增量（打字机原料）
    MESSAGE_REMOVED = "message.removed"
    MESSAGE_PART_REMOVED = "message.part.removed"

    # 会话状态
    SESSION_UPDATED = "session.updated"
    SESSION_IDLE = "session.idle"  # 本轮问答结束（收工信号）
    SESSION_ERROR = "session.error"
    SESSION_DELETED = "session.deleted"

    # 工具 / 权限（skill 执行时会用到）
    PERMISSION_UPDATED = "permission.updated"  # skill 请求权限时
    FILE_EDITED = "file.edited"  # skill 写文件时
    FILE_WATCHER_UPDATED = "file.watcher.updated"

    # 服务端
    SERVER_CONNECTED = "server.connected"


@dataclass(slots=True)
class ChatEvent:
    """一条被规整过的事件。"""

    type: str
    properties: dict[str, Any]
    raw: dict[str, Any]

    # 常用快捷取值
    @property
    def session_id(self) -> str | None:
        return self.properties.get("sessionID")

    @property
    def part(self) -> dict[str, Any]:
        """内容块（message.part.* 事件里才有）。"""
        return self.properties.get("part") or {}

    @property
    def part_id(self) -> str | None:
        return self.part.get("id") or self.properties.get("partID")

    @property
    def part_type(self) -> str | None:
        """内容块类型：text / reasoning / tool / step-start / step-finish ..."""
        return self.part.get("type")

    @property
    def message_id(self) -> str | None:
        """本事件所属的 message id（part 事件与 message.updated 都能取到）。

        角色（user/assistant）只在 message.updated 上，part 自己不带 role，
        靠这个 id 把 part 关联回它所属的 message 才能判断是谁说的。
        """
        info = self.properties.get("info") or {}
        return (
            self.part.get("messageID")  # message.part.updated
            or self.properties.get("messageID")  # message.part.delta / removed
            or info.get("id")  # message.updated
        )

    @property
    def text(self) -> str | None:
        """内容块文字（text / reasoning 有；其余为 None）。"""
        return self.part.get("text")

    @property
    def delta(self) -> str | None:
        """流式增量文字（message.part.delta 事件）。"""
        return self.properties.get("delta") or self.properties.get("text")

    @property
    def delta_field(self) -> str | None:
        """message.part.delta 增量所属字段：text / reasoning。

        （本 SDK 版本没有 delta 事件的类型，但服务端仍会发，SDK 原样透传，
        增量文字在 `delta`、所属字段在这里。）
        """
        return self.properties.get("field")

    @property
    def error(self) -> dict[str, Any]:
        """session.error 事件里的错误对象（含 name / data）。其余事件为空 dict。"""
        return self.properties.get("error") or {}

    @property
    def role(self) -> str | None:
        """message.updated 事件里发消息者角色：user / assistant。"""
        info = self.properties.get("info") or {}
        return info.get("role")

    @classmethod
    def from_sdk(cls, ev: Any) -> ChatEvent:
        """把 SDK 事件对象转成 ChatEvent。"""
        d: dict[str, Any] = ev.to_dict() if hasattr(ev, "to_dict") else dict(ev)
        return cls(
            type=d.get("type", ""),
            properties=d.get("properties", {}) or {},
            raw=d,
        )


class EventDispatcher:
    """按事件 type 把 ChatEvent 分发给注册的回调。

    单线程 asyncio 下运行：同步回调直接调用，async 回调 await。
    一个 type 可注册多个回调，会依次执行；某个回调抛异常不影响其他回调。
    """

    def __init__(self, *, on_error: Handler | None = None) -> None:
        self._by_type: dict[str, list[Handler]] = defaultdict(list)
        self._any: list[Handler] = []
        self._on_error = on_error  # 回调内部异常时的钩子（可选）

    # —— 注册 —— #
    def on(self, event_type: str, handler: Handler) -> Handler:
        """给指定事件类型注册一个回调。返回 handler 便于链式/装饰。"""
        self._by_type[event_type].append(handler)
        return handler

    def on_any(self, handler: Handler) -> Handler:
        """注册一个「所有事件都会收到」的回调（如全量日志）。"""
        self._any.append(handler)
        return handler

    def off(self, handler: Handler, event_type: str | None = None) -> bool:
        """注销回调。event_type=None 时从 on_any 及所有类型里一并摘除。

        返回是否确实移除了至少一个。astream 结束时用它摘掉临时桥接回调。
        """
        removed = False
        if event_type is None:
            if handler in self._any:
                self._any.remove(handler)
                removed = True
            for lst in self._by_type.values():
                if handler in lst:
                    lst.remove(handler)
                    removed = True
        else:
            lst = self._by_type.get(event_type)
            if lst and handler in lst:
                lst.remove(handler)
                removed = True
        return removed

    def handle(self, event_type: str) -> Callable[[Handler], Handler]:
        """装饰器写法：@dispatcher.handle(EventType.SESSION_IDLE)"""

        def deco(fn: Handler) -> Handler:
            return self.on(event_type, fn)

        return deco

    # —— 分发 —— #
    async def dispatch(self, event: ChatEvent) -> None:
        handlers = [*self._any, *self._by_type.get(event.type, ())]
        for h in handlers:
            try:
                result = h(event)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                await self._report_error(event, exc)
    async def _report_error(self, event: ChatEvent, exc: Exception) -> None:
        if self._on_error is None:
            # 没有错误钩子就打到 stderr，避免异常被静默吞掉
            print(
                f"[EventDispatcher] 处理 {event.type} 时回调异常: {exc}",
                file=sys.stderr,
            )
            traceback.print_exc()
            return
        try:
            result = self._on_error(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            pass
