"""WebUI 实时事件集线器。

把三类实时数据统一推到浏览器的 ``/ws/events``：

    - ``log``      —— root logger 的日志行（来自 :mod:`webui.logbuffer`）
    - ``chat``     —— 新收发的 QQ 消息（来自 ``rt.pipeline.chat_timeline.subscribe``）
    - ``status``   —— 运行状态快照（轮询 ``RuntimeHost.status()``，有变化才推）

``ChatTimelineStore.subscribe`` 的回调可能在任意线程触发，所以这里和 logbuffer
一样，一律用 ``loop.call_soon_threadsafe`` 回到事件循环再广播。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

STATUS_POLL_SECONDS = 2.0


class EventHub:
    """单个 WebSocket 连接的订阅集合。"""

    def __init__(self, host: Any, log_buffer: Any) -> None:
        self.host = host
        self.log_buffer = log_buffer
        self._clients: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._log_queue: asyncio.Queue | None = None
        self._unsubscribe_timeline = None
        self._status_task: asyncio.Task | None = None
        self._last_status: str = ""

    # ============================================================
    # 生命周期
    # ============================================================

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def start(self) -> None:
        """开始消费日志与 chat_timeline。"""
        if self._log_queue is None:
            self._log_queue = self.log_buffer.subscribe()
            self._status_task = asyncio.create_task(
                self._status_loop(), name="webui-status-push"
            )

    async def stop(self) -> None:
        if self._log_queue is not None:
            self.log_buffer.unsubscribe(self._log_queue)
            self._log_queue = None
        if self._unsubscribe_timeline is not None:
            try:
                self._unsubscribe_timeline()
            except Exception:  # noqa: BLE001
                logger.debug("取消 chat_timeline 订阅失败", exc_info=True)
            self._unsubscribe_timeline = None
        if self._status_task is not None:
            self._status_task.cancel()
            try:
                await self._status_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._status_task = None

    def attach_timeline(self) -> None:
        """Runtime 就绪后订阅 chat_timeline（每次重启都要重新订阅）。"""
        rt = getattr(self.host, "rt", None)
        if rt is None:
            return
        timeline = getattr(getattr(rt, "pipeline", None), "chat_timeline", None)
        if timeline is None or self._unsubscribe_timeline is not None:
            return
        self._unsubscribe_timeline = timeline.subscribe(self._on_timeline_message)

    # ============================================================
    # 客户端
    # ============================================================

    def register(self, queue: asyncio.Queue) -> None:
        self._clients.add(queue)

    def unregister(self, queue: asyncio.Queue) -> None:
        self._clients.discard(queue)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        for queue in list(self._clients):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def publish_threadsafe(self, payload: dict[str, Any]) -> None:
        """从任意线程投递一条事件。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._broadcast, payload)
        except RuntimeError:
            pass

    # ============================================================
    # 数据源
    # ============================================================

    def _on_timeline_message(self, message: Any) -> None:
        self.publish_threadsafe({"type": "chat", "data": _timeline_to_dict(message)})

    async def _status_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(STATUS_POLL_SECONDS)
                status = self.host.status()
                fingerprint = (
                    f"{status.get('state')}|{status.get('adapter_connected')}|"
                    f"{status.get('persona')}|{status.get('last_error')}"
                )
                if fingerprint == self._last_status:
                    continue
                self._last_status = fingerprint
                self._broadcast({"type": "status", "data": status})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.debug("状态推送循环异常", exc_info=True)
                await asyncio.sleep(STATUS_POLL_SECONDS)

    async def pump_logs(self, queue: asyncio.Queue) -> None:
        """把 logbuffer 的内容转给某个客户端队列（由 WS 路由调用）。"""
        if self._log_queue is None:
            return
        while True:
            item = await self._log_queue.get()
            try:
                queue.put_nowait({"type": "log", "data": item})
            except asyncio.QueueFull:
                pass


def _timeline_to_dict(message: Any) -> dict[str, Any]:
    """``ChatTimelineMessage`` → JSON 友好的 dict。"""
    attachments = []
    for att in getattr(message, "attachments", None) or []:
        attachments.append(
            {
                "type": getattr(att, "type", ""),
                "name": getattr(att, "name", None),
                "url": getattr(att, "url", None),
                "text": getattr(att, "text", None),
            }
        )
    return {
        "conversation_id": getattr(message, "conversation_id", ""),
        "direction": getattr(message, "direction", ""),
        "timestamp": getattr(message, "timestamp", 0.0),
        "time_text": getattr(message, "time_text", ""),
        "sender_name": getattr(message, "sender_name", ""),
        "sender_id": getattr(message, "sender_id", ""),
        "target_id": getattr(message, "target_id", ""),
        "group_id": getattr(message, "group_id", None),
        "msg_id": getattr(message, "msg_id", ""),
        "text": getattr(message, "text", ""),
        "reply_to": getattr(message, "reply_to", None),
        "attachments": attachments,
        "source": getattr(message, "source", ""),
        "pushed_at": time.time(),
    }


__all__ = ["EventHub"]
