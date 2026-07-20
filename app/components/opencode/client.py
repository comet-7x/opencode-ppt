"""异步聊天客户端：连接 opencode，实时捕获会话事件，按类型分发给回调。

职责边界
--------
本模块只管「连上服务端 + 收发聊天 + 把事件实时分发出去」。
自定义模型的 **注册**（把 base_url / api_key 写进 opencode 配置、装 skill 等）
属于「初始化模块」，不在这里；这里只按名字（provider_id / model_id）使用模型。

并发模型
--------
全程单线程 asyncio：
  * 一条常驻后台任务 `_listen()` 消费服务端事件流，按 sessionID 过滤后交给
    `EventDispatcher` 分发（回调在此协程里被 await）。
  * `send()` 在同一 event loop 里发消息；发送期间事件会并发流入 `_listen()`。

典型用法
--------
    async with OpencodeChatClient(server, model) as chat:
        chat.dispatcher.on(EventType.MESSAGE_PART_DELTA, on_delta)
        chat.dispatcher.on(EventType.SESSION_IDLE, on_idle)
        await chat.new_session()
        await chat.send("生成PPT：/path/to/source.md")
        await chat.wait_idle()          # 等本轮跑完
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from typing import Any, cast

from opencode_ai import AsyncOpencode, DefaultAsyncHttpxClient
from opencode_ai.types import session_chat_params

from .chunks import DoneChunk, StreamChunk, TextChunk, ToolChunk
from .config import ModelConfig, ServerConfig
from .events import ChatEvent, EventDispatcher, EventType, Handler


class Turn:
    """一轮问答里 AI 侧输出的累积器。

    只收 assistant 的 text / reasoning 内容块（按 partID 全量覆盖）。用户消息的
    回显由 `_collectTurn` 按 role 挡在外面，不进这里。用于 `ask()` 在本轮 idle
    后返回「AI 这轮说了什么」。
    """

    def __init__(self) -> None:
        # partID -> (part_type, text)
        self._parts: dict[str, tuple[str, str]] = {}

    def update(self, part_id: str, part_type: str, text: str) -> None:
        self._parts[part_id] = (part_type, text)

    def _join(self, want_type: str) -> str:
        return "".join(t for (pt, t) in self._parts.values() if pt == want_type and t)

    @property
    def text(self) -> str:
        """本轮 AI 正文（不含思考）。"""
        return self._join("text")

    @property
    def reasoning(self) -> str:
        """本轮 AI 思考过程。"""
        return self._join("reasoning")


class OpencodeChatClient:
    """一个「绑定到单个会话」的异步聊天客户端。"""

    def __init__(
        self,
        server: ServerConfig,
        model: ModelConfig,
        *,
        dispatcher: EventDispatcher | None = None,
        session_id: str | None = None,
        max_iteration: int | None = None,
    ) -> None:
        self.server = server
        self.model = model
        self.dispatcher = dispatcher or EventDispatcher()
        self.session_id = session_id  # 可预先绑定已有会话
        self.max_iteration = max_iteration  # 单轮 step-finish 上限，None=不设限

        self._client: AsyncOpencode | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()  # 事件流已就绪，可以安全发消息了
        self._idle = asyncio.Event()  # 当前轮次是否已「收工」（idle / 出错 / 超迭代）
        self.Turn: Turn | None = None  # 当前轮次的 AI 输出累积器
        self._roles: dict[str, str] = {}  # messageID -> user/assistant（part 不带 role）
        self.Turn_error: str | None = None  # 本轮出错原因（None=正常结束）
        self._step_count = 0  # 本轮 step-finish 计数（配合 max_iteration）
        self._closed = False

    # —————————————————————— 生命周期 —————————————————————— #
    @property
    def raw(self) -> AsyncOpencode:
        """底层 SDK 客户端（需要用到未封装的接口时）。"""
        if self._client is None:
            raise RuntimeError("客户端尚未 connect()")
        return self._client

    async def connect(self) -> OpencodeChatClient:
        """建立连接并启动后台事件监听。发消息前必须先 connect。"""
        if self._client is not None:
            return self
        self._client = AsyncOpencode(
            base_url=self.server.base_url,
            http_client=DefaultAsyncHttpxClient(
                trust_env=self.server.trust_env,
                timeout=self.server.timeout,
            ),
        )
        # 先把监听拉起来，并等它 ready，确保不漏掉发消息后的头几个事件
        self._listen_task = asyncio.create_task(self._listen(), name="ocppt-listen")
        await self._ready.wait()
        return self

    async def close(self) -> None:
        self._closed = True
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, Exception):
                pass
            self._listen_task = None
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def __aenter__(self) -> OpencodeChatClient:
        return await self.connect()

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    # —————————————————————— 会话 —————————————————————— #
    async def new_session(self, title: str | None = None) -> str:
        """新建一个会话并绑定到本客户端，返回 session id。

        SDK 的 create() 没有 title 参数；如给了 title，通过 extra_body 透传，
        由服务端决定是否采纳（不支持则忽略，不影响建会话）。
        """
        kwargs: dict[str, Any] = {}
        if title:
            kwargs["extra_body"] = {"title": title}
        session = await self.raw.session.create(**kwargs)
        self.session_id = session.id
        return session.id

    def bind_session(self, session_id: str) -> None:
        """绑定到一个已存在的会话（不新建）。"""
        self.session_id = session_id

    # —————————————————————— 收发 —————————————————————— #
    async def send(
        self,
        text: str,
        *,
        wait: bool = False,
        extra_parts: Iterable[dict[str, Any]] | None = None,
    ) -> Any:
        """向当前会话发一条文本消息。

        Args:
            text: 用户消息正文（触发 skill 就把触发语放这里）。
            wait: True 则 await 到服务端把整轮跑完（chat() 本身是阻塞式的）；
                  False（默认）则用后台任务发出、立即返回，让事件驱动 UI，
                  之后可自行 `await wait_idle()`。
            extra_parts: 追加的内容块（如附件），可选。

        Returns:
            wait=True 时返回 AssistantMessage；wait=False 时返回后台 Task。
        """
        if self.session_id is None:
            raise RuntimeError("尚未绑定会话，请先 new_session() 或 bind_session()")

        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
        if extra_parts:
            parts.extend(extra_parts)

        self._idle.clear()  # 新一轮开始，重置收工标记
        self.Turn = Turn()  # 重置本轮 AI 输出累积器
        self.Turn_error = None  # 清掉上一轮的错误
        self._step_count = 0  # 重置迭代计数

        coro = self.raw.session.chat(
            self.session_id,
            provider_id=self.model.provider_id,
            model_id=self.model.model_id,
            parts=cast("list[session_chat_params.Part]", parts),
        )
        if wait:
            return await coro
        task = asyncio.create_task(coro, name="ocppt-send")
        # 发送协程自身抛错时（连接/参数错误，未必伴随 session.error 事件），
        # 也要让本轮立刻收工，避免 wait_idle 干等到超时。
        task.add_done_callback(self._on_send_done)
        return task

    async def wait_idle(self, timeout: float | None = None) -> bool:
        """等待当前轮次结束（收到 session.idle）。

        Returns:
            True 正常结束；False 超时。
        """
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False

    @property
    def reply_text(self) -> str:
        """最近一轮 AI 的正文（send/ask 之后、idle 时即为完整回复）。"""
        return self.Turn.text if self.Turn else ""

    @property
    def reply_reasoning(self) -> str:
        """最近一轮 AI 的思考过程。"""
        return self.Turn.reasoning if self.Turn else ""

    @property
    def last_error(self) -> str | None:
        """最近一轮的出错原因；正常（收到 session.idle）结束则为 None。

        `wait_idle()` 返回 True 只代表本轮「收工」了，不代表成功——出错和超迭代
        也会解除等待。想区分成功/失败，收工后读这个属性。
        """
        return self.Turn_error

    async def ask(self, text: str, *, timeout: float | None = 1800) -> str:
        """发一句、等本轮结束、返回 AI 这轮的正文。用于多轮对话。

        典型多轮：
            reply = await chat.ask("在 X 目录生成PPT")
            if "请提供" in reply or "源文件" in reply:      # skill 反问
                reply = await chat.ask("源文件在 X/source/source.md")

        Returns:
            本轮 AI 正文；超时则返回目前已累积的部分（配合日志判断）。
        """
        await self.send(text)  # 后台发送
        await self.wait_idle(timeout)  # 等 session.idle（超时返回已有内容）
        return self.reply_text

    async def astream(
        self,
        text: str,
        *,
        extra_parts: Iterable[dict[str, Any]] | None = None,
        idle_timeout: float | None = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """发一句，并以「拉」的方式流式产出类型化块，直到本轮收工。

        与 on/on_any（推：框架回调你）互补——这里控制权在你的 async for 循环里，
        适合打字机式实时输出，或把 opencode 的流转发成 SSE / WebSocket 给前端。
        底层仍走同一个 dispatcher（临时挂一个桥接回调、结束即摘除），可与已注册的
        回调订阅者共存、互不影响。

        产出顺序：若干 TextChunk / ToolChunk，最后**必定**一个 DoneChunk。
        DoneChunk.ok=False 时 error 为原因（出错 / 超迭代 / 超时）。

        文字优先取服务端的逐字流（message.part.delta），TextChunk.text 即本次
        新增片段，业务端直接拼接即可；服务端若不发 delta，则退回用快照算差量。
        """
        if self.session_id is None:
            raise RuntimeError("尚未绑定会话，请先 new_session() 或 bind_session()")

        queue: asyncio.Queue[ChatEvent] = asyncio.Queue()
        bridge = self.dispatcher.on_any(queue.put_nowait)  # 推 → 拉 的桥
        seen: dict[str, str] = {}  # part_id -> 已产出的全量文本（快照回退时算增量用）
        delta_parts: set[str] = set()  # 已走 delta 逐字流的 part_id（避免快照重复产出）
        part_types: dict[str, str] = {}  # part_id -> text/reasoning（delta 认不出类型，靠它回查）
        # 单独等 _idle：发送协程若自爆（_failTurn 只置 _idle、不入队），靠它兜底唤醒
        idle_task = asyncio.ensure_future(self._idle.wait())
        try:
            await self.send(text, extra_parts=extra_parts)  # 后台发送
            while True:
                get_task = asyncio.ensure_future(queue.get())
                done, _pending = await asyncio.wait(
                    {idle_task, get_task},
                    timeout=idle_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:  # 超时
                    get_task.cancel()
                    yield DoneChunk(ok=False, error=f"等待超时（{idle_timeout}s）")
                    return
                if get_task in done:  # 有事件到达，优先消费
                    for chunk in self._event_to_chunks(
                        get_task.result(), seen, delta_parts, part_types
                    ):
                        yield chunk
                    if self._idle.is_set():
                        yield DoneChunk(ok=self.Turn_error is None, error=self.Turn_error)
                        return
                    continue
                # _idle 先就绪但没有对应事件（发送协程自爆等）：排空后收尾
                get_task.cancel()
                while not queue.empty():
                    for chunk in self._event_to_chunks(
                        queue.get_nowait(), seen, delta_parts, part_types
                    ):
                        yield chunk
                yield DoneChunk(ok=self.Turn_error is None, error=self.Turn_error)
                return
        finally:
            idle_task.cancel()
            self.dispatcher.off(bridge)

    def _event_to_chunks(
        self,
        event: ChatEvent,
        seen: dict[str, str],
        delta_parts: set[str],
        part_types: dict[str, str],
    ) -> list[StreamChunk]:
        """把一条原始事件翻译成 0..N 个类型化块。

        文字优先走 message.part.delta（服务端真正的逐字流），拿不到 delta 的
        服务端再退回用 message.part.updated 的快照做差量；工具走 part.updated。
        """
        if self._is_user_echo(event):  # 用户输入的回显，不是 AI 说的，别流给调用方
            return []
        # —— 首选：逐字增量（delta 本身就是增量，直接产出）——
        if event.type == EventType.MESSAGE_PART_DELTA:
            pid = event.part_id or ""
            delta = event.delta or ""
            # delta 的 field 恒为 "text"：那是 part 内部的字段名，不是 part 类型，
            # reasoning 的增量也报 text。真实类型只能拿 partID 回查快照事件记下的账
            # （实测每个 part 的 part.updated 总先于它自己的 delta 到达）。
            kind = part_types.get(pid) or event.delta_field
            if kind in ("text", "reasoning") and delta:
                delta_parts.add(pid)  # 记账：该 part 已走 delta
                return [TextChunk(kind=kind, text=delta)]
            return []
        if event.type != EventType.MESSAGE_PART_UPDATED:
            return []
        part_type = event.part_type
        if part_type in ("text", "reasoning"):
            pid = event.part_id or ""
            part_types[pid] = cast(str, part_type)  # 记账：给 delta 回查类型用
            if pid in delta_parts:  # 已由 delta 逐字产出，跳过重复
                return []
            full = event.text or ""
            prev = seen.get(pid, "")
            if full == prev:
                return []
            # 全量覆盖 → 增量：能对上前缀就取新增部分，对不上（被改写）则整段重发
            delta = full[len(prev) :] if full.startswith(prev) else full
            seen[pid] = full
            return [TextChunk(kind=part_type, text=delta)] if delta else []
        if part_type == "tool":
            part = event.part
            if part.get("tool") == "question":  # skill 内部反问，交互块跳过
                return []
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            return [
                ToolChunk(
                    tool=part.get("tool", ""),
                    call_id=part.get("callID", ""),
                    status=(state or {}).get("status"),
                    state=state or {},
                )
            ]
        return []

    async def run_until(
        self,
        initial: str,
        *,
        done: Callable[[], bool | Awaitable[bool]],
        followup: Callable[[str, int], str] | None = None,
        max_rounds: int = 6,
        idle_timeout: float | None = 1800,
    ) -> bool:
        """多轮驱动：反复「发一句 → 等 idle → 用外部条件判断是否结束」。

        这是「以事实为准的多轮循环」：每轮结束（idle）后调用 `done()`，用**外部**
        依据（例如目标目录是否出现 .pptx）判断对话是否真的完成，而不是信模型嘴上说
        「已完成」。未完成就发一句 `followup` 继续推进，最多 `max_rounds` 轮兜底。

        Args:
            initial: 第一句（通常是完整的生成指令 build_prompt(...)）。
            done: 结束判据，返回 True 即停止。可同步可 async。
                  典型：`lambda: find_latest_pptx(project_dir) is not None`
            followup: 未完成时的下一句；入参是 (上一轮AI正文, 已完成轮数)，返回要发的话。
                      缺省发一句通用催促。
            max_rounds: 除首轮外，最多再追加多少轮（防止模型永不产出时死循环）。
            idle_timeout: 每轮等待 idle 的超时。

        Returns:
            最终 `done()` 的结果（True=达成结束条件）。
        """
        followup = followup or (
            lambda _reply, _n: (
                "还没检测到最终产物（exports/ 下的 .pptx）。"
                "请继续把剩余步骤串行跑完并导出 PPTX，完成后给出该文件的绝对路径。"
            )
        )

        reply = await self.ask(initial, timeout=idle_timeout)
        for n in range(1, max_rounds + 1):
            if await self._is_done(done):
                return True
            reply = await self.ask(followup(reply, n), timeout=idle_timeout)
        return await self._is_done(done)

    @staticmethod
    async def _is_done(done: Callable[[], bool | Awaitable[bool]]) -> bool:
        result = done()
        if inspect.isawaitable(result):
            return bool(await result)
        return bool(result)

    # —————————————————————— 历史 —————————————————————— #
    async def history_raw(self) -> list[dict[str, Any]]:
        """取回本 session 的完整历史（保真，含 tool 的 input/output）。

        opencode 不是 OpenAI 格式：role 只有 user/assistant，工具调用是 assistant
        消息里 `type=="tool"` 的 part（调用参数 input 与结果 output 合在一起）。
        这里保留 text / reasoning / tool 三类有效内容，跳过 step-start/step-finish 标记。

        Returns:
            [{"role": "user"/"assistant", "parts": [ {...}, ... ]}, ...]
            text/reasoning part → {"type", "text"}
            tool part           → {"type":"tool", "tool", "call_id", "status",
                                    "input", "output"}
        """
        if self.session_id is None:
            raise RuntimeError("尚未绑定会话，无法取历史")
        msgs = await self.raw.session.messages(self.session_id)
        out: list[dict[str, Any]] = []
        for m in msgs:
            parts: list[dict[str, Any]] = []
            for p in m.parts:
                d = p.to_dict()
                t = d.get("type")
                if t in ("text", "reasoning"):
                    text = d.get("text")
                    if text:
                        parts.append({"type": t, "text": text})
                elif t == "tool":
                    st = cast("dict[str, Any]", d.get("state") or {})
                    parts.append(
                        {
                            "type": "tool",
                            "tool": d.get("tool"),
                            "call_id": d.get("callID"),
                            "status": st.get("status"),
                            "input": st.get("input"),
                            "output": st.get("output"),
                        }
                    )
                # step-start / step-finish 等内部标记：跳过
            out.append({"role": m.info.role, "parts": parts})
        return out

    # —————————————————————— 监听内核 —————————————————————— #
    async def _listen(self) -> None:
        """常驻协程：消费服务端事件流，过滤本会话，分发给 dispatcher。"""
        assert self._client is not None
        stream = await self._client.event.list()
        self._ready.set()  # 通知 connect()：可以发消息了
        async for ev in stream:
            if self._closed:
                break
            event = ChatEvent.from_sdk(ev)

            # 只处理属于当前会话的事件；session.idle 等也带 sessionID。
            # 尚未绑定会话时（session_id is None）放行全部，便于观测 session.created。
            if self.session_id is not None and event.session_id not in (
                None,
                self.session_id,
            ):
                continue

            # 先记角色再往下走：part 事件不带 role，只能靠 messageID 回查这张表，
            # 而某条 message 的 message.updated 总先于它自己的 part 事件到达。
            self._track_role(event)

            # 内部：累积本轮 AI 输出（供 ask()/reply_text 使用），再对外分发
            self._collectTurn(event)

            # 维护收工标记后再分发，保证回调里 wait_idle 能立即看到最新状态
            if event.type == EventType.SESSION_IDLE and event.session_id == self.session_id:
                self._idle.set()
            elif event.type == EventType.SESSION_ERROR:
                self._on_session_error(event)
            elif event.type == EventType.MESSAGE_PART_UPDATED and event.part_type == "step-finish":
                self._count_step()

            await self.dispatcher.dispatch(event)

    def _track_role(self, event: ChatEvent) -> None:
        """记下 messageID -> role（角色只在 message.updated 上报一次）。"""
        if event.type == EventType.MESSAGE_UPDATED:
            mid, role = event.message_id, event.role
            if mid and role:
                self._roles[mid] = role

    def _is_user_echo(self, event: ChatEvent) -> bool:
        """这个事件是不是用户消息的回显（服务端会把用户输入也发成 text part）。

        角色未知时按「不是回显」处理：宁可多放一段，也不无声吞掉 AI 的输出。
        """
        mid = event.message_id
        return bool(mid) and self._roles.get(mid or "") == "user"

    def _collectTurn(self, event: ChatEvent) -> None:
        """把 assistant 的 text/reasoning 内容块累积进当前轮。"""
        if self.Turn is None:
            return
        if event.type != EventType.MESSAGE_PART_UPDATED:
            return
        if event.part_type in ("text", "reasoning") and event.part_id:
            if self._is_user_echo(event):  # 用户输入的回显，不算 AI 这轮说的话
                return
            self.Turn.update(event.part_id, event.part_type, event.text or "")

    # —————————————————————— 收工 / 出错 —————————————————————— #
    def _failTurn(self, reason: str) -> None:
        """标记本轮失败并解除 wait_idle 阻塞（只认第一个收工信号，后到的忽略）。"""
        if self._idle.is_set():
            return
        self.Turn_error = reason
        self._idle.set()

    def _on_send_done(self, task: asyncio.Task[Any]) -> None:
        """发送协程结束回调：把它自身抛出的异常转成本轮失败，避免静默挂起。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            self._failTurn(f"发送消息失败: {type(exc).__name__}: {exc}")

    def _on_session_error(self, event: ChatEvent) -> None:
        """服务端报错：记录原因并立刻收工，别让 wait_idle 干等到超时。"""
        err = event.error
        name = err.get("name", "UnknownError")
        data = err.get("data") if isinstance(err.get("data"), dict) else {}
        msg = (data or {}).get("message", "")
        if name == "MessageAbortedError":
            reason = "opencode 会话已被中止 (MessageAbortedError)"
        else:
            reason = f"opencode 会话错误 ({name})" + (f": {msg}" if msg else "")
        self._failTurn(reason)

    def _count_step(self) -> None:
        """数一次 step-finish；达到 max_iteration 就中止会话并兜底收工。"""
        self._step_count += 1
        if self.max_iteration and self._step_count >= self.max_iteration:
            self._failTurn(f"单轮 step 次数达到上限 {self.max_iteration}，已提前中止本轮")
            self._abort_session_soon()

    def _abort_session_soon(self) -> None:
        """尽力中止服务端仍在跑的这一轮（fire-and-forget，不阻塞监听协程）。"""
        if self._client is None or self.session_id is None:
            return
        session_id = self.session_id  # 收窄到局部，narrowing 才能带进下面的闭包

        async def _abort() -> None:
            with contextlib.suppress(Exception):  # 兜底动作，失败无所谓
                await self.raw.session.abort(session_id)

        asyncio.create_task(_abort(), name="ocppt-abort")

    # —————————————————————— 便捷注册 —————————————————————— #
    def on(self, event_type: str, handler: Handler) -> Handler:
        """转发到内部 dispatcher.on，方便链式书写。"""
        return self.dispatcher.on(event_type, handler)

    def on_any(self, handler: Handler) -> Handler:
        return self.dispatcher.on_any(handler)
