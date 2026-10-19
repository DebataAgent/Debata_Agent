"""用量聚合与估算误差测试。"""

from __future__ import annotations

from utils.usage_summary import UsageSummary, add_record_to_summary


def test_summary_clamps_cached_tokens_and_exposes_uncached():
    summary = UsageSummary()
    add_record_to_summary(
        summary,
        {
            "prompt_tokens": 100,
            "cached_tokens": 130,
            "cache_creation_tokens": 200,
            "completion_tokens": 10,
            "total_tokens": 110,
        },
    )
    assert summary.prompt_tokens == 100
    assert summary.cached_tokens == 100
    assert summary.cache_creation_tokens == 100
    assert summary.uncached_prompt_tokens == 0
    assert summary.cache_hit_rate == 1.0


def test_summary_collects_estimate_error_only_for_samples():
    summary = UsageSummary()
    add_record_to_summary(
        summary,
        {
            "prompt_tokens": 1000,
            "estimated_prompt_tokens": 900,
            "completion_tokens": 50,
        },
    )
    add_record_to_summary(
        summary,
        {
            "prompt_tokens": 500,
            "completion_tokens": 20,
        },
    )
    assert summary.estimate_samples == 1
    assert summary.estimated_prompt_tokens == 900
    assert summary.estimate_actual_prompt_tokens == 1000
    assert summary.estimate_abs_error_tokens == 100
    assert abs(summary.estimate_error_rate - 0.1) < 1e-9


def test_empty_summary_has_zero_error_rate():
    summary = UsageSummary()
    assert summary.uncached_prompt_tokens == 0
    assert summary.estimate_error_rate == 0.0
