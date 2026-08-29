"""Contract validator.

Covers deterministic checks (not-null/unique/accepted/range), declared-type
validation, contract-level freshness, and severity-aware action derivation
(block/quarantine/warn).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

# critical -> block the pipeline, warning -> quarantine the batch for review,
# info -> warn only. Anything unrecognized is treated as a warning.
SEVERITY_ACTION = {"critical": "block", "warning": "quarantine", "info": "warn"}
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}
_SEVERITY_ORDER = SEVERITY_ORDER  # backward-compat alias for internal use


def _issue(
    check: str,
    *,
    column: str | None,
    severity: str,
    passed: bool,
    details: str,
) -> dict[str, Any]:
    return {
        "check": check,
        "column": column,
        "severity": severity,
        "passed": bool(passed),
        "details": details,
    }


def load_contract(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _value_matches_type(value: Any, declared_type: str) -> bool:
    """Best-effort check that a scalar respects a contract-declared type.

    Deliberately stricter than `pd.to_numeric(..., errors="coerce")`, which
    silently turns malformed strings into NaN and hides type drift.
    """
    if declared_type == "integer":
        try:
            return float(value).is_integer()
        except (TypeError, ValueError):
            return False
    if declared_type == "number":
        try:
            float(value)
            return True
        except (TypeError, ValueError):
            return False
    if declared_type == "boolean":
        if isinstance(value, bool):
            return True
        return str(value).strip().lower() in {"true", "false", "0", "1"}
    if declared_type == "datetime":
        return pd.notna(pd.to_datetime(value, errors="coerce", utc=True))
    if declared_type == "string":
        # A CSV column that is *entirely* numeric gets coerced to an int/float
        # dtype by pandas, which is exactly the kind of drift a contract
        # should catch (e.g. customer_id silently becoming numeric).
        return isinstance(value, str)
    return True


def _type_issue(column: str, series: pd.Series, declared_type: str, severity: str) -> dict[str, Any]:
    checked = series.dropna()
    invalid_mask = ~checked.apply(lambda v: _value_matches_type(v, declared_type))
    invalid_count = int(invalid_mask.sum())
    return _issue(
        "type",
        column=column,
        severity=severity,
        passed=(invalid_count == 0),
        details=f"invalid_count={invalid_count}; expected_type={declared_type}",
    )


def _freshness_issue(
    df: pd.DataFrame, freshness: dict[str, Any], reference_time: datetime | None
) -> dict[str, Any]:
    column = freshness.get("column")
    max_delay = freshness.get("max_delay_minutes")
    severity = freshness.get("severity", "warning")

    if column not in df.columns:
        return _issue(
            "freshness",
            column=column,
            severity=severity,
            passed=False,
            details=f"Missing freshness column: {column}",
        )

    parsed = pd.to_datetime(df[column], utc=True, errors="coerce")
    if parsed.notna().sum() == 0:
        return _issue(
            "freshness",
            column=column,
            severity=severity,
            passed=False,
            details="no_valid_timestamps",
        )

    reference = pd.Timestamp(reference_time or datetime.now(timezone.utc))
    if reference.tzinfo is None:
        reference = reference.tz_localize("UTC")
    delay_minutes = (reference - parsed.max()).total_seconds() / 60.0
    passed = delay_minutes <= max_delay
    return _issue(
        "freshness",
        column=column,
        severity=severity,
        passed=passed,
        details=f"delay_minutes={delay_minutes:.1f}, max_allowed_minutes={max_delay}",
    )


def validate_dataframe(
    df: pd.DataFrame,
    contract: dict[str, Any],
    *,
    reference_time: datetime | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    columns = contract.get("columns", {})

    for column, rules in columns.items():
        severity = rules.get("severity", "warning")
        required = bool(rules.get("required", False))

        if column not in df.columns:
            if required:
                issues.append(
                    _issue(
                        "required_column",
                        column=column,
                        severity=severity,
                        passed=False,
                        details=f"Missing required column: {column}",
                    )
                )
            continue

        series = df[column]

        if required:
            null_count = int(series.isna().sum())
            issues.append(
                _issue(
                    "not_null",
                    column=column,
                    severity=severity,
                    passed=(null_count == 0),
                    details=f"null_count={null_count}",
                )
            )

        if rules.get("unique"):
            duplicate_count = int(series.duplicated(keep=False).sum())
            issues.append(
                _issue(
                    "unique",
                    column=column,
                    severity=severity,
                    passed=(duplicate_count == 0),
                    details=f"duplicate_rows={duplicate_count}",
                )
            )

        accepted = rules.get("accepted_values")
        if accepted is not None:
            invalid_mask = series.notna() & ~series.isin(accepted)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "accepted_values",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}; accepted={accepted}",
                )
            )

        # Starter numeric range support. Type validation is intentionally minimal.
        if "min" in rules or "max" in rules:
            numeric = pd.to_numeric(series, errors="coerce")
            invalid = pd.Series(False, index=series.index)
            if "min" in rules:
                invalid |= numeric < rules["min"]
            if "max" in rules:
                invalid |= numeric > rules["max"]
            invalid_count = int(invalid.fillna(False).sum())
            issues.append(
                _issue(
                    "range",
                    column=column,
                    severity=severity,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}",
                )
            )

        declared_type = rules.get("type")
        if declared_type:
            issues.append(_type_issue(column, series, declared_type, severity))

    freshness = contract.get("freshness")
    if freshness:
        issues.append(_freshness_issue(df, freshness, reference_time))

    return issues


def failed_issues(issues: list[dict[str, Any]], min_severity: str | None = None) -> list[dict[str, Any]]:
    failed = [i for i in issues if not i.get("passed", False)]
    if min_severity is None:
        return failed
    threshold = _SEVERITY_ORDER[min_severity]
    return [i for i in failed if _SEVERITY_ORDER.get(i.get("severity", "warning"), 1) >= threshold]


def action_for_severity(severity: str) -> str:
    """Map a single severity level to a pipeline action."""
    return SEVERITY_ACTION.get(severity, "warn")


def determine_action(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate validation issues into a single pipeline action decision.

    Picks the highest-severity failed issue and derives block/quarantine/warn
    from it; returns "pass" when nothing failed.
    """
    failed = failed_issues(issues)
    if not failed:
        return {"action": "pass", "severity": None, "reason": "all_checks_passed"}

    worst = max(failed, key=lambda i: _SEVERITY_ORDER.get(i.get("severity", "warning"), 1))
    severity = worst.get("severity", "warning")
    return {
        "action": action_for_severity(severity),
        "severity": severity,
        "reason": f"{worst['check']} failed on column={worst.get('column')}: {worst.get('details')}",
        "failed_check_count": len(failed),
    }
