"""Exercise the real handlers and SQL against an isolated copy of the index."""

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from mcp.types import CallToolRequestParams

from server import mcp_server
from server.reference_store import ReferenceStore, SECTION_PAGE_SIZE


NAMESPACE = "http://www.imsglobal.org/xsd/imsccv1p4/imscp_v1p1"


class ToolHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "index.db"
        shutil.copyfile(mcp_server.DATABASE_PATH, self.database)
        self.store = ReferenceStore(self.database)
        self.store_patch = patch.object(mcp_server, "_STORE", self.store)
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.original_digest = hashlib.sha256(self.database.read_bytes()).digest()

    def tearDown(self):
        # Every successful and unsuccessful handler call must leave the index intact.
        self.assertEqual(
            hashlib.sha256(self.database.read_bytes()).digest(), self.original_digest
        )
        self.assertEqual(list(self.database.parent.iterdir()), [self.database])

    async def invoke(self, name, arguments=None, *, error=None):
        result = await mcp_server.call_tool(
            None, CallToolRequestParams(name=name, arguments=arguments)
        )
        self.assertIs(result.is_error, error is not None)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0].type, "text")
        payload = result.structured_content
        self.assertIsInstance(payload, dict)
        self.assertEqual(json.loads(result.content[0].text), payload)
        if error is not None:
            self.assertEqual(payload["error"]["code"], error)
            self.assertTrue(payload["error"]["message"])
        return payload

    async def test_search_defaults_and_ranked_results(self):
        payload = await self.invoke("search_spec", {"query": "manifest"})
        self.assertEqual(payload["query"], "manifest")
        self.assertEqual(payload["scope"], "all")
        self.assertEqual(payload["limit"], 10)
        self.assertEqual(payload["count"], len(payload["results"]))
        self.assertGreater(payload["count"], 0)
        self.assertLessEqual(payload["count"], 10)
        results = payload["results"]
        self.assertEqual(
            results,
            sorted(results, key=lambda row: (row["rank"], row["document_id"], row["section_id"])),
        )
        for row in results:
            self.assertTrue(row["excerpt"])
            self.assertEqual(row["section_ref"], f'{row["document_id"]}::{row["section_id"]}')

    async def test_search_filters_and_limits(self):
        for scope, query in (("core", "manifest"), ("schema", "manifest"), ("dependency", "string")):
            for limit in (1, 3, 50):
                with self.subTest(scope=scope, limit=limit):
                    payload = await self.invoke(
                        "search_spec", {"query": query, "scope": scope, "limit": limit}
                    )
                    self.assertEqual(payload["scope"], scope)
                    self.assertEqual(payload["limit"], limit)
                    self.assertEqual(payload["count"], len(payload["results"]))
                    self.assertGreater(payload["count"], 0)
                    self.assertLessEqual(payload["count"], limit)
                    self.assertTrue(all(row["kind"] == scope for row in payload["results"]))

    async def test_get_section_from_search_reference(self):
        search = await self.invoke("search_spec", {"query": "manifest", "limit": 1})
        hit = search["results"][0]
        page = await self.invoke("get_section", {"section_id": hit["section_ref"]})
        self.assertEqual(page["section_ref"], hit["section_ref"])
        self.assertEqual(page["url"], hit["url"])
        self.assertEqual(page["cursor"], "0")
        self.assertEqual(page["matching_document_count"], 1)
        self.assertTrue(page["text"])
        self.assertLessEqual(len(page["text"]), SECTION_PAGE_SIZE)
        bare = await self.invoke("get_section", {"section_id": hit["section_id"]})
        self.assertEqual(bare["section_id"], hit["section_id"])
        self.assertGreaterEqual(bare["matching_document_count"], 1)

    async def test_section_pagination_reconstructs_original_text(self):
        connection = self.store._connect()
        try:
            row = connection.execute(
                "SELECT document_id, section_id, text FROM sections "
                "ORDER BY length(text) DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        text = row["text"]
        self.assertGreater(len(text), SECTION_PAGE_SIZE)
        reference = f'{row["document_id"]}::{row["section_id"]}'
        pages = []
        for offset in range(0, len(text), SECTION_PAGE_SIZE):
            page = await self.invoke(
                "get_section", {"section_id": reference, "cursor": str(offset)}
            )
            self.assertEqual(page["cursor"], str(offset))
            self.assertEqual(page["total_characters"], len(text))
            self.assertEqual(page["text"], text[offset:offset + SECTION_PAGE_SIZE])
            next_offset = offset + SECTION_PAGE_SIZE
            self.assertEqual(
                page["next_cursor"], str(next_offset) if next_offset < len(text) else None
            )
            pages.append(page["text"])
        self.assertEqual("".join(pages), text)
        beyond = await self.invoke(
            "get_section", {"section_id": reference, "cursor": str(len(text))}
        )
        self.assertEqual(beyond["text"], "")
        self.assertIsNone(beyond["next_cursor"])

    async def test_lookup_definition(self):
        payload = await self.invoke("lookup_definition", {"term": "manifest"})
        self.assertEqual(payload["term"], "manifest")
        self.assertTrue(payload["schema_components"])
        self.assertTrue(payload["sections"])
        self.assertLessEqual(len(payload["sections"]), 10)
        for component in payload["schema_components"]:
            self.assertEqual(component["name"].lower(), "manifest")
            self.assertTrue(component["definition"])
            self.assertIn("::", component["section_ref"])
        upper = await self.invoke("lookup_definition", {"term": "MANIFEST"})
        self.assertEqual(upper["schema_components"], payload["schema_components"])

    async def test_get_schema_component(self):
        payload = await self.invoke(
            "get_schema_component", {"namespace": NAMESPACE, "name": "manifest"}
        )
        self.assertEqual(payload["namespace"], NAMESPACE)
        self.assertEqual(payload["name"], "manifest")
        self.assertTrue(payload["components"])
        for component in payload["components"]:
            self.assertEqual(component["namespace"], NAMESPACE)
            self.assertEqual(component["name"], "manifest")
            self.assertIn("manifest", component["definition"])
            section = await self.invoke("get_section", {"section_id": component["section_ref"]})
            self.assertEqual(section["text"], component["definition"][:SECTION_PAGE_SIZE])

    async def test_list_documents_defaults_and_filters(self):
        payload = await self.invoke("list_documents")
        self.assertEqual(payload["kind"], "all")
        self.assertEqual(payload["count"], len(payload["documents"]))
        self.assertGreater(payload["count"], 0)
        for document in payload["documents"]:
            self.assertTrue(document["title"])
            self.assertTrue(document["url"])
            self.assertGreater(document["section_count"], 0)
            self.assertGreaterEqual(document["schema_component_count"], 0)
        for kind in ("core", "schema", "dependency"):
            with self.subTest(kind=kind):
                filtered = await self.invoke("list_documents", {"kind": kind})
                expected = [row for row in payload["documents"] if row["kind"] == kind]
                self.assertEqual(filtered["documents"], expected)
                self.assertEqual(filtered["count"], len(expected))
                self.assertEqual(filtered["kind"], kind)
                self.assertTrue(expected)

    async def test_every_declared_tool_is_repeatable(self):
        search = await self.invoke("search_spec", {"query": "manifest", "limit": 1})
        calls = {
            "search_spec": {"query": "manifest"},
            "get_section": {"section_id": search["results"][0]["section_ref"]},
            "lookup_definition": {"term": "manifest"},
            "get_schema_component": {"namespace": NAMESPACE, "name": "manifest"},
            "list_documents": {},
        }
        advertised = await mcp_server.list_tools(None, None)
        self.assertEqual(set(calls), {tool.name for tool in advertised.tools})
        for name, arguments in calls.items():
            with self.subTest(tool=name):
                first = await self.invoke(name, arguments)
                self.assertEqual(await self.invoke(name, arguments), first)

    async def test_invalid_arguments(self):
        cases = [
            ("search_spec", {}),
            ("search_spec", {"query": "   "}),
            ("search_spec", {"query": 42}),
            ("search_spec", {"query": "!!!"}),
            ("search_spec", {"query": "manifest", "scope": "invalid"}),
            ("get_section", {}),
            ("get_section", {"section_id": ""}),
            ("lookup_definition", {}),
            ("lookup_definition", {"term": " "}),
            ("lookup_definition", {"term": "!!!"}),
            ("get_schema_component", {"name": "manifest"}),
            ("get_schema_component", {"namespace": NAMESPACE}),
            ("get_schema_component", {"namespace": NAMESPACE, "name": ""}),
            ("get_schema_component", {"namespace": None, "name": "manifest"}),
            ("list_documents", {"kind": "invalid"}),
            ("list_documents", {"kind": 1}),
        ]
        cases.extend(
            ("search_spec", {"query": "manifest", "limit": limit})
            for limit in (0, 51, -1, True, 1.5, "10", None)
        )
        cases.extend(
            ("get_section", {"section_id": "anything", "cursor": cursor})
            for cursor in ("-1", "1.5", "", " 0", "١", 0, True)
        )
        cases.extend((tool.name, {"unexpected": "value"}) for tool in mcp_server.TOOLS)
        for name, arguments in cases:
            with self.subTest(tool=name, arguments=arguments):
                payload = await self.invoke(name, arguments, error="invalid_parameters")
                self.assertEqual(payload["error"]["details"]["tool"], name)

    async def test_unknown_tool(self):
        await self.invoke("not_a_tool", error="unknown_tool")

    async def test_missing_sections_and_exact_components(self):
        await self.invoke("get_section", {"section_id": "missing::section"}, error="not_found")
        for namespace, name in (("urn:missing", "manifest"), (NAMESPACE, "MANIFEST"), ("", "missing")):
            with self.subTest(namespace=namespace, name=name):
                payload = await self.invoke(
                    "get_schema_component", {"namespace": namespace, "name": name}, error="not_found"
                )
                self.assertEqual(payload["error"]["details"], {"namespace": namespace, "name": name})

    async def test_empty_search_and_definition_are_not_errors(self):
        term = "ccmcpnonexistentterm987654321"
        search = await self.invoke("search_spec", {"query": term})
        self.assertEqual(search["results"], [])
        self.assertEqual(search["count"], 0)
        definition = await self.invoke("lookup_definition", {"term": term})
        self.assertEqual(definition["schema_components"], [])
        self.assertEqual(definition["sections"], [])

    async def test_unavailable_database_returns_safe_error(self):
        missing = self.database.parent / "missing.db"
        with patch.object(mcp_server, "_STORE", ReferenceStore(missing)):
            for name, arguments in (
                ("search_spec", {"query": "manifest"}),
                ("get_section", {"section_id": "any"}),
                ("lookup_definition", {"term": "manifest"}),
                ("get_schema_component", {"namespace": NAMESPACE, "name": "manifest"}),
                ("list_documents", {}),
            ):
                with self.subTest(tool=name):
                    payload = await self.invoke(name, arguments, error="data_access_error")
                    self.assertEqual(payload["error"]["details"], {"tool": name})
                    self.assertNotIn(str(missing), json.dumps(payload))
        self.assertFalse(missing.exists())

    async def test_handlers_close_database_connections(self):
        connect = self.store._connect
        connections = []

        def tracked_connect():
            connection = connect()
            connections.append(connection)
            return connection

        try:
            with patch.object(self.store, "_connect", side_effect=tracked_connect):
                search = await self.invoke("search_spec", {"query": "manifest", "limit": 1})
                await self.invoke("get_section", {"section_id": search["results"][0]["section_ref"]})
                await self.invoke("lookup_definition", {"term": "manifest"})
                await self.invoke("get_schema_component", {"namespace": NAMESPACE, "name": "manifest"})
                await self.invoke("list_documents")
            self.assertEqual(len(connections), 5)
            for index, connection in enumerate(connections):
                with self.subTest(connection=index):
                    with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                        connection.execute("SELECT 1")
        finally:
            for connection in connections:
                connection.close()

    def test_database_connection_rejects_writes(self):
        connection = self.store._connect()
        try:
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                connection.execute("CREATE TABLE test_write_probe (id INTEGER)")
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
