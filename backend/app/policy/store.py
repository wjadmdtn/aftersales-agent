"""T007/T008: 条款存储。

版本与品类过滤必须在召回前完成（WHERE 条件），不能在应用层召回后再过滤——
否则 top-K 里过滤掉未来版本可能只剩 0~1 条，人为制造"检索不到"。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Protocol, runtime_checkable

from app.policy.embedder import Embedder, cosine
from app.policy.models import PolicyClause, RetrievedClause

logger = logging.getLogger(__name__)


@runtime_checkable
class ClauseStore(Protocol):
    def search(
        self,
        *,
        query_vector: list[float],
        categories: list[str] | None,
        product_type: str | None,
        order_date: date,
        limit: int,
    ) -> list[RetrievedClause]: ...


def _visible(clause: PolicyClause, *, product_type: str | None, order_date: date) -> bool:
    if not clause.effective_at(order_date):
        return False
    types = clause.product_types
    if "*" in types:
        return True
    # 品类缺失时只放行通配条款：不用品类不明的条款强行回答
    return product_type is not None and product_type in types


class InMemoryClauseStore:
    """CI / 单测用。过滤语义必须与 PgClauseStore 的 SQL 条件一致。"""

    def __init__(self, clauses: list[PolicyClause], embedder: Embedder):
        self.clauses = clauses
        self.embedder = embedder
        self._vectors = embedder.embed([c.embed_text for c in clauses])

    def search(
        self,
        *,
        query_vector: list[float],
        categories: list[str] | None,
        product_type: str | None,
        order_date: date,
        limit: int,
    ) -> list[RetrievedClause]:
        scored: list[RetrievedClause] = []
        for clause, vec in zip(self.clauses, self._vectors):
            if categories and clause.category not in categories:
                continue
            if not _visible(clause, product_type=product_type, order_date=order_date):
                continue
            scored.append(RetrievedClause(clause=clause, similarity=cosine(query_vector, vec)))
        scored.sort(key=lambda r: r.similarity, reverse=True)
        return scored[:limit]


class PgClauseStore:
    """PostgreSQL + pgvector。硬过滤与向量检索在同一条 SQL 内完成。"""

    DDL = """
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS policy_clause (
        clause_id       TEXT NOT NULL,
        policy_id       TEXT NOT NULL,
        version         INTEGER NOT NULL,
        effective_from  DATE NOT NULL,
        effective_to    DATE,
        category        TEXT NOT NULL,
        title           TEXT NOT NULL,
        body            TEXT NOT NULL,
        exceptions      JSONB NOT NULL DEFAULT '[]',
        product_types   TEXT[] NOT NULL DEFAULT ARRAY['*'],
        embedding       vector(%(dim)s) NOT NULL,
        PRIMARY KEY (clause_id, version)
    );
    CREATE INDEX IF NOT EXISTS ix_policy_clause_validity
        ON policy_clause (category, effective_from, effective_to);
    CREATE INDEX IF NOT EXISTS ix_policy_clause_types
        ON policy_clause USING GIN (product_types);
    """

    SEARCH_SQL = """
    SELECT clause_id, policy_id, version, effective_from, effective_to,
           category, title, body, exceptions, product_types,
           1 - (embedding <=> %(q)s::vector) AS similarity
    FROM policy_clause
    WHERE (%(categories)s::text[] IS NULL OR category = ANY(%(categories)s::text[]))
      AND effective_from <= %(d)s
      AND (effective_to IS NULL OR effective_to >= %(d)s)
      AND (product_types && ARRAY['*']::text[]
           OR (%(pt)s::text IS NOT NULL AND product_types && ARRAY[%(pt)s]::text[]))
    ORDER BY embedding <=> %(q)s::vector
    LIMIT %(limit)s
    """

    def __init__(self, dsn: str, dim: int):
        import psycopg

        self._pg = psycopg
        self.dsn = dsn
        self.dim = dim

    def _conn(self):
        return self._pg.connect(self.dsn)

    def ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(self.DDL % {"dim": self.dim})
            conn.commit()

    def upsert(self, clauses: list[PolicyClause], embedder: Embedder) -> None:
        vectors = embedder.embed([c.embed_text for c in clauses])
        with self._conn() as conn:
            with conn.cursor() as cur:
                for clause, vec in zip(clauses, vectors):
                    cur.execute(
                        """
                        INSERT INTO policy_clause (clause_id, policy_id, version,
                            effective_from, effective_to, category, title, body,
                            exceptions, product_types, embedding)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::vector)
                        ON CONFLICT (clause_id, version) DO UPDATE SET
                            effective_from=EXCLUDED.effective_from,
                            effective_to=EXCLUDED.effective_to,
                            category=EXCLUDED.category,
                            title=EXCLUDED.title,
                            body=EXCLUDED.body,
                            exceptions=EXCLUDED.exceptions,
                            product_types=EXCLUDED.product_types,
                            embedding=EXCLUDED.embedding
                        """,
                        (
                            clause.clause_id,
                            clause.policy_id,
                            clause.version,
                            clause.effective_from,
                            clause.effective_to,
                            clause.category,
                            clause.title,
                            clause.body,
                            json_dumps(clause.exceptions),
                            clause.product_types,
                            _to_pg_vector(vec),
                        ),
                    )
            conn.commit()

    def search(
        self,
        *,
        query_vector: list[float],
        categories: list[str] | None,
        product_type: str | None,
        order_date: date,
        limit: int,
    ) -> list[RetrievedClause]:
        params = {
            "q": _to_pg_vector(query_vector),
            "categories": categories,
            "pt": product_type,
            "d": order_date,
            "limit": limit,
        }
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(self.SEARCH_SQL, params)
                rows = cur.fetchall()

        out: list[RetrievedClause] = []
        for r in rows:
            clause = PolicyClause(
                clause_id=r[0],
                policy_id=r[1],
                version=r[2],
                effective_from=r[3],
                effective_to=r[4],
                category=r[5],
                title=r[6],
                body=r[7],
                exceptions=r[8] if isinstance(r[8], list) else [],
                applies_to={"product_types": list(r[9] or ["*"])},
            )
            out.append(RetrievedClause(clause=clause, similarity=float(r[10])))
        return out


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def _to_pg_vector(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
