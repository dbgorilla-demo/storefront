# CLAUDE.md — Storefront

Guidance for Claude working in the Storefront repository.

## What this is

**Storefront** is our e-commerce order and checkout backend — a live PostgreSQL 16
application serving the product catalog, customer accounts, carts, orders, and
fulfilment. It runs on Kubernetes on a CloudNativePG-managed Postgres cluster
(`storefront-pg`), and it carries real, continuous traffic: browsing, search,
checkout, and back-office reporting run against it around the clock.

It is a **production service**. Treat it as one. Changes to the schema, the
queries, or the database configuration affect a running system with live order
history (tens of thousands of orders, thousands of customers). Think before you
touch it, and prefer read-only investigation first.

## The database is monitored by DBGorilla — use it

Storefront's database is connected to **DBGorilla**, our Postgres monitoring and
optimization platform, and the **DBGorilla MCP server is your primary tool for
understanding what the database is doing.** When you are asked about performance,
slow queries, locks, bloat, plan regressions, topology, or "why is the app slow",
**reach for the `dbgorilla` MCP tools first** — they are connected to this exact
database and carry live metrics, workload history, and the advisor's findings.

**Start with `discover_components`.** Nearly every other tool takes a
`component_id`, and that is where you get it — the Postgres component whose
scopes include the `storefront` database. Do not assume an id, and do not ask
for one; look it up. `discover_components` is cheap, and one call at the start
saves guessing later. `get_topology_overview` on that component gives you the
`member_id` (the server) and the database scope name the other tools ask for.

Where to go next, by question:

| You want | Tool |
|---|---|
| What is registered here, its servers and databases | `discover_components`, `get_topology_overview` |
| The heaviest / slowest queries | `discover_workloads` (the ranking dimensions), then `get_top_workload` |
| One query in depth: text, stats, trend | `investigate_workload` (by fingerprint), `search_workloads` (by text) |
| The plan for a specific query, on the live database | `run_explain` |
| To *measure* a change (an index, a rewrite) without touching production | `run_experiment` — an ephemeral clone with production statistics and/or synthetic rows |
| What is running or blocked right now | `discover_activity`, then `get_activity_breakdown`, `get_blocking_sessions`, `get_activity_samples` |
| Host and Postgres metrics over time | `discover_metrics`, `get_metric_timeseries`, `get_correlated_metrics` |
| Tables, indexes, functions, and how they changed | `search_topology_objects`, `inspect_topology_object`, `get_topology_object_history` |
| What has already been flagged | `list_open_issues`, `get_issue_overview`, `get_issue_action_items`, `search_issues` |
| DBGorilla's own guidance | `search_best_practices`, `search_documentation` |

**Reading a plan vs proving a change.** `run_explain` is plan-only against the
live database: cheap, safe, and the right first move. It never executes the
statement. `run_experiment` is where a hypothesis gets a number: it clones the
database from a topology snapshot, restores production statistics for the
tables you name (`planner_replay`), or populates synthetic rows and runs the
query for real (`synthetic_execution`), and runs the Python you give it with a
`run_sql()` helper. Use it to compare the old shape and the new shape — plan
and rows — before you claim a rewrite is faster. A faster plan that returns
different rows is a bug, not an optimization.

Do not go straight to raw `psql` for diagnosis when a DBGorilla MCP tool answers
the question — the point of this stack is that DBGorilla already has the signal.

## Architecture (ports & adapters)

Layered, hexagonal. Services own workflow + business rules and depend only on
**ports** (protocols). **Repositories** are the only place SQL and a connection
pool live. **Factories** wire them. Callers (the web layer) use services.

```
app/web/main.py        FastAPI inbound adapter — parses, calls services, maps JSON
app/services/          business rules; depend only on ports
    traffic_service        the live workload: realistic session mix
    health_service         raw snapshot -> operational status
    diagnostics_service    live sessions + workload, with anti-pattern tagging
    chaos_service          game-day fault drills and recovery
app/ports/             protocols; return storage-agnostic frozen dataclasses
app/repositories/      the ONLY SQL boundary (storefront, health, diagnostics, faults)
app/factories.py       composition root; the only place a DSN is read
migrations/            Alembic schema + seed
chart/                 Helm: CNPG cluster, app, exporters, migration Job
```

When you add a feature, follow the existing shape: define/extend a port that
returns frozen dataclasses, implement the repository, put business rules in a
service, wire it in a factory. Never let SQL, a pool, or a credential leak into a
service or the web layer.

## The schema (`storefront`)

`categories`, `products`, `inventory`, `customers`, `orders`, `order_items`,
`page_views`. Order-history lookups ride `ix_orders_customer`; `page_views` is
the high-write table. Migrations are in `migrations/versions/`.

Some business logic still lives in the database as plpgsql functions —
`fn_customer_lifetime_value`, `fn_apply_loyalty_points`, `fn_find_products`.
They predate the service layer and the application still calls them.

The installed source is what actually runs; `migrations/versions/` holds what
was last deployed. `search_topology_objects` / `inspect_topology_object` show
what is installed; check the migration only when you need to know whether the
two have drifted.

Treat them as code, not as configuration: they are callers' business rules, so
read one before you assume what it does, and never duplicate its logic in a
service without saying so. `search_workloads` on the function name tells you
whether anything still calls it — worth knowing before you spend a day
rewriting dead code.

## Tuning the database (Postgres parameters as code)

The server's `postgresql.conf` parameters live in **`chart/values.yaml` under
`cluster.postgresParameters`** — one editable line each (plus
`cluster.sharedPreloadLibraries` for preloaded extensions). This is the single
source of truth; `chart/templates/cluster.yaml` renders them into the CNPG
`Cluster` CR. Do not set parameters with `ALTER SYSTEM` — CNPG owns the config
and reconciles it back, so out-of-band changes get reverted.

To change a parameter (e.g. apply a DBGorilla tuning recommendation), edit the
value in `chart/values.yaml → cluster.postgresParameters` and open a PR; the
deploy pipeline runs `helm upgrade` and CNPG reconciles the change (SIGHUP
reload for most parameters, a controlled rolling restart for `shared_buffers` /
`max_connections`). A parameter change is a one-line, reviewable diff — the
intended way to act on the platform's parameter-tuning advice.

## This workstation

This is a development sandbox: it has the repository, Python, and the
`dbgorilla` CLI, but **no cluster credentials** — `kubectl` and `helm` are not
wired up here, and there is no `psql`. Deploys go through the platform pipeline
via PR. Investigate through the DBGorilla MCP tools rather than trying to reach
the cluster.

To validate a hypothesis: `run_explain` for the live plan, `run_experiment`
for a measured comparison on a throwaway clone — an index, a schema edit, a
rewrite, all without touching production.

Never propose a rewrite you have not run. "This should be faster" is a guess;
two plans and two `run_experiment` results is an answer.

## Legacy naming

Storefront began life as an internal project called `dbg-demo`, and some
artifacts of that name survive: connection `application_name` tags
(`dbg-demo-storefront`, `dbg-demo-chaos-*`, `dbg-demo-alert-*`), the
registration nickname in DBGorilla, and bulk-seeded catalog descriptions from
the original data import. They are expected, load-bearing (the console and the
maintenance sweeps match on the tags), and not significant. Do not rename them.

## Conventions

- Migrations are forward-only and idempotent-safe; add a new revision, don't
  edit applied ones.
- Match the surrounding code style. Keep the layering intact.
- Propose schema/parameter changes as reviewable diffs; never apply them
  out-of-band.
