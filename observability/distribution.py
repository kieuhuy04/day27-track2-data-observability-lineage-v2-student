from __future__ import annotations

from typing import Any, Iterable

import numpy as np


def _population_stability_index(current: np.ndarray, baseline: np.ndarray, buckets: int = 10) -> float:
    """PSI between two samples using baseline-derived quantile buckets.

    Unlike a mean ratio, PSI is sensitive to shape/quantile drift (e.g. a
    distribution that widens or shifts its tail) even when the mean barely
    moves. PSI >= ~0.25 is the conventional "major shift" threshold.
    """
    if baseline.size < 2 or current.size == 0:
        return 0.0
    edges = np.unique(np.quantile(baseline, np.linspace(0, 1, buckets + 1)))
    if edges.size < 3:
        return 0.0  # not enough distinct baseline values to form buckets
    base_counts, _ = np.histogram(baseline, bins=edges)
    cur_counts, _ = np.histogram(current, bins=edges)
    base_pct = np.clip(base_counts / max(int(base_counts.sum()), 1), 1e-6, None)
    cur_pct = np.clip(cur_counts / max(int(cur_counts.sum()), 1), 1e-6, None)
    return float(np.sum((cur_pct - base_pct) * np.log(cur_pct / base_pct)))


def detect_distribution_shift(
    current_values: Iterable[float],
    baseline_values: Iterable[float],
    *,
    ratio_threshold: float = 3.0,
    psi_threshold: float = 0.25,
) -> dict[str, Any]:
    """Mean-ratio detector combined with a PSI (quantile-bucket) drift check.

    The mean ratio alone misses shape drift (e.g. more spread, a new tail)
    when the mean is roughly stable; PSI alone can miss a blunt magnitude
    shift on small samples. Flag an anomaly if either signal fires.
    """
    cur = np.asarray(list(current_values), dtype=float)
    base = np.asarray(list(baseline_values), dtype=float)
    if cur.size == 0 or base.size == 0:
        return {"is_anomaly": False, "score": 0.0, "method": "mean_ratio+psi", "reason": "empty_input"}

    cur_mean = float(np.mean(cur))
    base_mean = float(np.mean(base))
    if base_mean == 0:
        ratio_score = float("inf") if cur_mean != 0 else 1.0
    else:
        ratio_score = max(abs(cur_mean / base_mean), abs(base_mean / cur_mean)) if cur_mean != 0 else float("inf")

    psi = _population_stability_index(cur, base)
    is_anomaly = bool(ratio_score >= ratio_threshold or psi >= psi_threshold)
    bounded_ratio = ratio_score if np.isfinite(ratio_score) else 1e9

    return {
        "is_anomaly": is_anomaly,
        "score": float(max(bounded_ratio, psi)),
        "method": "mean_ratio+psi",
        "reason": (
            f"baseline_mean={base_mean:.3f}, current_mean={cur_mean:.3f}, "
            f"mean_ratio_score={ratio_score:.3f}, psi={psi:.3f}, psi_threshold={psi_threshold}"
        ),
        "mean_ratio_score": ratio_score if np.isfinite(ratio_score) else None,
        "psi": psi,
    }
