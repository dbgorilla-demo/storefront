"""grant the collector role read access to the storefront schema

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-19

`storefront_collector` is declared in chart/values.yaml and created by CNPG as a
managed role (chart/templates/cluster.yaml) with `login: true` and membership of
`pg_monitor`. The chart comment describes it as "SELECT + pg_monitor, no write" —
but nothing ever granted the SELECT, so the role could log in, read server
statistics, and not read a single row of storefront data. That is why the
DBGorilla registration script connects as `storefront_app` (the database OWNER)
instead: the least-privilege role did not work, so the working one got used.

This migration supplies the missing half, so the collector is what its own
declaration always claimed.

WHAT THIS COSTS WHEN IT IS MISSING
----------------------------------
`pg_monitor` covers `pg_stat_*` and the catalogs, so topology and workload
collection work and the gap stays invisible. EXPLAIN does not: it needs the same
privileges as the statement it explains, so every `run_explain` against a
`storefront.*` query fails with "permission denied for schema storefront" — the
query-plan and optimizer flows die silently while the collector's config still
looks correct. The config flag is the first gate and privileges are the second,
and nothing checks the second until it fails.

WHY THIS IS A MIGRATION AND NOT `postInitApplicationSQL`
--------------------------------------------------------
The chart's `bootstrap.initdb.postInitApplicationSQL` is the obvious-looking home
and it is the wrong one, for two independent reasons:

  1. it runs during initdb, before CNPG reconciles `managed.roles`, so
     `storefront_collector` does not exist yet;
  2. it runs before any migration, so the `storefront` schema does not exist
     either.

Either one alone would fail the bootstrap, and a failed post-init fails the whole
cluster. `GRANT pg_monitor` survives there only because granting a role needs
nothing to exist yet. By this revision both the role and the schema exist, and
the schema is owned by `storefront_app` — which is also the role running the
migration, so it can grant without being a superuser. The Helm Job re-runs on
upgrade, so an already-running cluster is repaired by a deploy rather than by
someone remembering to paste SQL.

`ALTER DEFAULT PRIVILEGES` covers tables added by later revisions, so a future
migration does not silently reintroduce the gap for whatever it creates.

Read-only by construction: USAGE and SELECT only. No INSERT, UPDATE, DELETE,
TRUNCATE or DDL, and nothing on `public`.

NOT YET APPLIED to the `storefront-staging` database on dev3, where the grant
is still absent.

VERIFY AFTER APPLYING — the migration reporting success is not the test:

    psql -U storefront_collector -d storefront \\
         -c "SELECT count(*) FROM storefront.orders"

ORDERING — if the DBGorilla registration is ever switched from `storefront_app`
to `storefront_collector`, this migration must have run against that database
first. Switching before it does leaves DBGorilla connected to a role that can
read nothing, which presents as a broken platform rather than an empty role.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ROLE = "storefront_collector"


def upgrade() -> None:
    # The role is created by CNPG's managed.roles, not by this repo, so on a
    # plain local Postgres it may legitimately be absent — guarded so that is
    # not a migration failure.
    #
    # WARNING rather than NOTICE on purpose: the skip path leaves the collector
    # unable to read anything while the Job still goes green, which is the
    # failure shape worth making loud. Use the verify query in the docstring
    # rather than trusting the exit code.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                GRANT USAGE ON SCHEMA storefront TO {ROLE};
                GRANT SELECT ON ALL TABLES IN SCHEMA storefront TO {ROLE};
                -- Scope the default privileges to the migrating role
                -- explicitly rather than relying on the session default.
                ALTER DEFAULT PRIVILEGES FOR ROLE storefront_app
                    IN SCHEMA storefront GRANT SELECT ON TABLES TO {ROLE};
            ELSE
                RAISE WARNING
                    'role % absent; collector grants SKIPPED and the collector '
                    'cannot read storefront data', '{ROLE}';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                ALTER DEFAULT PRIVILEGES FOR ROLE storefront_app
                    IN SCHEMA storefront REVOKE SELECT ON TABLES FROM {ROLE};
                REVOKE SELECT ON ALL TABLES IN SCHEMA storefront FROM {ROLE};
                REVOKE USAGE ON SCHEMA storefront FROM {ROLE};
            END IF;
        END
        $$;
        """
    )
