-- If the customer dimension has more than one active row per customer, a
-- naive join on customer_id fans out and inflates revenue without a SQL
-- error (see unit test `duplicate_active_customer_rows_do_not_inflate_revenue`
-- in unit_tests.yml). active_customers is deduplicated to at most one row per
-- customer_id (most recent valid_from wins) so the join stays 1:1.

with completed_orders as (
    select *
    from {{ ref('stg_orders') }}
    where status = 'completed'
),
active_customers_ranked as (
    select
        *,
        row_number() over (partition by customer_id order by valid_from desc) as rn
    from {{ ref('stg_customers') }}
    where is_active = true
),
active_customers as (
    select *
    from active_customers_ranked
    where rn = 1
)
select
    o.order_date,
    count(*) as completed_order_rows,
    sum(o.amount_usd) as daily_revenue
from completed_orders o
left join active_customers c
    on o.customer_id = c.customer_id
group by 1
order by 1
