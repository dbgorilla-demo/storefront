# DBGorilla #13: customer anti-join

## Decision and scope

`PostgresStorefrontRepository.awaiting_shipment_count(days)` is the matching
customer-count query; `TrafficService` calls it with 90 days. The port accepts
only days, not status. Keep its integer result, bound days, strict `>` cutoff,
and literal `status = 'shipped'`. `NOT EXISTS` counts recent customers with no
shipped order, including customers with no orders at all. Other-status orders
do not exclude customers; duplicate shipped orders exclude a customer once.

Use `ix_customers_created_at` and the smaller partial
`ix_orders_customer_shipped(customer_id) WHERE status = 'shipped'`, not a
composite index. The literal predicate remains provable even when psycopg
switches to a generic prepared plan; only days is parameterized. If this API
later accepts bound statuses, revisit the index choice rather than assuming
a generic plan can use this partial index.

Initial migration and latest inspected topology both make `orders.customer_id`
NOT NULL (and customers' ID is a primary key): results are equivalent under the
current constraints. If orders ever allow NULL customer IDs, `NOT EXISTS`
intentionally ignores those rows, unlike `NOT IN`, which can return zero due to
NULL poisoning. Tests demonstrate that hypothetical difference without changing
the application schema. No status API is added.

Latest topology has identical `ix_orders_customer` / `ix_orders_customer_dup`
and a different partial `ix_orders_status_placed(status, placed_at DESC)` for
paid/packed orders; none supersedes this shipped anti-join index. Leave all
pre-existing indexes and historical migrations untouched.

Investigation `01a10ed9-8bbb-760b-845a-997ca09d1ef3` identifies fingerprint
`58e0b53550e4abec` as ~53.8% of execution time (200,758 calls, 4,279 ms mean).
Its scan-heavy NOT IN plan and growing orders table motivate this change, not
blocking. The reported 22.887 ms to 0.025 ms (~915x) result was synthetic, not a
production guarantee. No database writes or performance experiments were run
from this sandbox.

## Deployment: indexes first

1. Schedule off-peak with disk headroom. Concurrent builds still consume I/O,
   CPU, disk/WAL, can wait on old transactions, and add subsequent write cost.
   Allow enough time for both builds; the existing Helm Job has a 900s deadline.
   Avoid parallel migration runners.
2. Before applying/retrying, inspect both names using an operator database
   connection (read-only checks):

   ```sql
   SELECT n.nspname, x.relname, t.relname AS target_table,
          i.indisvalid, i.indisready, pg_get_indexdef(x.oid) AS definition
   FROM pg_class x
   JOIN pg_namespace n ON n.oid = x.relnamespace
   LEFT JOIN pg_index i ON i.indexrelid = x.oid
   LEFT JOIN pg_class t ON t.oid = i.indrelid
   WHERE n.nspname = 'storefront'
     AND x.relname IN ('ix_customers_created_at', 'ix_orders_customer_shipped');
   ```

   Expect absent names, or valid/ready nonunique btree indexes on exactly
   `storefront.customers(created_at)` and `storefront.orders(customer_id)`
   with predicate `status = 'shipped'`. Record which names already existed.
   A conflicting definition, non-index object, or invalid/not-ready index is a
   **stop**, not a successful no-op. `IF NOT EXISTS` only checks names; it does
   not validate definitions or repair failed builds.
3. Run `alembic upgrade 0009` from this release's image/source in a dedicated
   migration job using the normal `DATABASE_URL` secret, **before rolling out
   the new app image**. `migrations/env.py` already uses
   `transaction_per_migration=True`; revision 0009 uses Alembic
   `autocommit_block()` for each concurrent DDL sequence. Do not wrap this
   command/DDL in another transaction.

   The chart currently uses **post-install/post-upgrade** hooks despite its
   comments saying otherwise, so the normal combined Helm upgrade alone does
   not enforce index-before-app ordering. Run the separate migration job first,
   confirm success/validity, then proceed with the normal platform app release.
   The later hook's `upgrade head` is a no-op for 0009. Do not run concurrent
   runners or assume hook completion means a skipped index is valid.
4. Repeat the catalog check, require both correct/valid/ready indexes and
   `alembic current` = 0009, then roll out the query. Monitor latency, temp
   spills, total execution time, write overhead and resource utilization. The
   query rewrite can acquire a new pg_stat_statements fingerprint; track both.

Concurrent DDL is not atomic: a failure may leave one completed index and an
invalid second index with the revision still at 0008. Stop automatic retries if
such a failure occurs (including Job timeout). Inspect catalog/build activity
first. With operator approval, for an invalid index **owned by this deployment**,
run the appropriate `DROP INDEX CONCURRENTLY IF EXISTS storefront.<new_name>`
as a standalone autocommit statement, then rerun `alembic upgrade 0009` and
recheck. Do not drop an active build, a conflicting/pre-existing index, or either
shared customer index. Resolve name conflicts manually before proceeding;
there is deliberately no destructive automatic repair.

## Verification

With requirements installed, from the repository root:

```sh
python -m unittest discover -s tests -v
# Optional semantics tests: ONLY an empty disposable PostgreSQL database.
TEST_DATABASE_URL='postgresql://...' python -m unittest discover -s tests -v
```

Default tests cover actual emitted SQL, binding/count behavior, and concurrent
migration/rollback statements with mocked Alembic autocommit. Optional tests
exercise the real repository query on transaction-scoped fixtures, strict cutoff
boundaries, multiple days, no orders, shipped/other statuses, duplicate matches,
and NULL rejection plus hypothetical nullable semantics. They refuse an existing
`storefront` schema. Fixtures are rolled back; they are not a live-schema test.
Neither suite could be run here: this sandbox has no test runner/database client.
CI should also apply 0001–0009 on a disposable database and check index definitions
and validity; mocks do not prove PostgreSQL's concurrent DDL behavior.

Optional plan/execution validation on a safe representative clone (ANALYZE
executes the query, so do not run expensive baseline scans on production):

```sql
ANALYZE storefront.customers;
ANALYZE storefront.orders;
SET plan_cache_mode = force_generic_plan;
PREPARE awaiting_shipment(integer) AS
SELECT count(*) FROM storefront.customers c
WHERE c.created_at > now() - make_interval(days => $1)
  AND NOT EXISTS (
    SELECT 1 FROM storefront.orders o
    WHERE o.customer_id = c.customer_id AND o.status = 'shipped'
  );
EXPLAIN (ANALYZE, BUFFERS) EXECUTE awaiting_shipment(90);
EXPLAIN (ANALYZE, BUFFERS) EXECUTE awaiting_shipment(1);
DEALLOCATE awaiting_shipment;
RESET plan_cache_mode;
```

Compare results, execution time, buffers/temp I/O with the old NOT IN shape and
with custom plans on the same data/snapshot. Look for an anti-join and index use;
the planner may rationally choose scans for broad windows/small tables. Do not
force index use or infer a guaranteed production speedup from a clone.

## Rollback

Revert the application query/redeploy the prior app first. After confirming no
other consumers need the new indexes, run `alembic downgrade 0008` (only when
0009 is current, and both names were introduced by this deployment). Its
concurrent drops run outside transactions. Equivalent standalone autocommit DDL:

```sql
DROP INDEX CONCURRENTLY IF EXISTS storefront.ix_orders_customer_shipped;
DROP INDEX CONCURRENTLY IF EXISTS storefront.ix_customers_created_at;
```

Use separate statements, not a transaction or a multi-statement transactional
batch. Manual SQL does not update Alembic's version; reconcile it deliberately
before any future `upgrade head`. If either correct index predated this release,
do not blindly use the downgrade: preserve that index and drop only indexes
recorded as newly introduced, coordinating migration-version reconciliation with
the operator. Never drop `ix_orders_customer`, `ix_orders_customer_dup`, or other
pre-existing shared indexes. The old query restores the expensive plan and, if
constraints have drifted to allow NULL, restores NOT IN's null poisoning too.
