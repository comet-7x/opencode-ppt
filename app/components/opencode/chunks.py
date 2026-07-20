"""类型化输出块：给业务端消费的「语义层」结果。

`astream()` 把 opencode 的原始事件（ChatEvent / properties 那一套「传输层」结构）
翻译成这里的几种块，业务端只认块的字段，不必理解 opencode 事件协议：

    async for ch in chat.astream("生成PPT"):
        match ch:
            case TextChunk(kind="text", text=t):      # AI 正文（打字机增量）
                print(t, end="", flush=True)
            case TextChunk(kind="reasoning", text=t): # 思考过程
                ...
            case ToolChunk(tool=name, status=st):     # 工具执行（读写文件、跑脚本…）
                ...
            case DoneChunk(ok=ok, error=err):         # 本轮收工（终止块，循环随后结束）
                ...

设计说明
--------
* TextChunk.text 是**增量**（本次新增的片段），不是全量——即使 opencode 服务端
  发的是「全量覆盖」，astream 也会替你算好差值，业务端直接拼接即可。
* 每个流最后**必定**以一个 DoneChunk 结束：ok=True 表示正常（session.idle）；
  ok=False 表示出错 / 超迭代 / 超时，error 里是原因（等同 client.last_error）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class TextChunk:
    """AI 的文字输出增量。"""

    kind: Literal["text", "reasoning"]   # text=正文；reasoning=思考过程
    text: str                            # 本次新增片段（打字机原料）


@dataclass(slots=True)
class ToolChunk:
    """一次工具调用的状态更新（同一 call_id 会随 pending→running→completed 多次产出）。"""

    tool: str                            # 工具名（如 read / write / bash …）
    call_id: str                         # 调用 ID（同一次调用多次更新时 ID 不变）
    status: str | None                   # pending / running / completed / error
    state: dict[str, Any] = field(default_factory=dict)  # 原始 state（含 input/output）


@dataclass(slots=True)
class DoneChunk:
    """终止块：一轮结束的信号，永远是流的最后一个块。"""

    ok: bool                             # True=正常收工（idle）；False=出错/超迭代/超时
    error: str | None = None             # ok=False 时的原因


# 业务端可用它做类型标注：`async for ch: StreamChunk in chat.astream(...)`
StreamChunk = TextChunk | ToolChunk | DoneChunk
