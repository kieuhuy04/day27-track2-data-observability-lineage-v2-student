from __future__ import annotations

from typing import Any


def calculate_slo(target: float, bad_events: int, total_events: int) -> dict[str, Any]:
    if not 0 < target < 1:
        raise ValueError("target must be between 0 and 1 (exclusive)")
    if bad_events < 0 or total_events < 0 or bad_events > total_events:
        raise ValueError("invalid event counts")
    allowed_bad_rate = 1.0 - target
    if total_events == 0:
        return {
            "target": target,
            "actual_bad_rate": 0.0,
            "allowed_bad_rate": allowed_bad_rate,
            "burn_rate": 0.0,
            "remaining_error_budget_fraction": 1.0,
            "breached": False,
        }
    actual_bad_rate = bad_events / total_events
    burn_rate = actual_bad_rate / allowed_bad_rate
    consumed_fraction = min(1.0, actual_bad_rate / allowed_bad_rate)
    return {
        "target": target,
        "actual_bad_rate": actual_bad_rate,
        "allowed_bad_rate": allowed_bad_rate,
        "burn_rate": burn_rate,
        "remaining_error_budget_fraction": max(0.0, 1.0 - consumed_fraction),
        "breached": bool(actual_bad_rate > allowed_bad_rate),
    }


_POLICIES = {
    # (fast_threshold, moderate_threshold), following Google's SRE Workbook
    # "Alerting on SLOs" multi-window guidance: 14.4x burn exhausts a 30-day
    # budget in ~2 days, 6x in ~5 days.
    "default": {"fast": 14.4, "moderate": 6.0},
}


def evaluate_multiwindow_burn(
    *,
    short_window_burn: float,
    long_window_burn: float,
    policy: str = "default",
) -> dict[str, Any]:
    """Multi-window burn-rate paging policy.

    Requiring BOTH the short window and the long window to exceed a burn-rate
    threshold is what distinguishes a sustained incident (page) from a
    transient spike that recovers before it meaningfully damages the error
    budget (short window high, long window still fine -> do not page).
    """
    thresholds = _POLICIES.get(policy, _POLICIES["default"])
    fast, moderate = thresholds["fast"], thresholds["moderate"]

    base = {"short_window_burn": short_window_burn, "long_window_burn": long_window_burn, "policy": policy}

    if short_window_burn >= fast and long_window_burn >= fast:
        return {
            **base,
            "page": True,
            "severity": "critical",
            "reason": f"sustained_fast_burn: both windows >= {fast}x",
        }
    if short_window_burn >= moderate and long_window_burn >= moderate:
        return {
            **base,
            "page": True,
            "severity": "warning",
            "reason": f"sustained_moderate_burn: both windows >= {moderate}x",
        }
    if short_window_burn >= fast and long_window_burn < moderate:
        return {
            **base,
            "page": False,
            "severity": "info",
            "reason": "transient_spike: short window is hot but long window has not sustained it, no page",
        }
    return {
        **base,
        "page": False,
        "severity": "info",
        "reason": "within_error_budget",
    }
