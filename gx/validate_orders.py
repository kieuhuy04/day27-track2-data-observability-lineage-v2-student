#!/usr/bin/env python3
"""Great Expectations Core 1.21 flow for orders.

Packages the starter's ad-hoc `batch.validate(expectation)` calls into a
reusable Expectation Suite + Validation Definition + Checkpoint, then derives
a severity-aware pipeline action (block/quarantine/warn) from the result,
mirroring `src.contract_validator.determine_action`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import great_expectations as gx
    from great_expectations.checkpoint import UpdateDataDocsAction
except ImportError as exc:  # friendlier classroom failure
    raise SystemExit("great_expectations is not installed. Run: pip install -r requirements.txt") from exc

from src.contract_validator import SEVERITY_ACTION, SEVERITY_ORDER  # noqa: E402


def build_suite(context: Any) -> Any:
    suite = context.suites.add(gx.ExpectationSuite(name="orders_suite"))
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToNotBeNull(column="order_id", severity="critical")
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeUnique(column="order_id", severity="critical")
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeBetween(column="amount", min_value=0, severity="critical")
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="currency", value_set=["USD", "VND"], severity="critical"
        )
    )
    suite.add_expectation(
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="status",
            value_set=["pending", "completed", "refunded", "cancelled"],
            severity="warning",
        )
    )
    return suite


def expectation_severity(expectation: Any) -> str:
    severity = getattr(expectation, "severity", None)
    if severity is None:
        return "warning"
    return getattr(severity, "value", severity)


def derive_action(checkpoint_result: Any, suite: Any) -> dict[str, Any]:
    """Severity-aware action decision, analogous to contract_validator.determine_action.

    Great Expectations Core does not ship a built-in "fail on critical only"
    action, so this walks the per-expectation results and applies the same
    block/quarantine/warn policy used by the deterministic contract validator.
    """
    severity_by_expectation = {
        exp.id: expectation_severity(exp) for exp in suite.expectations
    }

    failed_severities: list[str] = []
    for validation_result in checkpoint_result.run_results.values():
        for result in validation_result.results:
            if bool(result.success):
                continue
            config = result.expectation_config
            severity = severity_by_expectation.get(getattr(config, "id", None), "warning")
            failed_severities.append(severity)

    if not failed_severities:
        return {"action": "pass", "severity": None, "reason": "all_expectations_passed"}

    worst = max(failed_severities, key=lambda s: SEVERITY_ORDER.get(s, 1))
    return {
        "action": SEVERITY_ACTION.get(worst, "warn"),
        "severity": worst,
        "reason": f"{len(failed_severities)} expectation(s) failed, worst_severity={worst}",
    }


def main() -> None:
    df = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    context = gx.get_context(mode="ephemeral")

    data_source = context.data_sources.add_pandas("orders_pandas")
    asset = data_source.add_dataframe_asset(name="orders_dataframe")
    batch_definition = asset.add_batch_definition_whole_dataframe("whole_orders")

    suite = build_suite(context)

    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(name="orders_validation_definition", data=batch_definition, suite=suite)
    )

    checkpoint = context.checkpoints.add(
        gx.Checkpoint(
            name="orders_checkpoint",
            validation_definitions=[validation_definition],
            actions=[UpdateDataDocsAction(name="update_data_docs")],
        )
    )

    result = checkpoint.run(batch_parameters={"dataframe": df})

    for validation_result in result.run_results.values():
        for r in validation_result.results:
            name = r.expectation_config.type
            severity = expectation_severity(r.expectation_config)
            print(f"{name:<40} severity={severity:<9} success={r.success}")

    decision = derive_action(result, suite)
    print(f"\nGX checkpoint result: {'PASS' if result.success else 'FAIL'}")
    print(f"Derived action: {decision['action']} ({decision['reason']})")


if __name__ == "__main__":
    main()
