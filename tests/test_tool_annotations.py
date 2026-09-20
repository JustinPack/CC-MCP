"""Regression coverage for the metadata advertised to MCP hosts."""

import unittest

from server.mcp_server import list_tools


class ToolAnnotationTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_advertised_tool_has_explicit_boolean_hints(self):
        result = await list_tools(None, None)
        # Omit unset fields so SDK defaults cannot conceal missing declarations.
        payload = result.model_dump(mode="json", by_alias=True, exclude_unset=True)
        self.assertEqual(
            {tool["name"] for tool in payload["tools"]},
            {
                "search_spec",
                "get_section",
                "lookup_definition",
                "get_schema_component",
                "list_documents",
            },
        )
        expected = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        }
        for tool in payload["tools"]:
            with self.subTest(tool=tool["name"]):
                self.assertIn("annotations", tool)
                for hint, value in expected.items():
                    with self.subTest(hint=hint):
                        self.assertIn(hint, tool["annotations"])
                        actual = tool["annotations"][hint]
                        self.assertIs(type(actual), bool)
                        self.assertIs(actual, value)


if __name__ == "__main__":
    unittest.main()
