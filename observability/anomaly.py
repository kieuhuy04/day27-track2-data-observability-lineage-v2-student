"""Anomaly detection starter.

Z-score is deliberately the default baseline. Students should improve `auto`
mode for seasonality/outliers rather than deleting the simple implementation.
"""
from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def zscore_detector(current: float, history: Iterable[float], threshold: float = 3.0) -> dict[str, Any]:
    values = np.asarray(list(history), dtype=float)
    if values.size < 3:
        return {"is_anomaly": False, "score": 0.0, "method": "zscore", "reason": "insufficient_history"}
    mean = float(np.mean(values))
    std = float(np.std(values))
    if std == 0:
        score = float("inf") if float(current) != mean else 0.0
    else:
        score = abs(float(current) - mean) / std
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "zscore",
        "reason": f"mean={mean:.3f}, std={std:.3f}, threshold={threshold}",
    }


def mad_detector(current: float, history: Iterable[float], threshold: float = 3.5) -> dict[str, Any]:
    """Robust example, intentionally incomplete around zero-MAD edge cases.

    Students may improve this function and/or use it from auto mode.
    """
    values = np.asarray(list(history), dtype=float)
    if values.size < 5:
        return {"is_anomaly": False, "score": 0.0, "method": "mad", "reason": "insufficient_history"}
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    if mad == 0:
        return {"is_anomaly": False, "score": 0.0, "method": "mad", "reason": "mad_is_zero_todo"}
    modified_z = 0.6745 * abs(float(current) - median) / mad
    return {
        "is_anomaly": bool(modified_z > threshold),
        "score": float(modified_z),
        "method": "mad",
        "reason": f"median={median:.3f}, mad={mad:.3f}, threshold={threshold}",
    }


def auto_detector(
    current: float,
    history: Iterable[float],
    *,
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Context-aware robust baseline used by `method="auto"`.

    - Seasonality: prefers `context["same_segment_history"]` (e.g. same
      weekday) over the raw `history` when it has enough points, so a
      legitimate Saturday dip is not compared against weekday volume.
    - Robust baseline: uses median/MAD (modified z-score) instead of the
      mean/std z-score once there is enough history, since MAD is far less
      sensitive to the outlier we are trying to detect than mean/std are.
    - Falls back to z-score when history is too short for a stable MAD.
    """
    context = context or {}
    same_segment = context.get("same_segment_history")
    used_segment = bool(same_segment) and len(list(same_segment)) >= 3
    effective_history = list(same_segment) if used_segment else list(history)
    values = np.asarray(effective_history, dtype=float)

    if values.size >= 5:
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        if mad > 0:
            modified_z = 0.6745 * abs(float(current) - median) / mad
            result = {
                "is_anomaly": bool(modified_z > 3.5),
                "score": float(modified_z),
                "method": "mad",
                "reason": f"median={median:.3f}, mad={mad:.3f}, mad_threshold=3.5",
            }
        else:
            # Zero-MAD edge case: the baseline is (near) constant, so the
            # modified z-score denominator is undefined. Fall back to a
            # tight relative deviation from the median instead of silently
            # reporting "no anomaly" for every current value.
            spread = max(abs(median) * 0.01, 1e-9)
            result = {
                "is_anomaly": bool(float(current) != median),
                "score": float(abs(float(current) - median) / spread),
                "method": "mad_zero_fallback",
                "reason": f"constant_baseline median={median:.3f}",
            }
    else:
        result = zscore_detector(current, values, threshold=threshold)

    result["method"] = f"auto:{result['method']}" + (":seasonal" if used_segment else "")
    result["reason"] += f"; seasonal_segment={used_segment}, n={values.size}"
    return result


def detect_anomaly(
    current: float,
    history: Iterable[float],
    *,
    method: str = "auto",
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stable lab API.

    - `zscore`: basic z-score.
    - `mad`: MAD example.
    - `auto`: context-aware robust baseline, see `auto_detector`.
    """
    if method == "mad":
        return mad_detector(current, history)
    if method == "zscore":
        return zscore_detector(current, history, threshold=threshold)
    if method == "auto":
        return auto_detector(current, history, threshold=threshold, context=context)
    raise ValueError(f"Unsupported method: {method}")
