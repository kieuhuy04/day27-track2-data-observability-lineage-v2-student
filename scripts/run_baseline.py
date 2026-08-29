#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability.anomaly import detect_anomaly
from observability.lineage import get_downstream_assets
from observability.rag_metrics import detect_text_length_shift
from observability.slo import calculate_slo
from src.contract_validator import determine_action, failed_issues, load_contract, validate_dataframe
from src.io_utils import load_jsonl

QUARANTINE_DIR = ROOT / "data" / "quarantine"


def main() -> None:
    orders = pd.read_csv(ROOT / "data" / "incoming" / "orders.csv")
    history = pd.read_csv(ROOT / "data" / "history" / "metrics_history.csv")
    contract = load_contract(ROOT / "contracts" / "orders_contract.yaml")
    issues = validate_dataframe(orders, contract)
    failed = failed_issues(issues)
    critical_failed = failed_issues(issues, min_severity="critical")
    action = determine_action(issues)

    quarantined_path = None
    if action["action"] in {"block", "quarantine"}:
        QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        quarantined_path = QUARANTINE_DIR / f"{stamp}_orders.csv"
        orders.to_csv(quarantined_path, index=False)

    # Public example: segment by weekday before applying the simple detector.
    # Hidden evaluation still challenges students to make detect_metric(..., context=...)
    # context-aware instead of relying on caller-side preprocessing.
    current_dow = datetime.now().weekday()
    segment = history.loc[history["day_of_week"] == current_dow, "row_count"].tail(8).tolist()
    row_history = segment if len(segment) >= 3 else history["row_count"].tail(14).tolist()
    row_result = detect_anomaly(
        len(orders),
        row_history,
        method="auto",
        context={"metric_name": "row_count", "day_of_week": current_dow},
    )

    updated = pd.to_datetime(orders["updated_at"], utc=True, errors="coerce")
    freshness_minutes = (
        pd.Timestamp(datetime.now(timezone.utc)) - updated.max()
    ).total_seconds() / 60.0

    docs = load_jsonl(ROOT / "data" / "incoming" / "kb_documents.jsonl")
    text_result = detect_text_length_shift(
        [d["content"] for d in docs], history["mean_text_length"].tail(14).tolist()
    )

    # KB freshness/SLO: the starter left this unwired (see LAB_GUIDE Phase 6,
    # "stale_kb" scenario), so a stale knowledge base silently reported healthy.
    # Reuse the generic contract validator against kb_contract.yaml's "fields"
    # (same rule vocabulary as orders_contract.yaml's "columns").
    kb_contract = load_contract(ROOT / "contracts" / "kb_contract.yaml")
    kb_issues = validate_dataframe(
        pd.DataFrame(docs),
        {"columns": kb_contract.get("fields", {}), "freshness": kb_contract.get("freshness")},
    )
    kb_failed = failed_issues(kb_issues)
    kb_stale = any(i["check"] == "freshness" and not i["passed"] for i in kb_issues)
    kb_slo = calculate_slo(0.99, bad_events=1 if kb_stale else 0, total_events=1)

    # Demo SLO: one check event for this run.
    bad = 1 if critical_failed else 0
    contract_slo = calculate_slo(0.999, bad_events=bad, total_events=1)

    with open(ROOT / "data" / "baseline" / "lineage_graph.json", "r", encoding="utf-8") as f:
        lineage = json.load(f)["dataset_lineage"]
    blast_radius = get_downstream_assets(lineage, "stg_orders")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "orders_rows": int(len(orders)),
        "failed_contract_checks": len(failed),
        "critical_contract_failures": len(critical_failed),
        "row_count_anomaly": row_result,
        "freshness_minutes": freshness_minutes,
        "kb_text_length_signal": text_result,
        "kb_failed_contract_checks": len(kb_failed),
        "kb_stale": kb_stale,
        "kb_freshness_slo": kb_slo,
        "contract_slo": contract_slo,
        "sample_blast_radius_from_stg_orders": blast_radius,
        "contract_action": action,
        "quarantined_to": str(quarantined_path.relative_to(ROOT)) if quarantined_path else None,
    }
    out = ROOT / "reports" / "latest_metrics.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print("=== DATA RELIABILITY BASELINE ===")
    print(f"orders rows              : {len(orders)}")
    print(f"contract failed checks   : {len(failed)}")
    print(f"critical contract fails  : {len(critical_failed)}")
    print(f"row-count anomaly        : {row_result['is_anomaly']} ({row_result['method']}, score={row_result['score']:.2f})")
    print(f"freshness minutes        : {freshness_minutes:.1f}")
    print(f"KB length anomaly        : {text_result['is_anomaly']}")
    print(f"KB stale (freshness)     : {kb_stale} (failed_checks={len(kb_failed)}, breached={kb_slo['breached']})")
    print(f"sample blast radius      : {', '.join(blast_radius)}")
    print(f"contract action          : {action['action']} ({action['reason']})")
    if quarantined_path:
        print(f"quarantined orders copy  : {quarantined_path.relative_to(ROOT)}")
    print(f"report                    : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
