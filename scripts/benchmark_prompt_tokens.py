"""只读 benchmark：对比重构前后的 prompt token 估算。

用法：
    .venv/bin/python scripts/benchmark_prompt_tokens.py \
      --db ~/.local/share/Debata_Agent/instances/default/memory/小堡/小堡.db \
      --persona 小堡 --model deepseek-flash

不会写数据库、不会调用模型、不会修改任何文件。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.context_builder import build_combined_system_prompt  # noqa: E402
from agents.persona_loader import load_persona  # noqa: E402
from app_config.paths import AppPaths  # noqa: E402
from core.model_view import ModelViewPolicy, build_model_view  # noqa: E402
from tools import build_default_registry  # noqa: E402
from utils.token_budget import TokenEstimator  # noqa: E402


def _load_history(db_path: Path) -> tuple[list[dict[str, Any]], int]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "select coalesce(active_start_index, 0) from rolling_summary limit 1"
        ).fetchone()
        active_start = int(row[0]) if row else 0
        rows = connection.execute(
            "select record_json from history_records order by history_index"
        ).fetchall()
    finally:
        connection.close()
    records: list[dict[str, Any]] = []
    for (raw,) in rows:
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict):
            records.append(record)
    return records[max(0, active_start):], active_start


def _fake_config(profile: str, memory_mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        features=SimpleNamespace(
            long_term_memory=SimpleNamespace(mode=memory_mode),
            vision=SimpleNamespace(enabled=True),
            web_search=SimpleNamespace(enabled=True),
            weather=SimpleNamespace(enabled=False),
        ),
        persona_management=None,
        behavior=SimpleNamespace(tool_schema_profile=profile),
    )


def _tool_tokens(profile: str, model: str, memory_mode: str) -> tuple[int, int]:
    registry = build_default_registry(_fake_config(profile, memory_mode))
    estimator = TokenEstimator(model=model)
    schemas = registry.get_schemas()
    return len(schemas), estimator.estimate_tools_schema(schemas)


def _signature(record: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    content = str(record.get("content") or "")
    digest = hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()[:16]
    tool_ids = tuple(
        str(tool_call.get("id") or "")
        for tool_call in record.get("tool_calls") or []
        if isinstance(tool_call, dict)
    )
    return str(record.get("role") or ""), digest, tool_ids


def _simulate_prefix(
    records: list[dict[str, Any]],
    *,
    policy: ModelViewPolicy,
    steps: int = 20,
) -> tuple[float, int, int]:
    if len(records) < 2:
        return 0.0, 0, 0
    step = max(1, len(records) // max(1, steps))
    previous: list[tuple[str, str, tuple[str, ...]]] | None = None
    invalidated: list[int] = []
    for end in range(step, len(records) + 1, step):
        view = build_model_view(records[:end], policy=policy)
        current = [_signature(record) for record in view.records]
        if previous is not None:
            common = 0
            for left, right in zip(previous, current, strict=False):
                if left != right:
                    break
                common += 1
            invalidated.append(len(previous) - common)
        previous = current
    if not invalidated:
        return 0.0, 0, 0
    return sum(invalidated) / len(invalidated), max(invalidated), len(invalidated)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Debata 人格 SQLite 路径")
    parser.add_argument("--persona", default="小堡", help="人格名")
    parser.add_argument("--model", default="deepseek-flash", help="用于 tokenizer 的模型名")
    parser.add_argument("--memory-mode", default="file", choices=["file", "rag"])
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--no-simulate", action="store_true", help="跳过前缀稳定性模拟")
    args = parser.parse_args(argv)

    records, active_start = _load_history(Path(args.db).expanduser())
    policy = ModelViewPolicy()
    view = build_model_view(records, policy=policy)
    estimator = TokenEstimator(model=args.model)
    raw_tokens = estimator.estimate_messages(records)
    view_tokens = estimator.estimate_messages(view.records)

    persona = None
    try:
        persona = load_persona(AppPaths(), args.persona)
    except Exception:
        persona = None
    system_tokens = 0
    if persona is not None:
        system_prompt = build_combined_system_prompt(
            persona,
            memory_mode=args.memory_mode,
        )
        system_tokens = estimator.estimate_text(system_prompt)

    full_count, full_tools_tokens = _tool_tokens("full", args.model, args.memory_mode)
    compact_count, compact_tools_tokens = _tool_tokens(
        "compact", args.model, args.memory_mode
    )
    compact_registry = build_default_registry(
        _fake_config("compact", args.memory_mode)
    )
    compact_schemas = compact_registry.get_schemas()
    compact_stub_count = sum(
        1
        for schema in compact_schemas
        if "_tool_search_required"
        in (schema.get("function", {}).get("parameters", {}).get("properties", {}))
    )

    before_prompt = raw_tokens + system_tokens + full_tools_tokens
    after_prompt = view_tokens + system_tokens + compact_tools_tokens
    reduction = 0.0 if before_prompt <= 0 else 1 - after_prompt / before_prompt

    payload = {
        "db": str(args.db),
        "active_start": active_start,
        "records_raw": len(records),
        "records_view": len(view.records),
        "tokens_raw": raw_tokens,
        "tokens_view": view_tokens,
        "view_reduction": 0.0 if raw_tokens <= 0 else 1 - view_tokens / raw_tokens,
        "system_tokens": system_tokens,
        "tools_full": {"count": full_count, "tokens": full_tools_tokens},
        "tools_compact": {
            "count": compact_count,
            "tokens": compact_tools_tokens,
            "stub_count": compact_stub_count,
        },
        "estimated_prompt_before": before_prompt,
        "estimated_prompt_after": after_prompt,
        "estimated_prompt_reduction": reduction,
        "model_view": {
            "dropped_runtime": view.dropped_runtime,
            "dropped_no_action_pairs": view.dropped_no_action_pairs,
            "dropped_no_action_records": view.dropped_no_action_records,
            "capped_records": view.capped_records,
            "projected_tool_results": view.projected_tool_results,
        },
    }
    if not args.no_simulate:
        average, maximum, samples = _simulate_prefix(records, policy=policy)
        payload["prefix_stability"] = {
            "avg_invalidated_records": average,
            "max_invalidated_records": maximum,
            "samples": samples,
        }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print("Debata prompt token benchmark（只读估算）")
    print(f"  数据库        : {args.db}")
    print(f"  活跃记录      : {len(records)} -> {len(view.records)}")
    print(f"  活跃 token    : {raw_tokens:,} -> {view_tokens:,} "
          f"({payload['view_reduction']:.1%} 降低)")
    print(f"  系统提示      : {system_tokens:,} token")
    print(f"  工具 schema   : full={full_count} 个/{full_tools_tokens:,} token"
          f" -> compact={compact_count} 个/{compact_tools_tokens:,} token"
          f"（stub {compact_stub_count} 个）")
    print(f"  估算 prompt   : {before_prompt:,} -> {after_prompt:,} "
          f"({reduction:.1%} 降低)")
    print(
        "  模型视图丢弃  : "
        f"runtime={view.dropped_runtime}, "
        f"no_action_pairs={view.dropped_no_action_pairs}, "
        f"capped={view.capped_records}, "
        f"projected_tools={view.projected_tool_results}"
    )
    if "prefix_stability" in payload:
        prefix = payload["prefix_stability"]
        print(
            "  前缀稳定性    : "
            f"平均失效 {prefix['avg_invalidated_records']:.1f} 条，"
            f"最大 {prefix['max_invalidated_records']} 条"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
