# Incident Report

## Severity
P2 (customer-facing revenue reporting affected; no data loss, recoverable via re-ingestion)

## Summary
`orders.csv` ingestion silently truncated to 25% of expected rows (partial-ingestion
fault, reproduced with `python scripts/inject_fault.py volume_drop`). The pipeline
still reported `SUCCESS` — no contract check failed, because every remaining row was
individually well-formed — but `fct_daily_revenue` and the CEO revenue dashboard
understated the day's revenue by roughly 75%.

## Detection
- Signal: `detect_metric(row_count, ...)` (`observability/anomaly.py`, `auto` mode)
  flags the row-count drop using a same-weekday, MAD-based robust baseline —
  `is_anomaly=True`, `method=auto:mad`, `score≈5.5` (modified z-score, threshold 3.5).
- Deterministic contracts (`orders_contract.yaml`) do **not** catch this: not-null,
  unique, accepted-value, range, type and freshness checks all pass on the truncated
  file because the fault removes rows, it doesn't corrupt them.
- First observed: immediately after ingestion, on the next `make baseline` run
  (metric evaluated per batch, no polling delay in this lab).

## Root Cause
Upstream ingestion job stopped early / only delivered a partial extract
(`scripts/inject_fault.py volume_drop` truncates the file to `max(10, 25%)` of the
original row count, simulating a crashed or rate-limited extract). Row-level data
quality was not affected — this is a **completeness** failure, not a validity one,
which is exactly the class of fault deterministic contracts are blind to and that
anomaly detection exists to cover.

## Evidence
1. `python scripts/inject_fault.py volume_drop` → `orders.csv` drops from 600 to 150
   rows (kept 150/600).
2. `make baseline` output:
   - `contract failed checks: 0`, `critical contract fails: 0` (contracts pass — false
     sense of health).
   - `row-count anomaly: True (auto:mad, score=5.53)` (anomaly layer catches it).
   - `contract action: pass` — confirms the layered design: contracts alone would
     have shipped this incident as `SUCCESS`.
3. Blast radius (`observability/lineage.py`, `get_downstream_assets(graph, "stg_orders")`):
   `stg_orders -> fct_daily_revenue -> ceo_revenue_dashboard`. Every downstream
   consumer of `stg_orders` is affected; `dbt_project` staging/marts and the CEO
   dashboard all read the same truncated batch.
4. Cross-check against the RAG/support-agent branch: `python scripts/inject_fault.py
   stale_kb` (separate scenario) similarly passes KB field-level contract checks
   (`kb_contract.yaml`) but is now caught by the wired-in `freshness` check in
   `scripts/run_baseline.py` (`kb_stale=True`, `kb_freshness_slo.breached=True`) —
   confirming staleness is a distinct failure mode from row-level validity, and one
   the starter left unimplemented on purpose.
5. Known false positive (documented, not silently suppressed): on a **healthy**
   reset baseline the same detector also fires (`row_count_anomaly=True`,
   `score≈18.75`) when the lab happens to run on a Saturday/Sunday. Cause: the
   static sample `data/baseline/orders.csv` always ships ~600 rows regardless of
   weekday, while `data/history/metrics_history.csv` encodes real weekend
   seasonality (~235–270 rows on Sat/Sun vs ~565–650 on weekdays). The seasonal
   MAD detector is working as designed — it is the synthetic fixture that doesn't
   vary by weekday. On a weekday run this false positive does not occur.

## Blast Radius

```text
stg_orders
-> fct_daily_revenue
-> ceo_revenue_dashboard
```

## Mitigation
- Contract layer: `src/contract_validator.determine_action` already derives
  `block`/`quarantine`/`warn` from severity and `scripts/run_baseline.py` copies the
  batch to `data/quarantine/<timestamp>_orders.csv` whenever the action is
  `block`/`quarantine` (verified against the `duplicate_pk` scenario, which *does*
  fail a critical contract check). Completeness faults like `volume_drop` are not
  contract failures, so they are not auto-quarantined today — recommended follow-up
  is to add a row-count/completeness rule to the contract layer (e.g. minimum
  expected row count vs. same-weekday baseline) so this class of incident also
  triggers automatic quarantine, not just an anomaly alert.
- Immediate action for this incident: halt promotion of `fct_daily_revenue` /
  dashboard refresh for the affected batch, re-run ingestion for the missing 75% of
  rows, re-run `make baseline` to confirm `row_count_anomaly=False` before
  re-enabling downstream refresh.

## Recovery
1. Re-ingest the missing rows (or full re-extract) into `data/incoming/orders.csv`.
2. `make baseline` → confirm row count returns to the same-weekday expected range.
3. `make dbt` → rebuild `fct_daily_revenue`; confirm `assert_nonnegative_revenue` and
   `unique_fct_daily_revenue_order_date` still pass and daily revenue matches the
   pre-incident magnitude.
4. Re-enable the CEO dashboard refresh.

## Verification
- [x] Contract healthy (`contract failed checks: 0`, `contract action: pass`)
- [x] dbt tests healthy (`dbt build`: 19/19 PASS — seeds, staging tests, marts tests,
      unit tests)
- [x] anomaly returned to expected range (row-count anomaly `False` after reset to
      baseline on a weekday; documented weekend exception above)
- [x] SLO healthy / budget understood (`contract_slo.breached=False`,
      `kb_freshness_slo.breached=False` on healthy baseline)
- [x] downstream output verified (`fct_daily_revenue` row/revenue totals match
      pre-incident magnitude after `dbt build` on the restored dataset)

## Prevention / Action Items
| Action | Owner | Deadline | Why |
|---|---|---|---|
| Add a completeness/row-count rule to `orders_contract.yaml` + `contract_validator.py` so `volume_drop`-class faults trigger automatic quarantine, not just an anomaly alert | Contract & Validation owner | Next lab iteration | Contracts currently only check row-level validity; completeness gaps ship as `SUCCESS` |
| Regenerate `data/baseline/orders.csv` with weekday-aware row counts (or make `reset_lab.py` scale volume by day-of-week) | Anomaly & SLO owner | Next lab iteration | Removes the documented weekend false positive without weakening the seasonal detector |
| Wire `multiwindow_burn` into `run_baseline.py`/dashboard using short/long window burn rates computed from `metrics_history.csv`, so transient spikes vs. sustained incidents are distinguished in the UI, not just in `observability/slo.py` | Anomaly & SLO owner | Next lab iteration | `evaluate_multiwindow_burn` is implemented and unit-tested but not yet surfaced end-to-end |
| Extend `column_downstream` usage into the dashboard/report to show *which* revenue column is impacted (`stg_orders.amount_usd -> fct_daily_revenue.daily_revenue -> ceo_revenue_dashboard.revenue`), not just dataset-level blast radius | Lineage & Điều tra owner | Next lab iteration | Column lineage graph already exists in `lineage_graph.json`, but only dataset-level BFS is surfaced today |
