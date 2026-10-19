"""日志环形缓冲 + 实时推送。

原 GUI 的日志页给 root logger 挂了个 Qt 信号 Handler，把日志灌进 QListWidget。
WebUI 这里换成同样的思路：挂一个 ``logging.Handler``，写进定长 deque，
同时投递给所有 WebSocket 订阅者。

``logging.Handler.emit`` 可能在任何线程被调用（Runtime 里有工作线程），
所以投递一律走 ``loop.call_soon_threadsafe``；没有事件循环时只缓存、不推送。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime
from typing import Any

DEFAULT_CAPACITY = 2000

LEVEL_NAMES = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class LogRingBuffer(logging.Handler):
    """把日志留在内存里，供 WebUI 拉取历史并实时订阅。"""

    def __init__(self, capacity: int = DEFAULT_CAPACITY, level: int = logging.NOTSET) -> None:
        super().__init__(level=level)
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seq = 0

    # ----- 订阅管理 -----

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, maxsize: int = 500) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    # ----- 读取 -----

    def tail(self, limit: int = 300) -> list[dict[str, Any]]:
        """返回最近 ``limit`` 条（按时间正序）。"""
        if limit <= 0:
            return []
        return list(self._records)[-limit:]

    def __len__(self) -> int:
        return len(self._records)

    # ----- logging.Handler -----

    def emit(self, record: logging.LogRecord) -> None:
        try:
            item = self._to_item(record)
        except Exception:  # noqa: BLE001 - 日志处理绝不能反过来炸业务
            return
        self._records.append(item)
        self._publish(item)

    def _to_item(self, record: logging.LogRecord) -> dict[str, Any]:
        self._seq += 1
        created = getattr(record, "created", time.time())
        return {
            "seq": self._seq,
            "ts": created,
            "time_text": datetime.fromtimestamp(created).strftime("%H:%M:%S"),
            "level": LEVEL_NAMES.get(record.levelno, record.levelname),
            "level_no": record.levelno,
            "logger": record.name,
            "message": record.getMessage(),
            "exc": self._format_exception(record),
        }

    @staticmethod
    def _format_exception(record: logging.LogRecord) -> str:
        if not record.exc_info:
            return ""
        try:
            return logging.Formatter().formatException(record.exc_info)
        except Exception:  # noqa: BLE001
            return ""

    def _publish(self, item: dict[str, Any]) -> None:
        if not self._subscribers:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, item)
        except RuntimeError:
            # 事件循环正在关闭；丢掉这条即可。
            pass

    def _fanout(self, item: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                # 订阅者读得太慢，丢最旧的一条，保证最新日志能到。
                try:
                    queue.get_nowait()
                    queue.put_nowait(item)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


_installed: LogRingBuffer | None = None


def install_log_buffer(level: int = logging.INFO) -> LogRingBuffer:
    """把环形缓冲挂到 root logger 上（幂等）。"""
    global _installed
    if _installed is not None:
        return _installed
    handler = LogRingBuffer()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    _installed = handler
    return handler


def get_log_buffer() -> LogRingBuffer | None:
    return _installed


__all__ = [
    "DEFAULT_CAPACITY",
    "LEVEL_NAMES",
    "LogRingBuffer",
    "get_log_buffer",
    "install_log_buffer",
]
