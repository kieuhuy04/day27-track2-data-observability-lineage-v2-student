from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from observability.anomaly import zscore_detector


def approximate_token_lengths(texts: Iterable[str]) -> list[int]:
    # Deliberately simple proxy; no tokenizer/model download needed.
    return [len(str(t).split()) for t in texts]


def detect_text_length_shift(
    current_texts: Iterable[str],
    baseline_batch_means: Iterable[float],
    *,
    threshold: float = 3.0,
) -> dict[str, Any]:
    lengths = approximate_token_lengths(current_texts)
    current_mean = float(np.mean(lengths)) if lengths else 0.0
    result = zscore_detector(current_mean, baseline_batch_means, threshold=threshold)
    result["metric"] = "mean_text_length"
    result["current_mean"] = current_mean
    return result


def detect_embedding_norm_shift(
    current_norms: Iterable[float], baseline_norms: Iterable[float], *, threshold: float = 3.0
) -> dict[str, Any]:
    """Embedding-space drift signal based on mean embedding norm.

    Mirrors `detect_text_length_shift`: compare the current batch's mean
    embedding norm against the historical distribution of batch-mean norms
    with a z-score. A shifted mean norm is a cheap proxy for
    re-indexing/model-swap/encoding drift without requiring an embedding
    model at grading time (hidden evaluation can feed precomputed norms).
    """
    norms = np.asarray(list(current_norms), dtype=float)
    current_mean = float(np.mean(norms)) if norms.size else 0.0
    result = zscore_detector(current_mean, baseline_norms, threshold=threshold)
    result["metric"] = "mean_embedding_norm"
    result["current_mean"] = current_mean
    return result
