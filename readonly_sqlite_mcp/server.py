"""Read-only SQLite MCP server.

A zero-dependency MCP (Model Context Protocol) server that lets AI
assistants explore and query local SQLite databases SAFELY:

- Read-only by default: connections are opened with SQLite's own
  read-only URI mode, and read_query only accepts SELECT / WITH /
  PRAGMA / EXPLAIN statements.
- Writing is possible only when the user explicitly starts the server
  with --allow-write.
- Only database files named at startup (--db) can be touched. The AI
  cannot open arbitrary files on your disk.

Runs over stdio using newline-delimited JSON-RPC 2.0 (MCP 2024-11-05).
Pure Python standard library. Python 3.10+.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys

SERVER_NAME = "readonly-sqlite-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

MAX_LIMIT = 1000
DEFAULT_LIMIT = 200

# Configured at startup by main()
DATABASES: dict[str, str] = {}  # alias -> absolute path
ALLOW_WRITE = False


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------

def _resolve_db(name: str) -> str:
    """Map a tool's database argument (alias or full path) to a real path."""
    if name in DATABASES:
        return DATABASES[name]
    ap = os.path.abspath(name)
    for path in DATABASES.values():
        if os.path.abspath(path) == ap:
            return path
    known = ", ".join(sorted(DATABASES)) or "(none configured)"
    raise ValueError(f"unknown database {name!r}. Configured databases: {known}")


def _connect(path: str, write: bool = False) -> sqlite3.Connection:
    if write:
        return sqlite3.connect(path)
    uri = "file:" + path.replace("?", "%3f").replace("#", "%23") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _check_table_name(table: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table or ""):
        raise ValueError(f"invalid table name: {table!r}")
    return table


def _rows_to_text(cursor: sqlite3.Cursor, limit: int) -> str:
    cols = [d[0] for d in cursor.description] if cursor.description else []
    rows = cursor.fetchmany(limit + 1)
    truncated = len(rows) > limit
    rows = rows[:limit]
    out = []
    if cols:
        out.append(" | ".join(cols))
        out.append("-+-".join("-" * len(c) for c in cols))
    for row in rows:
        out.append(" | ".join("NULL" if v is None else str(v) for v in row))
    out.append(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''}"
               + (f", truncated at limit {limit}" if truncated else "") + ")")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def _list_databases(_args: dict) -> str:
    if not DATABASES:
        return "No databases configured. Start the server with --db PATH."
    lines = []
    for alias, path in sorted(DATABASES.items()):
        size = os.path.getsize(path) if os.path.exists(path) else 0
        lines.append(f"- {alias}: {path} ({size:,} bytes)")
    mode = "WRITE ENABLED (--allow-write)" if ALLOW_WRITE else "read-only"
    return f"Configured databases ({mode}):\n" + "\n".join(lines)


def _list_tables(args: dict) -> str:
    path = _resolve_db(args["database"])
    con = _connect(path)
    try:
        cur = con.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        )
        rows = cur.fetchall()
    finally:
        con.close()
    if not rows:
        return "No tables or views found."
    return "\n".join(f"- {name} ({typ})" for name, typ in rows)


def _describe_table(args: dict) -> str:
    path = _resolve_db(args["database"])
    table = _check_table_name(args["table"])
    con = _connect(path)
    try:
        cols = con.execute(f"PRAGMA table_info({table})").fetchall()
        if not cols:
            raise ValueError(f"table not found: {table}")
        fks = con.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        idx = con.execute(f"PRAGMA index_list({table})").fetchall()
    finally:
        con.close()
    lines = [f"Table: {table}", "Columns:"]
    for _cid, name, ctype, notnull, default, pk in cols:
        flags = []
        if pk:
            flags.append("PRIMARY KEY")
        if notnull:
            flags.append("NOT NULL")
        if default is not None:
            flags.append(f"DEFAULT {default}")
        lines.append(f"  - {name} {ctype or ''} {' '.join(flags)}".rstrip())
    if fks:
        lines.append("Foreign keys:")
        for fk in fks:
            lines.append(f"  - {fk[3]} -> {fk[2]}.{fk[4]}")
    if idx:
        lines.append("Indexes: " + ", ".join(i[1] for i in idx))
    return "\n".join(lines)


def _table_stats(args: dict) -> str:
    path = _resolve_db(args["database"])
    con = _connect(path)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        lines = []
        for t in tables:
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            lines.append(f"- {t}: {n:,} rows")
    finally:
        con.close()
    return "\n".join(lines) if lines else "No tables found."


_READ_OK = ("select", "with", "pragma", "explain")


def _enforce_read_only(sql: str) -> str:
    cleaned = re.sub(r"--[^\n]*", "", sql)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.S).strip()
    first = cleaned.split(None, 1)[0].lower() if cleaned else ""
    if first not in _READ_OK:
        raise ValueError(
            "read_query only allows SELECT, WITH, PRAGMA and EXPLAIN statements. "
            "Writing is disabled unless the server is started with --allow-write."
        )
    return cleaned


def _read_query(args: dict) -> str:
    path = _resolve_db(args["database"])
    sql = _enforce_read_only(args["sql"])
    limit = max(1, min(int(args.get("limit", DEFAULT_LIMIT)), MAX_LIMIT))
    con = _connect(path)
    try:
        cur = con.execute(sql)
        return _rows_to_text(cur, limit)
    except sqlite3.Error as exc:
        raise ValueError(f"SQL error: {exc}") from exc
    finally:
        con.close()


def _sample_rows(args: dict) -> str:
    path = _resolve_db(args["database"])
    table = _check_table_name(args["table"])
    limit = max(1, min(int(args.get("limit", 10)), 100))
    con = _connect(path)
    try:
        cur = con.execute(f'SELECT * FROM "{table}" LIMIT {limit}')
        return _rows_to_text(cur, limit)
    except sqlite3.Error as exc:
        raise ValueError(f"SQL error: {exc}") from exc
    finally:
        con.close()


def _export_csv(args: dict) -> str:
    path = _resolve_db(args["database"])
    sql = _enforce_read_only(args["sql"])
    limit = max(1, min(int(args.get("limit", DEFAULT_LIMIT)), MAX_LIMIT))
    con = _connect(path)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(limit)
    except sqlite3.Error as exc:
        raise ValueError(f"SQL error: {exc}") from exc
    finally:
        con.close()
    buf = io.StringIO()
    writer = csv.writer(buf)
    if cols:
        writer.writerow(cols)
    writer.writerows(rows)
    return buf.getvalue().rstrip("\n")


def _write_query(args: dict) -> str:
    if not ALLOW_WRITE:
        raise ValueError("writing is disabled (start the server with --allow-write to enable)")
    path = _resolve_db(args["database"])
    con = _connect(path, write=True)
    try:
        cur = con.execute(args["sql"])
        con.commit()
        return f"OK. Rows affected: {cur.rowcount}"
    except sqlite3.Error as exc:
        con.rollback()
        raise ValueError(f"SQL error: {exc}") from exc
    finally:
        con.close()


# --------------------------------------------------------------------------
# Tool registry
# --------------------------------------------------------------------------

def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


_DB = {"type": "string", "description": "Database alias or path given at server startup"}
_SQL = {"type": "string"}
_LIMIT = {"type": "integer", "default": DEFAULT_LIMIT, "minimum": 1, "maximum": MAX_LIMIT}

TOOLS: dict[str, dict] = {
    "list_databases": {
        "description": "List the SQLite databases this server is allowed to access.",
        "inputSchema": _schema({}, []),
        "fn": _list_databases,
    },
    "list_tables": {
        "description": "List tables and views in a database.",
        "inputSchema": _schema({"database": _DB}, ["database"]),
        "fn": _list_tables,
    },
    "describe_table": {
        "description": "Show a table's columns, types, keys and indexes.",
        "inputSchema": _schema(
            {"database": _DB, "table": {"type": "string"}}, ["database", "table"]
        ),
        "fn": _describe_table,
    },
    "table_stats": {
        "description": "Show row counts for every table in a database.",
        "inputSchema": _schema({"database": _DB}, ["database"]),
        "fn": _table_stats,
    },
    "read_query": {
        "description": "Run a read-only SQL query (SELECT / WITH / PRAGMA / EXPLAIN only). Results are capped.",
        "inputSchema": _schema(
            {"database": _DB, "sql": _SQL, "limit": _LIMIT}, ["database", "sql"]
        ),
        "fn": _read_query,
    },
    "sample_rows": {
        "description": "Fetch a few example rows from a table.",
        "inputSchema": _schema(
            {"database": _DB, "table": {"type": "string"},
             "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100}},
            ["database", "table"],
        ),
        "fn": _sample_rows,
    },
    "export_csv": {
        "description": "Run a read-only query and return the result as CSV text.",
        "inputSchema": _schema(
            {"database": _DB, "sql": _SQL, "limit": _LIMIT}, ["database", "sql"]
        ),
        "fn": _export_csv,
    },
    "write_query": {
        "description": "Run a write SQL statement (INSERT/UPDATE/DELETE/DDL). Only available when the server is started with --allow-write.",
        "inputSchema": _schema({"database": _DB, "sql": _SQL}, ["database", "sql"]),
        "fn": _write_query,
        "write_only": True,
    },
}


def visible_tools() -> dict:
    if ALLOW_WRITE:
        return TOOLS
    return {k: v for k, v in TOOLS.items() if not v.get("write_only")}


# --------------------------------------------------------------------------
# Minimal MCP stdio transport (newline-delimited JSON-RPC 2.0)
# --------------------------------------------------------------------------

def _result(req_id, payload) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": payload}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def handle(request: dict) -> dict | None:
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params") or {}

    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _result(req_id, {})
    if method == "tools/list":
        return _result(req_id, {
            "tools": [
                {"name": name, "description": spec["description"],
                 "inputSchema": spec["inputSchema"]}
                for name, spec in visible_tools().items()
            ]
        })
    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        spec = TOOLS.get(name)
        if spec is None or (spec.get("write_only") and not ALLOW_WRITE):
            return _result(req_id, {
                "content": [{"type": "text", "text": f"unknown tool: {name}"}],
                "isError": True,
            })
        try:
            output = spec["fn"](arguments)
        except (ValueError, KeyError, TypeError) as exc:
            return _result(req_id, {
                "content": [{"type": "text", "text": f"error: {exc}"}],
                "isError": True,
            })
        return _result(req_id, {
            "content": [{"type": "text", "text": str(output)}],
            "isError": False,
        })
    return _error(req_id, -32601, f"method not found: {method}")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=SERVER_NAME)
    parser.add_argument("--db", action="append", default=[],
                        help="Path to a SQLite database file (repeatable)")
    parser.add_argument("--allow-write", action="store_true",
                        help="Enable the write_query tool (default: read-only)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global ALLOW_WRITE
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    ALLOW_WRITE = args.allow_write

    db_paths = list(args.db)
    env_dbs = os.environ.get("SQLITE_MCP_DBS", "")
    if env_dbs:
        db_paths += [p for p in env_dbs.split(os.pathsep) if p]
    for p in db_paths:
        ap = os.path.abspath(p)
        alias = os.path.splitext(os.path.basename(ap))[0]
        base, n = alias, 2
        while alias in DATABASES and DATABASES[alias] != ap:
            alias = f"{base}_{n}"
            n += 1
        DATABASES[alias] = ap

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
