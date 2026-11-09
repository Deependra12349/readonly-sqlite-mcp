# Contributing

Thanks for helping improve Read-only SQLite MCP!

## Ground rules (not negotiable)

1. **Zero dependencies** — Python standard library only.
2. **Read-only by default** — no change may weaken the safety model:
   allow-listed databases, SQLite `mode=ro` connections, statement
   filtering in `read_query`, capped results.
3. **Writes stay opt-in** — write capability only behind `--allow-write`.

## Adding a tool

1. Implement it in `readonly_sqlite_mcp/server.py` and register it in
   the `TOOLS` dict with a description and JSON schema.
2. Add a check to `tests/test_mcp.py`.
3. Run the full test before opening a PR:

   ```bash
   python tests/test_mcp.py
   ```

4. Add a row to the README tools table.

## Reporting bugs

Open an issue with the tool name, the SQL/input used, what you expected
and what you got. Security issues in the safety model are especially
welcome — please describe the bypass precisely.
