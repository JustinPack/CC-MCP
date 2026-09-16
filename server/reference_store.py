"""Read-only access to the indexed Common Cartridge 1.4 reference data."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path


_QUERY_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
SECTION_PAGE_SIZE = 8000


class ReferenceStore:
    """Query the reference index without ever opening it for writes."""

    def __init__(self, database_path: Path):
        self.database_path = Path(database_path).resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{self.database_path.as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = _QUERY_TOKEN_RE.findall(query)
        if not tokens:
            raise ValueError("query must contain at least one searchable word")
        return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)

    def search_spec(self, query: str, scope: str, limit: int) -> list[dict[str, object]]:
        """Return BM25-ranked section matches, optionally restricted by document kind."""
        match_query = self._fts_query(query)
        sql = """
            SELECT
                d.id AS document_id,
                d.url,
                d.kind,
                d.title,
                s.section_id,
                bm25(sections_fts) AS rank,
                snippet(sections_fts, 1, '[', ']', ' ... ', 32) AS excerpt
            FROM sections_fts
            JOIN sections AS s ON s.id = sections_fts.rowid
            JOIN documents AS d ON d.id = s.document_id
            WHERE sections_fts MATCH ?
        """
        parameters: list[object] = [match_query]
        if scope != "all":
            sql += " AND d.kind = ?"
            parameters.append(scope)
        sql += " ORDER BY rank, d.id, s.section_id LIMIT ?"
        parameters.append(limit)

        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [
            {
                **dict(row),
                "section_ref": f'{row["document_id"]}::{row["section_id"]}',
            }
            for row in rows
        ]

    def get_section(self, section_id: str, cursor: str | None) -> dict[str, object]:
        """Return one bounded page from a section selected by reference or bare ID."""
        offset = int(cursor or "0")
        if "::" in section_id:
            document_id, local_section_id = section_id.split("::", 1)
            where_clause = "s.document_id = ? AND s.section_id = ?"
            parameters = (document_id, local_section_id)
        else:
            where_clause = "s.section_id = ?"
            parameters = (section_id,)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    s.document_id,
                    s.section_id,
                    s.text,
                    d.url,
                    d.kind,
                    d.title,
                    count(*) OVER () AS matching_document_count
                FROM sections AS s
                JOIN documents AS d ON d.id = s.document_id
                WHERE {where_clause}
                ORDER BY s.document_id
                LIMIT 1
                """,
                parameters,
            ).fetchall()
        if not rows:
            raise LookupError(f"section not found: {section_id}")

        row = rows[0]
        text = row["text"]
        next_offset = offset + SECTION_PAGE_SIZE
        return {
            "document_id": row["document_id"],
            "section_id": row["section_id"],
            "section_ref": f'{row["document_id"]}::{row["section_id"]}',
            "url": row["url"],
            "kind": row["kind"],
            "title": row["title"],
            "text": text[offset:next_offset],
            "cursor": str(offset),
            "next_cursor": str(next_offset) if next_offset < len(text) else None,
            "total_characters": len(text),
            "matching_document_count": row["matching_document_count"],
        }

    def lookup_definition(self, term: str) -> dict[str, object]:
        """Find exact XSD declarations and ranked prose mentioning a term."""
        match_query = self._fts_query(term)
        with self._connect() as connection:
            component_rows = connection.execute(
                """
                SELECT
                    c.document_id,
                    c.namespace,
                    c.local_name AS name,
                    c.component_type,
                    c.url,
                    c.section_id,
                    s.text AS definition
                FROM schema_components AS c
                JOIN sections AS s
                  ON s.document_id = c.document_id
                 AND s.section_id = c.section_id
                WHERE c.local_name = ? COLLATE NOCASE
                ORDER BY c.namespace, c.document_id, c.component_type
                """,
                (term,),
            ).fetchall()
            section_rows = connection.execute(
                """
                SELECT
                    d.id AS document_id,
                    d.url,
                    d.kind,
                    d.title,
                    s.section_id,
                    bm25(sections_fts) AS rank,
                    snippet(sections_fts, 1, '[', ']', ' ... ', 64) AS definition
                FROM sections_fts
                JOIN sections AS s ON s.id = sections_fts.rowid
                JOIN documents AS d ON d.id = s.document_id
                WHERE sections_fts MATCH ?
                ORDER BY rank, d.id, s.section_id
                LIMIT 10
                """,
                (match_query,),
            ).fetchall()

        components = [
            {
                **dict(row),
                "section_ref": f'{row["document_id"]}::{row["section_id"]}',
            }
            for row in component_rows
        ]
        sections = [
            {
                **dict(row),
                "section_ref": f'{row["document_id"]}::{row["section_id"]}',
            }
            for row in section_rows
        ]
        return {
            "term": term,
            "schema_components": components,
            "sections": sections,
        }

    def get_schema_component(self, namespace: str, name: str) -> dict[str, object]:
        """Return every exact named global XSD component in a namespace."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    c.document_id,
                    c.namespace,
                    c.local_name AS name,
                    c.component_type,
                    c.url,
                    c.section_id,
                    s.text AS definition
                FROM schema_components AS c
                JOIN sections AS s
                  ON s.document_id = c.document_id
                 AND s.section_id = c.section_id
                WHERE c.namespace = ? AND c.local_name = ?
                ORDER BY c.document_id, c.component_type
                """,
                (namespace, name),
            ).fetchall()
        return {
            "namespace": namespace,
            "name": name,
            "components": [
                {
                    **dict(row),
                    "section_ref": f'{row["document_id"]}::{row["section_id"]}',
                }
                for row in rows
            ],
        }

    def list_documents(self, kind: str) -> list[dict[str, object]]:
        """List indexed documents and their indexed-content counts."""
        sql = """
            SELECT
                d.id,
                d.url,
                d.kind,
                d.title,
                d.sha256,
                count(DISTINCT s.id) AS section_count,
                count(DISTINCT c.id) AS schema_component_count
            FROM documents AS d
            LEFT JOIN sections AS s ON s.document_id = d.id
            LEFT JOIN schema_components AS c ON c.document_id = d.id
        """
        parameters: tuple[object, ...] = ()
        if kind != "all":
            sql += " WHERE d.kind = ?"
            parameters = (kind,)
        sql += " GROUP BY d.id ORDER BY d.kind, d.title, d.id"
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]
