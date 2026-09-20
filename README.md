# CC MCP

A Common Cartridge 1.4 specification reference MCP server for building Common Cartridge 1.4 course packages. Give your MCP-compatible assistant searchable specification text, implementation guidance, and XML schema definitions for manifests, metadata, resources, and more.

Read-only, local, and offline after installation. Includes a ready-to-use reference index; no API keys required. This is a reference lookup service, not a package validator or certification tool.

M8ven Badge
[![M8ven Score](https://m8ven.ai/badge/mcp/justinpack-cc-mcp-e3c6nq?v=735272f820383226f2cb5d25d2009fda)](https://m8ven.ai/mcp/justinpack-cc-mcp-e3c6nq)

## Launch locally

Requires Python 3.11+ with SQLite FTS5 support (included in standard Python distributions).

```sh
git clone https://github.com/JustinPack/CC-MCP.git
cd CC-MCP
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server/mcp_server.py
```

On Windows PowerShell, after cloning and entering the folder:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server\mcp_server.py
```

The server uses **stdio**, not HTTP. It waits silently for an MCP client and exits when stdin closes. Normally, your client launches it using a configuration like this:

```json
{
  "mcpServers": {
    "cc-mcp": {
      "command": "/absolute/path/CC-MCP/.venv/bin/python",
      "args": ["/absolute/path/CC-MCP/server/mcp_server.py"]
    }
  }
}
```

Use absolute paths. On Windows, use `C:/path/CC-MCP/.venv/Scripts/python.exe` and `C:/path/CC-MCP/server/mcp_server.py`. Configuration location and format depend on your MCP client.

## Launch with Docker

From the cloned folder:

```sh
docker build -t cc-mcp .
docker run --rm -i --read-only --network none \
  --cap-drop=ALL --security-opt=no-new-privileges cc-mcp
```

For a client-managed container, replace the `cc-mcp` entry above with:

```json
{
  "command": "docker",
  "args": [
    "run", "--rm", "-i", "--read-only", "--network", "none",
    "--cap-drop=ALL", "--security-opt=no-new-privileges", "cc-mcp"
  ]
}
```

Keep `-i`; do not add `-t`. No ports or volumes are needed. The image runs as
an unprivileged user and its application files and reference database are
read-only. This is a stdio MCP server, so deploy it as a client-managed
`docker run` process rather than as a port-based Compose or web service.

## Reference requests

Ask your connected assistant, for example:

- “Use CC MCP to find the manifest metadata requirements for a Common Cartridge 1.4 course package. Cite the reference sections.”
- “Look up the CC1.4 `manifest` schema declaration and explain its required attributes.”

| Tool | Purpose |
| --- | --- |
| `search_spec` | Ranked keyword search; `scope`: `core`, `schema`, `dependency`, or `all`. |
| `get_section` | Read a section using a `section_ref` returned by search. |
| `lookup_definition` | Find schema declarations and relevant prose for a term. |
| `get_schema_component` | Get an XSD declaration by exact namespace and name. |
| `list_documents` | List available documents; `kind` uses the same values as `scope`. |

For direct MCP clients, these are JSON-RPC `tools/call` requests **after the MCP initialization handshake**. Send one JSON object per line over stdio, not as HTTP requests:

```jsonl
{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_spec","arguments":{"query":"manifest","scope":"core","limit":3}}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_section","arguments":{"section_id":"057c26e3f89a0c24f07f2b31b614d1c5bf8369f329129c3d55c6fd73af5fe467::h3-8-2-manifest-level-metadata"}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"lookup_definition","arguments":{"term":"resource"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"get_schema_component","arguments":{"namespace":"http://www.imsglobal.org/xsd/imsccv1p4/imscp_v1p1","name":"manifest"}}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"list_documents","arguments":{"kind":"core"}}}
```

Search returns source URLs, excerpts, and `section_ref` values. The second request reads a section returned by the first. For other lookups, pass the returned `section_ref` as `section_id`. Sections are paged at 8,000 characters; resend the same `section_id` with `"cursor":"<next_cursor>"` until `next_cursor` is `null`. Results include JSON in `structuredContent` and a text equivalent in `content`.

## Development checks

After installing the requirements, run the test suite (no extra test dependencies):

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The suite checks that every advertised tool explicitly serializes all four
boolean annotation hints and exercises all five handlers against temporary
copies of the bundled index. It covers successful results, text/structured JSON
consistency, filters and limits, pagination, invalid arguments, missing results,
data-access errors, repeat calls, and read-only database access. Tests run offline
without API credentials and leave the bundled database untouched.

All current tools are read-only, non-destructive, idempotent, and closed-world
because they only query the bundled local index.

## License

Server code: [MIT](LICENSE). Bundled reference material retains its upstream terms; see [NOTICE.md](NOTICE.md). Independent project, not affiliated with or endorsed by 1EdTech.
