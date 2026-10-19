"""Token 预算估算工具。

热路径只做本地估算，不调用模型。优先使用 tiktoken；不可用时退回粗略字符估算。

口径说明：
    - 文本 token 数来自 tiktoken（或字符兜底），再乘以按 provider/model 持久化的
      校准系数。校准样本来自 provider 返回的真实 ``prompt_tokens``。
    - messages 按 provider 实际发送口径（``normalize_messages``）序列化为规范 JSON，
      避免把 metadata / conversation_id / reasoning_content 等本地字段算进去。
    - tools schema 同样按规范 JSON 计算，且按 registry 返回的列表对象缓存，
      避免每轮重新 encode 3 万字符的 schema。
    - 预检估算与压缩触发统一使用 ``estimate_prompt``，不再用 ``str(tools_schema)``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

try:  # orjson 是项目依赖；测试环境缺失时退回标准库
    import orjson as _orjson
except Exception:  # pragma: no cover - 依赖缺失兜底
    _orjson = None


def _canonical_json(value: Any) -> str:
    if _orjson is not None:
        return _orjson.dumps(
            value,
            option=_orjson.OPT_SORT_KEYS,
            default=str,
        ).decode("utf-8")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


# 单条消息的角色/分隔符/协议开销。OpenAI 兼容协议通常按 3-4 token/消息计。
_MESSAGE_OVERHEAD_TOKENS = 4
# 单个工具定义的协议开销（name/type/parameters 包装）。
_TOOL_OVERHEAD_TOKENS = 8
# 只缓存不超过此长度的文本，避免 lru_cache 持有大块上下文。
_TEXT_CACHE_CHAR_LIMIT = 8192


@dataclass(slots=True)
class PromptTokenParts:
    """一次 prompt 估算的组成部分（均为校准前 raw token）。"""

    message_tokens_raw: int
    tool_tokens_raw: int
    overhead_tokens: int

    @property
    def raw_total(self) -> int:
        return max(
            1,
            self.message_tokens_raw + self.tool_tokens_raw + self.overhead_tokens,
        )


@dataclass(slots=True)
class TokenBudget:
    max_context_tokens: int
    reserve_output_tokens: int
    memory_token_budget: int
    summary_token_budget: int

    @property
    def total_input_budget(self) -> int:
        return max(1024, self.max_context_tokens - self.reserve_output_tokens)


class TokenEstimator:
    """带简单校准系数的 token 估算器。

    ``calib_ratio`` 会乘到所有 raw token 结果上。校准应该只反映 tokenizer 与
    provider 实际计费的差异，因此 messages / tools 都使用同一系数。
    """

    def __init__(
        self,
        model: str = "",
        calib_ratio: float = 1.0,
        *,
        provider: str = "",
        provider_overhead_tokens: int = 0,
    ) -> None:
        self.model = model
        self.provider = provider
        self.calib_ratio = max(0.05, float(calib_ratio or 1.0))
        self.provider_overhead_tokens = max(0, int(provider_overhead_tokens))
        self._tools_cache: dict[int, tuple[int, int]] = {}

    # ------------------------------------------------------------------
    # 文本
    # ------------------------------------------------------------------
    def estimate_text(self, text: str) -> int:
        raw = raw_text_tokens(text, self.model)
        return max(1, int(round(raw * self.calib_ratio)))

    def raw_text_tokens(self, text: str) -> int:
        return raw_text_tokens(text, self.model)

    # ------------------------------------------------------------------
    # messages / tools / prompt
    # ------------------------------------------------------------------
    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        raw = self.raw_message_tokens(messages)
        return max(1, int(round(raw * self.calib_ratio)))

    def raw_message_tokens(self, messages: list[dict[str, Any]]) -> int:
        total = 0
        for message in _normalize_messages_for_estimate(messages):
            total += _MESSAGE_OVERHEAD_TOKENS
            total += raw_text_tokens(_canonical_json(message), self.model)
        return max(1, total)

    def estimate_tools_schema(self, tools_schema: list[dict[str, Any]] | None) -> int:
        raw = self.raw_tools_tokens(tools_schema)
        return max(1, int(round(raw * self.calib_ratio))) if raw > 0 else 0

    def raw_tools_tokens(self, tools_schema: list[dict[str, Any]] | None) -> int:
        if not tools_schema:
            return 0
        cache_key = id(tools_schema)
        cached = self._tools_cache.get(cache_key)
        if cached is not None and cached[0] == len(tools_schema):
            return cached[1]
        raw = raw_text_tokens(_canonical_json(tools_schema), self.model)
        raw += _TOOL_OVERHEAD_TOKENS * len(tools_schema)
        self._tools_cache[cache_key] = (len(tools_schema), raw)
        # 只保留最近几份 schema，避免 id 长期持有造成泄漏
        if len(self._tools_cache) > 8:
            for key in list(self._tools_cache)[:-8]:
                self._tools_cache.pop(key, None)
        return raw

    def estimate_prompt_parts(
        self,
        messages: list[dict[str, Any]],
        tools_schema: list[dict[str, Any]] | None = None,
        *,
        provider_overhead_tokens: int | None = None,
    ) -> PromptTokenParts:
        overhead = (
            self.provider_overhead_tokens
            if provider_overhead_tokens is None
            else max(0, int(provider_overhead_tokens))
        )
        return PromptTokenParts(
            message_tokens_raw=self.raw_message_tokens(messages),
            tool_tokens_raw=self.raw_tools_tokens(tools_schema),
            overhead_tokens=overhead,
        )

    def estimate_prompt(
        self,
        messages: list[dict[str, Any]],
        tools_schema: list[dict[str, Any]] | None = None,
        *,
        provider_overhead_tokens: int | None = None,
    ) -> int:
        parts = self.estimate_prompt_parts(
            messages,
            tools_schema,
            provider_overhead_tokens=provider_overhead_tokens,
        )
        return max(1, int(round(parts.raw_total * self.calib_ratio)))

    # ------------------------------------------------------------------
    # 校准
    # ------------------------------------------------------------------
    def set_calibration(self, ratio: float) -> None:
        self.calib_ratio = min(2.0, max(0.35, float(ratio or 1.0)))

    def update_calibration(self, estimated: int, actual_prompt_tokens: int) -> None:
        """用一次真实调用的 (估算, 实际) 反馈修正系数。

        两道护栏，防止一次异常样本把系数带偏（估算器决定上下文预算与压缩时机，
        一旦跑飞会让压缩策略整体失准）：
          - 单次 ratio 先夹到 [0.3, 3.0]，挡掉口径错误或含工具循环累加值的样本；
          - 累计 calib_ratio 再夹到 [0.35, 2.0]。
        """
        if estimated <= 0 or actual_prompt_tokens <= 0:
            return
        ratio = actual_prompt_tokens / estimated
        ratio = min(3.0, max(0.3, ratio))
        blended = self.calib_ratio * 0.8 + ratio * 0.2
        self.calib_ratio = min(2.0, max(0.35, blended))


def _estimate_text_raw(text: str, model: str = "") -> int:
    if not text:
        return 1
    try:
        enc = _encoding_for_model(model)
        return len(enc.encode(text))
    except Exception:
        # 中文约 1.5 字/token，英文约 4 chars/token；用保守混合估算。
        return max(1, len(text) // 2)


@lru_cache(maxsize=4096)
def _cached_raw_text_tokens(model: str, text: str) -> int:
    return _estimate_text_raw(text, model)


def raw_text_tokens(text: str, model: str = "") -> int:
    """返回未经校准的 token 数。

    小文本走 LRU 缓存；大文本直接编码，避免缓存持有整段上下文。
    """
    if not text:
        return 1
    if len(text) <= _TEXT_CACHE_CHAR_LIMIT:
        return _cached_raw_text_tokens(model, text)
    return _estimate_text_raw(text, model)


def warm_token_estimator(model: str = "") -> None:
    """预热并缓存 tokenizer，避免首条真实消息触发同步加载。"""
    _encoding_for_model(model)
    raw_text_tokens("warmup", model)


@lru_cache(maxsize=32)
def _encoding_for_model(model: str = ""):
    import tiktoken

    try:
        return tiktoken.encoding_for_model(model) if model else tiktoken.get_encoding("o200k_base")
    except Exception:
        try:
            return tiktoken.get_encoding("o200k_base")
        except Exception:
            return tiktoken.get_encoding("cl100k_base")


def _normalize_messages_for_estimate(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 provider 默认发送口径规整消息，避免把本地存储字段计入预算。"""
    from providers.base import normalize_messages

    return normalize_messages(messages)


__all__ = [
    "PromptTokenParts",
    "TokenBudget",
    "TokenEstimator",
    "raw_text_tokens",
    "warm_token_estimator",
]
