#!/usr/bin/env python3
"""MCP stdio server for the indexed Common Cartridge 1.4 reference."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)

if __package__:
    from .reference_store import ReferenceStore
else:
    from reference_store import ReferenceStore


SERVER_DIR = Path(__file__).resolve().parent
DATABASE_PATH = SERVER_DIR / "data" / "cc14_index.db"

SCOPES = ("core", "schema", "dependency", "all")
DOCUMENT_KINDS = ("core", "schema", "dependency", "all")


def _object_schema(
    properties: dict[str, dict[str, Any]],
    required: list[str] | None = None,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


TOOLS = [
    Tool(
        name="search_spec",
        description="Search indexed CC1.4 prose and schemas using ranked FTS5 keyword matching.",
        input_schema=_object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "scope": {"type": "string", "enum": list(SCOPES), "default": "all"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                },
            },
            ["query"],
        ),
    ),
    Tool(
        name="get_section",
        description=(
            "Read one indexed section in 8,000-character pages. Prefer the "
            "document_id::section_id reference returned by search_spec."
        ),
        input_schema=_object_schema(
            {
                "section_id": {"type": "string", "minLength": 1},
                "cursor": {
                    "anyOf": [{"type": "string", "pattern": "^[0-9]+$"}, {"type": "null"}],
                    "default": None,
                },
            },
            ["section_id"],
        ),
    ),
    Tool(
        name="lookup_definition",
        description="Find XSD declarations and ranked definitional text for a term.",
        input_schema=_object_schema(
            {"term": {"type": "string", "minLength": 1}},
            ["term"],
        ),
    ),
    Tool(
        name="get_schema_component",
        description="Return complete indexed XSD declarations for an exact namespace and name.",
        input_schema=_object_schema(
            {
                "namespace": {"type": "string"},
                "name": {"type": "string", "minLength": 1},
            },
            ["namespace", "name"],
        ),
    ),
    Tool(
        name="list_documents",
        description="List indexed reference documents, optionally filtered by kind.",
        input_schema=_object_schema(
            {
                "kind": {
                    "type": "string",
                    "enum": list(DOCUMENT_KINDS),
                    "default": "all",
                }
            }
        ),
    ),
]

_STORE = ReferenceStore(DATABASE_PATH)
_ALLOWED_ARGUMENTS = {
    "search_spec": {"query", "scope", "limit"},
    "get_section": {"section_id", "cursor"},
    "lookup_definition": {"term"},
    "get_schema_component": {"namespace", "name"},
    "list_documents": {"kind"},
}


def _result(
    payload: Any,
    *,
    is_error: bool = False,
    text: str | None = None,
) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=text if text is not None else json.dumps(
                    payload, ensure_ascii=False, indent=2, sort_keys=True
                ),
            )
        ],
        structured_content=payload,
        is_error=is_error,
    )


def _error(code: str, message: str, **details: Any) -> CallToolResult:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return _result({"error": error}, is_error=True)


def _validate_keys(tool_name: str, arguments: dict[str, Any]) -> None:
    extras = sorted(set(arguments) - _ALLOWED_ARGUMENTS[tool_name])
    if extras:
        raise ValueError("unknown parameter(s): " + ", ".join(extras))


def _required_string(
    arguments: dict[str, Any],
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _choice(arguments: dict[str, Any], name: str, choices: tuple[str, ...], default: str) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{name} must be one of: {', '.join(choices)}")
    return value


def _search_limit(arguments: dict[str, Any]) -> int:
    value = arguments.get("limit", 10)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ValueError("limit must be an integer from 1 through 50")
    return value


def _section_cursor(arguments: dict[str, Any]) -> str | None:
    value = arguments.get("cursor")
    if value is None:
        return None
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError("cursor must be a non-negative decimal string")
    return value


async def list_tools(
    _context: object,
    _params: PaginatedRequestParams | None,
) -> ListToolsResult:
    """Advertise the read-only reference tools."""
    return ListToolsResult(tools=TOOLS)


async def call_tool(_context: object, params: CallToolRequestParams) -> CallToolResult:
    """Validate and dispatch one reference-tool call without leaking exceptions."""
    tool_name = params.name
    if tool_name not in _ALLOWED_ARGUMENTS:
        return _error("unknown_tool", f"unknown tool: {tool_name}")

    arguments = params.arguments or {}
    try:
        _validate_keys(tool_name, arguments)
        if tool_name == "search_spec":
            query = _required_string(arguments, "query")
            scope = _choice(arguments, "scope", SCOPES, "all")
            limit = _search_limit(arguments)
            results = _STORE.search_spec(query, scope, limit)
            return _result(
                {
                    "query": query,
                    "scope": scope,
                    "limit": limit,
                    "count": len(results),
                    "results": results,
                }
            )

        if tool_name == "get_section":
            section_id = _required_string(arguments, "section_id")
            cursor = _section_cursor(arguments)
            return _result(_STORE.get_section(section_id, cursor))

        if tool_name == "lookup_definition":
            term = _required_string(arguments, "term")
            return _result(_STORE.lookup_definition(term))

        if tool_name == "get_schema_component":
            namespace = _required_string(arguments, "namespace", allow_empty=True)
            name = _required_string(arguments, "name")
            result = _STORE.get_schema_component(namespace, name)
            if not result["components"]:
                return _error(
                    "not_found",
                    "schema component not found",
                    namespace=namespace,
                    name=name,
                )
            return _result(result)

        if tool_name == "list_documents":
            kind = _choice(arguments, "kind", DOCUMENT_KINDS, "all")
            documents = _STORE.list_documents(kind)
            return _result({"kind": kind, "count": len(documents), "documents": documents})

        return _error("unknown_tool", f"unknown tool: {tool_name}")
    except (OSError, sqlite3.Error, UnicodeError, json.JSONDecodeError):
        return _error(
            "data_access_error",
            "the reference data could not be read",
            tool=tool_name,
        )
    except ValueError as error:
        return _error("invalid_parameters", str(error), tool=tool_name)
    except LookupError as error:
        return _error("not_found", str(error), tool=tool_name)


server = Server(
    "cc-mcp",
    title="CC MCP",
    version="1.0.0",
    description="Common Cartridge 1.4 specification reference for course package development",
    website_url="https://github.com/JustinPack/CC-MCP",
    on_list_tools=list_tools,
    on_call_tool=call_tool,
)


async def main() -> None:
    """Run one MCP connection over stdin/stdout until the client closes stdin."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    anyio.run(main)
