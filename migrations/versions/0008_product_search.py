"""full-text product search

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-27

Search moves from pattern matching on the name to a text-search document
maintained by the database: a generated tsvector over name and description,
indexed with GIN, plus a function the search endpoint calls so the ranking
lives in one place.
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE storefront.products ADD COLUMN search_document tsvector "
        "GENERATED ALWAYS AS ("
        "  setweight(to_tsvector('english', coalesce(name, '')), 'A') || "
        "  setweight(to_tsvector('english', coalesce(description, '')), 'B')"
        ") STORED"
    )
    op.execute("CREATE INDEX ix_products_search ON storefront.products USING gin (search_document)")
    op.execute(
        """
        CREATE FUNCTION storefront.fn_search_products(p_query text, p_limit integer DEFAULT 20)
        RETURNS TABLE (product_id integer, sku text, name text, price_cents integer, rank real)
        LANGUAGE sql STABLE AS $$
            SELECT p.product_id, p.sku, p.name, p.price_cents,
                   ts_rank(p.search_document, websearch_to_tsquery('english', p_query)) AS rank
              FROM storefront.products p
             WHERE p.search_document @@ websearch_to_tsquery('english', p_query)
             ORDER BY rank DESC, p.product_id
             LIMIT p_limit
        $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS storefront.fn_search_products(text, integer)")
    op.execute("DROP INDEX IF EXISTS storefront.ix_products_search")
    op.execute("ALTER TABLE storefront.products DROP COLUMN IF EXISTS search_document")
