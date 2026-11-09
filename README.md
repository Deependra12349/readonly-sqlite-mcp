# Read-only SQLite MCP

**Let your AI assistant explore your SQLite databases — without ever
being able to change them.**

Anthropic's original SQLite reference server was moved to the archive.
This is a maintained replacement with a safety-first design:

- 🔒 **Read-only by default** — connections use SQLite's own read-only
  mode, and queries are restricted to `SELECT / WITH / PRAGMA / EXPLAIN`.
  Writes need an explicit `--allow-write` flag at startup.
- 📁 **Only your files** — the server can only open databases you name
  with `--db`. The AI cannot wander your disk.
- ⚡ **Zero dependencies** — pure Python standard library. No Docker,
  no Node, works offline.

Works with **Claude Code, Cursor, VS Code, Windsurf** and any MCP client.

## Install

```bash
pip install readonly-sqlite-mcp
```

## Connect it

Point the server at one or more database files:

**Claude Code**

```bash
claude mcp add sqlite -- readonly-sqlite-mcp --db /path/to/app.db --db /path/to/data.sqlite
```

**Cursor / VS Code / Windsurf** (`mcp.json`):

```json
{
  "mcpServers": {
    "sqlite": {
      "command": "readonly-sqlite-mcp",
      "args": ["--db", "/path/to/app.db"]
    }
  }
}
```

Then ask your assistant:

- *"What tables are in this database and how big is each one?"*
- *"Show me the schema of the users table"*
- *"Which products sold most last month? Give me CSV"*

## Tools

| Tool | What it does |
|---|---|
| `list_databases` | Databases this server may access |
| `list_tables` | Tables and views in a database |
| `describe_table` | Columns, types, keys, foreign keys, indexes |
| `table_stats` | Row count for every table |
| `read_query` | Read-only SQL (SELECT/WITH/PRAGMA/EXPLAIN), capped results |
| `sample_rows` | A few example rows from a table |
| `export_csv` | Run a read-only query, get CSV back |
| `write_query` | INSERT/UPDATE/DELETE/DDL — **only with `--allow-write`** |

## Safety model

1. Databases are allow-listed at startup (`--db`, repeatable, or the
   `SQLITE_MCP_DBS` environment variable).
2. Read connections are opened with SQLite URI `mode=ro` — the database
   engine itself refuses writes, not just this server's code.
3. `read_query` additionally rejects any statement that does not start
   with SELECT, WITH, PRAGMA or EXPLAIN.
4. Results are capped (default 200 rows, max 1000) so a runaway query
   can't flood the conversation.

## Development

```bash
git clone https://github.com/Deependra12349/readonly-sqlite-mcp
cd readonly-sqlite-mcp
python tests/test_mcp.py   # creates a temp DB, runs real protocol checks,
                           # including "writes are blocked" and "file unchanged"
```

## Contributing

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
The zero-dependency and read-only-by-default rules are not negotiable.

## License

MIT — see [LICENSE](LICENSE).

*Maintained by Deependra, with AI assistance for implementation.
Safety behaviour is covered by the automated tests in `tests/`.*
