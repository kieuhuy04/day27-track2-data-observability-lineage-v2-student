
# AI Agent Decision Log

## Decision 1

- Hypothesis: Contract validator thiếu type/freshness/severity-action nên không bắt được type drift hoặc staleness dù rule đã khai báo trong `orders_contract.yaml`.
- Prompt / request to agent: Thêm type validation, freshness validation (dùng `contract["freshness"]`), và severity→action (block/quarantine/warn) vào `src/contract_validator.py`, giữ nguyên `validate_dataframe(df, contract)` signature/return shape.
- Agent proposal: Thêm `_value_matches_type` (kiểm tra scalar theo declared type, coi cột string toàn số là type drift), `_freshness_issue` (so `max(column)` với wall-clock `now`, có `reference_time` optional param), và `determine_action(issues)` chọn issue fail có severity cao nhất rồi map qua `SEVERITY_ACTION`.
- Evidence/test: `pytest tests_public/test_contracts.py` pass; chạy `python scripts/inject_fault.py duplicate_pk` rồi `make baseline` → `contract action: block`, batch được copy vào `data/quarantine/`.
- Accept / reject / revise: Accept, có revise một chỗ.
- Why: Freshness dùng wall-clock `now()` ban đầu làm fail test `test_healthy_contract_passes_starter_checks` vì fixture cũ hardcode ngày `2026-08-28` cố định (luôn "cũ" hơn 30 phút so với lúc test chạy thật). Revise: đổi fixture sang `datetime.now(timezone.utc) - timedelta(minutes=...)` để test không phụ thuộc ngày chạy, thay vì làm yếu freshness check.

## Decision 2

- Hypothesis: `fct_daily_revenue.sql` join với `active_customers` có nguy cơ fan-out (nhân đôi revenue) nếu một `customer_id` có nhiều hơn 1 dòng `is_active=true` trong dimension.
- Prompt / request to agent: Viết dbt unit test nhỏ nhất expose lỗi fan-out này trước, KHÔNG sửa model ngay (theo gợi ý trong `docs/AI_AGENT_GUIDE.md`).
- Agent proposal: Thêm unit test `duplicate_active_customer_rows_do_not_inflate_revenue` (2 dòng active cùng customer_id, kỳ vọng revenue KHÔNG nhân đôi).
- Evidence/test: Test fail trên model gốc (dùng `select * from stg_customers where is_active = true` không dedupe) → xác nhận lỗi có thật.
- Accept / reject / revise: Accept root cause, sau đó sửa model: thêm `row_number() over (partition by customer_id order by valid_from desc)` để dedupe `active_customers` trước khi join.
- Why: Đây đúng là silent failure loại "pipeline SUCCESS nhưng số sai" mà lab nhắm tới — không có SQL error, chỉ có unit test mới lộ ra. `dbt build` sau khi sửa: 19/19 PASS (bao gồm cả 3 unit test).

## Decision 3

- Hypothesis: `detect_anomaly(..., method="auto")` bản gốc bỏ qua `context` hoàn toàn (naive z-score), nên không phân biệt được "Saturday dip hợp lệ" với "volume drop thật".
- Prompt / request to agent: Implement MAD-based detector cho `auto` mode, ưu tiên `context["same_segment_history"]` khi có (seasonality), giữ nguyên `zscore`/`mad` mode cũ. Xử lý edge case MAD=0.
- Evidence/test: `pytest tests_public/test_anomaly.py` pass (method="zscore" không đổi hành vi). Chạy `inject_fault.py volume_drop` → `row-count anomaly: True (auto:mad, score=5.53)`; baseline khỏe trên weekday → `False`.
- Accept / reject / revise: Accept, nhưng phát hiện và ghi nhận false positive: baseline khỏe chạy vào cuối tuần vẫn bị flag `True` (score cao, ~18.75) vì `data/baseline/orders.csv` không đổi row count theo ngày trong tuần trong khi `metrics_history.csv` có seasonality thật. Không sửa detector để che giấu vấn đề — ghi rõ vào `reports/incident_report.md` như một known limitation của synthetic data, kèm action item đề xuất sửa `reset_lab.py`.
- Why: Detector seasonality-aware đang làm đúng việc của nó (phát hiện lệch so với baseline cùng ngày trong tuần); vấn đề nằm ở dữ liệu mẫu tĩnh, không phải logic — làm detector "kém nhạy" hơn để né false positive này sẽ che giấu volume drop thật vào cuối tuần.

## Decision 4

- Hypothesis: `evaluate_multiwindow_burn` bản gốc luôn trả `page: False` (chưa implement), không phân biệt được sustained fast burn với transient spike.
- Prompt / request to agent: Implement policy kiểu Google SRE Workbook — chỉ page khi CẢ short-window VÀ long-window đều vượt ngưỡng burn rate; spike ngắn (short cao, long chưa kịp cao) thì không page.
- Agent proposal: Ngưỡng `fast=14.4`, `moderate=6.0` (theo SRE Workbook, ứng với ~2%/5% budget cháy nhanh); trả thêm `severity` (critical/warning/info) và `reason` giải thích quyết định.
- Evidence/test: `pytest tests_public/test_slo.py` pass (không đổi `calculate_slo`). Test thủ công: `short=20, long=20` → `page=True, severity=critical`; `short=20, long=2` → `page=False, reason=transient_spike`.
- Accept / reject / revise: Accept.
- Why: Đáp ứng đúng yêu cầu Phase 5 "Strong challenge" của `docs/LAB_GUIDE.md` (transient spike ngắn -> không page, sustained fast burn -> page) mà không cần state/thời gian thực (chỉ nhận 2 burn-rate số đã tính sẵn, giữ nguyên stable interface `multiwindow_burn(short_window_burn, long_window_burn)`).

## Decision 5

- Hypothesis: `stale_kb` là 1 trong 3 public fault scenario nhưng `scripts/run_baseline.py` không có bất kỳ signal nào phát hiện nó (LAB_GUIDE gọi đây là "TODO có chủ đích").
- Prompt / request to agent: Wire `kb_contract.yaml` (đã có sẵn nhưng chưa dùng ở đâu) vào `run_baseline.py` bằng cách tái sử dụng `validate_dataframe` (đổi `contract["fields"]` thành `{"columns": ..., "freshness": ...}`) + `calculate_slo` cho `rag_index_freshness` (target 0.99 theo `lab_config.yaml`).
- Evidence/test: Baseline khỏe → `KB stale: False, breached=False`. Sau `inject_fault.py stale_kb` (lùi `published_at` 3 giờ) → `KB stale: True, breached=True`.
- Accept / reject / revise: Accept.
- Why: Không tái sử dụng được `validate_orders`/`student_api` trực tiếp vì `kb_contract.yaml` dùng key `fields` thay vì `columns`; viết adapter ngắn thay vì đổi schema `validate_dataframe` (tránh phá stable interface `docs/STUDENT_API.md`).

## Decision 6

- Hypothesis: 2 bonus item đã implement (`get_column_downstream`, `evaluate_multiwindow_burn`) chỉ có unit test/manual test rời rạc, chưa chạy end-to-end trên dữ liệu thật trong `scripts/run_baseline.py`/dashboard — không đủ evidence để tính bonus "column lineage +7" và "multi-window burn-rate +7" theo tiêu chí `docs/SCORING.md` ("Bonus chỉ tính nếu có evidence kỹ thuật cho thấy giải pháp bắt được failure mà baseline không bắt được").
- Prompt / request to agent: Wire 2 hàm này vào `run_baseline.py` bằng dữ liệu thật, không tạo API/tool mới, không đổi stable interface.
- Agent proposal:
  - Column lineage: đọc thêm key `column_lineage` (đã có sẵn trong `data/baseline/lineage_graph.json`, chưa ai dùng) và gọi `get_column_downstream(column_lineage, "stg_orders.amount_usd")` song song với `get_downstream_assets` dataset-level; in ra console + thêm field `sample_column_blast_radius_from_stg_orders_amount_usd` vào `reports/latest_metrics.json`; hiển thị trong `dashboard/app.py`.
  - Multi-window burn: thêm `_historical_row_count_anomaly_flags()` — replay `detect_anomaly(method="auto")` qua từng ngày trong `metrics_history.csv` (không lookahead: chỉ dùng dữ liệu trước ngày đó) để biến chuỗi metric liên tục thành chuỗi "bad check" nhị phân; nối thêm kết quả anomaly của batch hiện tại (real-time) làm điểm gần nhất; cắt cửa sổ short=7 ngày/long=30 ngày (config hoá trong `lab_config.yaml` mục `slo.row_count_reliability`, target=0.95); tính `calculate_slo` cho mỗi cửa sổ rồi đưa `burn_rate` vào `evaluate_multiwindow_burn`.
- Evidence/test:
  - `pytest tests_public -q`: vẫn 10/10 PASS (không đổi stable API).
  - `make baseline` in ra `column blast radius: fct_daily_revenue.daily_revenue, ceo_revenue_dashboard.revenue` và `row-count burn (7d/30d): short=Xx long=Yx -> page=...`.
  - Test tổng hợp riêng (synthetic history, không phụ thuộc ngày chạy thật): 1 ngày bad đơn lẻ trong 30 ngày ổn định -> `short_burn=2.86x, long_burn=0.77x, page=False, reason=within_error_budget` (transient, đúng như Phase 5 yêu cầu); 10 ngày cuối bị volume-drop liên tục -> `short_burn=11.4x, long_burn=6.15x, page=True, reason=sustained_moderate_burn` (sustained, có page).
  - Dashboard: chạy `streamlit run dashboard/app.py --server.headless true`, `curl /_stcore/health` trả 200, không có traceback.
- Accept / reject / revise: Accept, có 1 revise quan trọng — bản đầu chỉ tính burn rate từ `metrics_history.csv` (lịch sử tĩnh, không đổi theo batch hiện tại), khiến `inject_fault.py volume_drop` không làm short-window burn nhúc nhích gì (sai với kỳ vọng "incident hôm nay phải thấy trong short window"). Sửa: nối thêm anomaly flag của batch hiện tại vào cuối chuỗi trước khi cắt cửa sổ.
- Why: Giữ đúng nguyên tắc SRE Workbook multi-window mà `evaluate_multiwindow_burn` đã implement từ Decision 4 — cần một chuỗi check theo thời gian thật (không phải 1 sample) thì logic short-vs-long mới có ý nghĩa. Known limitation kế thừa từ Decision 3 (weekday false positive trên `data/baseline/orders.csv` cuối tuần) vẫn còn: chạy đúng ngày Thứ Bảy khiến batch hiện tại luôn bị flag anomalous kể cả baseline khỏe, nên trên máy chấm nếu chạy cuối tuần, `row-count burn` output sẽ không phân biệt rõ 2 kịch bản healthy vs volume_drop — đã verify riêng bằng synthetic data ở trên để tách bạch đúng-sai của logic khỏi vấn đề dữ liệu mẫu này.
