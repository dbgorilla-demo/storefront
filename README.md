# Storefront

The order and checkout backend for our e-commerce storefront. A PostgreSQL 16
application handling the product catalog, customer accounts, carts, orders, and
fulfilment, with live browsing/search/checkout traffic and back-office reporting.

Runs on Kubernetes on a CloudNativePG cluster (`storefront-pg`). The database is
monitored by **DBGorilla**.

## Console

A small operations console ships with the service (`app/web/`):

- **Dashboard** — live health (connections, active/blocked queries, longest
  query, dead-tuple %, DB size, order-history index status) and operational
  maintenance controls.
- **Live Queries** — backends currently executing or holding a transaction open,
  from `pg_stat_activity`, with anti-pattern flags.
- **Workload** — storefront queries ranked by total execution time, from
  `pg_stat_statements`, flagged where they exhibit a known anti-pattern.

## Diagnosing performance

The database is connected to **DBGorilla** — use the `dbgorilla` MCP tools first
for slow queries, plans, schema, and what the advisor has already flagged. Start
with the discovery tool to find this database (`Storefront`); most other tools
take the id it returns. See `CLAUDE.md` for the tool map.

## Architecture

Layered ports & adapters. Services own business rules and depend only on ports;
repositories are the only SQL boundary; factories wire them.

```
app/web/          FastAPI + the console UI
app/services/     traffic, health, diagnostics, chaos drills (business rules)
app/ports/        protocols returning storage-agnostic dataclasses
app/repositories/ the only place SQL + a connection pool live
app/factories.py  composition root (only place a DSN is read)
migrations/       Alembic schema + seed
chart/            Helm: CNPG cluster, app, node/postgres exporters, migration Job
```

## Schema

`storefront`: `categories`, `products`, `inventory`, `customers`, `orders`,
`order_items`, `page_views`. Order-history reads use `ix_orders_customer`.

## Deploy

Deploys go through the platform pipeline: merge to `main`, the pipeline runs
`helm upgrade` with `./chart`. Migrations run as a Helm
`post-install`/`post-upgrade` Job (`alembic upgrade head`). Postgres parameters
are code — see `chart/values.yaml → cluster.postgresParameters`.
