"""模型 usage 聚合的轻量共享类型。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

UsageRange = Literal["today", "7d", "30d", "all"]


@dataclass(slots=True)
class UsageSummary:
    request_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0
    total_tokens: int = 0

    # ---- 估算准确性（只有带 estimated_prompt_tokens 的记录才计入）----
    estimated_prompt_tokens: int = 0
    estimate_actual_prompt_tokens: int = 0
    estimate_abs_error_tokens: int = 0
    estimate_samples: int = 0

    @property
    def cache_hit_rate(self) -> float:
        if self.prompt_tokens <= 0:
            return 0.0
        return max(0, min(self.cached_tokens, self.prompt_tokens)) / self.prompt_tokens

    @property
    def uncached_prompt_tokens(self) -> int:
        """未命中缓存的输入 token（cached 是 prompt 的子集）。"""
        return max(0, self.prompt_tokens - max(0, self.cached_tokens))

    @property
    def estimate_error_rate(self) -> float:
        """加权平均估算误差：sum(|估算-实际|) / sum(实际)。

        没有估算样本时返回 0，调用方应同时检查 ``estimate_samples``。
        """
        if self.estimate_samples <= 0 or self.estimate_actual_prompt_tokens <= 0:
            return 0.0
        return self.estimate_abs_error_tokens / self.estimate_actual_prompt_tokens


def add_record_to_summary(summary: UsageSummary, record: dict) -> None:
    """把一条 usage record 累加到 summary，统一处理脏数据与估算误差。"""
    prompt = int(record.get("prompt_tokens") or 0)
    cached = int(record.get("cached_tokens") or 0)
    cached = max(0, min(cached, prompt))
    completion = int(record.get("completion_tokens") or 0)
    total = int(record.get("total_tokens") or 0) or (prompt + completion)
    summary.request_count += 1
    summary.prompt_tokens += prompt
    summary.completion_tokens += completion
    summary.reasoning_tokens += int(record.get("reasoning_tokens") or 0)
    summary.cached_tokens += cached
    summary.cache_creation_tokens += max(
        0,
        min(int(record.get("cache_creation_tokens") or 0), prompt),
    )
    summary.total_tokens += total
    estimated = int(record.get("estimated_prompt_tokens") or 0)
    if estimated > 0 and prompt > 0:
        summary.estimate_samples += 1
        summary.estimated_prompt_tokens += estimated
        summary.estimate_actual_prompt_tokens += prompt
        summary.estimate_abs_error_tokens += abs(estimated - prompt)


def cutoff_timestamp(range_name: UsageRange) -> float | None:
    now = time.time()
    if range_name == "today":
        local = time.localtime(now)
        start = time.struct_time(
            (
                local.tm_year,
                local.tm_mon,
                local.tm_mday,
                0,
                0,
                0,
                local.tm_wday,
                local.tm_yday,
                local.tm_isdst,
            )
        )
        return time.mktime(start)
    if range_name == "7d":
        return now - 7 * 86400
    if range_name == "30d":
        return now - 30 * 86400
    return None


__all__ = [
    "UsageRange",
    "UsageSummary",
    "add_record_to_summary",
    "cutoff_timestamp",
]
