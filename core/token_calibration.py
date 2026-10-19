"""按 provider/model 持久化的 token 估算校准。

为什么需要：
    tiktoken 的 tokenizer 与各 provider 的实际计费口径不同（中文差异尤其明显）。
    仅靠进程内的 EWMA 会在重启后丢失，导致上下文预算和压缩时机反复跑偏。
    这里把每次真实调用的 ``actual / estimated`` 样本持久化到实例目录，
    启动时恢复；文件很小，原子写入即可。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import orjson

_VERSION = 1
_MIN_RATIO = 0.35
_MAX_RATIO = 2.0
_SAMPLE_RATIO_MIN = 0.3
_SAMPLE_RATIO_MAX = 3.0
_EWMA_ALPHA = 0.2
_WARMUP_SAMPLES = 3
_MAPE_WINDOW = 20


@dataclass(slots=True)
class CalibrationState:
    """一个 provider/model 的校准状态。"""

    ratio: float = 1.0
    samples: int = 0
    mape: float = 0.0
    updated_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def calibration_key(provider: str, model: str) -> str:
    provider_text = str(provider or "").strip() or "-"
    model_text = str(model or "").strip() or "-"
    return f"{provider_text}|{model_text}"


class TokenCalibrationStore:
    """轻量 JSON 校准存储，线程安全、原子写入。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._states: dict[str, CalibrationState] = {}
        self._load()

    # ------------------------------------------------------------------
    # 读写
    # ------------------------------------------------------------------
    def get(self, provider: str, model: str) -> CalibrationState:
        key = calibration_key(provider, model)
        with self._lock:
            state = self._states.get(key)
            return CalibrationState(**state.as_dict()) if state else CalibrationState()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: state.as_dict() for key, state in self._states.items()}

    def update_sample(
        self,
        provider: str,
        model: str,
        *,
        estimated_prompt_tokens: int,
        actual_prompt_tokens: int,
    ) -> CalibrationState:
        """记录一次真实样本并返回更新后的状态。

        单次样本 ratio 先夹到 [0.3, 3.0]；累计 ratio 再夹到 [0.35, 2.0]。
        前 3 个样本取均值，之后用 EWMA，避免首轮噪声把预算带飞。
        """
        if estimated_prompt_tokens <= 0 or actual_prompt_tokens <= 0:
            return self.get(provider, model)

        sample_ratio = actual_prompt_tokens / estimated_prompt_tokens
        sample_ratio = min(_SAMPLE_RATIO_MAX, max(_SAMPLE_RATIO_MIN, sample_ratio))
        sample_error = abs(actual_prompt_tokens - estimated_prompt_tokens) / max(
            1, actual_prompt_tokens
        )
        key = calibration_key(provider, model)
        with self._lock:
            current = self._states.get(key) or CalibrationState()
            if current.samples < _WARMUP_SAMPLES:
                blended = (
                    current.ratio * current.samples + sample_ratio
                ) / (current.samples + 1)
            else:
                blended = current.ratio * (1.0 - _EWMA_ALPHA) + sample_ratio * _EWMA_ALPHA
            ratio = min(_MAX_RATIO, max(_MIN_RATIO, blended))
            mape_samples = min(current.samples, _MAPE_WINDOW)
            mape = (
                current.mape * mape_samples + sample_error
            ) / (mape_samples + 1)
            state = CalibrationState(
                ratio=ratio,
                samples=current.samples + 1,
                mape=mape,
                updated_at=time.time(),
            )
            self._states[key] = state
            self._save_unlocked()
            return CalibrationState(**state.as_dict())

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = orjson.loads(self.path.read_bytes())
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        models = payload.get("models")
        if not isinstance(models, dict):
            return
        for key, value in models.items():
            if not isinstance(value, dict):
                continue
            try:
                ratio = float(value.get("ratio", 1.0))
            except (TypeError, ValueError):
                ratio = 1.0
            if not (0.05 <= ratio <= 10.0):
                ratio = 1.0
            self._states[str(key)] = CalibrationState(
                ratio=ratio,
                samples=max(0, int(value.get("samples") or 0)),
                mape=max(0.0, float(value.get("mape") or 0.0)),
                updated_at=float(value.get("updated_at") or 0.0),
            )

    def _save_unlocked(self) -> None:
        payload = {
            "version": _VERSION,
            "updated_at": time.time(),
            "models": {key: state.as_dict() for key, state in self._states.items()},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".{self.path.name}.tmp")
            tmp.write_bytes(orjson.dumps(payload))
            os.replace(tmp, self.path)
        except OSError:
            # 校准是优化项，写失败不能影响主流程。
            pass


__all__ = [
    "CalibrationState",
    "TokenCalibrationStore",
    "calibration_key",
]
