#!/usr/bin/env python3
"""End-to-end test: create a real SQLite DB, spawn the server, speak MCP.

Run:  python tests/test_mcp.py
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def send(proc, obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def recv(proc):
    line = proc.stdout.readline()
    assert line, "server closed stdout unexpectedly"
    return json.loads(line)


def call(proc, req_id, name, arguments):
    send(proc, {"jsonrpc": "2.0", "id": req_id, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}})
    resp = recv(proc)
    result = resp["result"]
    return result["content"][0]["text"], result.get("isError", False)


def spawn(db_path, extra=()):
    proc = subprocess.Popen(
        [sys.executable, "-m", "readonly_sqlite_mcp", "--db", str(db_path), *extra],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=ROOT,
    )
    send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}}})
    resp = recv(proc)
    assert resp["result"]["serverInfo"]["name"] == "readonly-sqlite-mcp", resp
    send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    return proc


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "shop.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL)")
        con.executemany("INSERT INTO products VALUES (?, ?, ?)",
                        [(1, "Notebook", 49.5), (2, "Pen", 9.99), (3, "Bag", 499.0)])
        con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, product_id INTEGER REFERENCES products(id), qty INTEGER)")
        con.executemany("INSERT INTO orders VALUES (?, ?, ?)", [(1, 1, 2), (2, 3, 1)])
        con.commit()
        con.close()

        # ---- read-only mode ----
        proc = spawn(db)
        try:
            send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = [t["name"] for t in recv(proc)["result"]["tools"]]
            assert "write_query" not in names, f"write tool must be hidden: {names}"
            assert len(names) == 7, names
            print("tools/list OK (7 tools, write hidden)")

            text, err = call(proc, 3, "list_databases", {})
            assert not err and "shop" in text, text
            print("list_databases OK")

            text, err = call(proc, 4, "list_tables", {"database": "shop"})
            assert "products" in text and "orders" in text, text
            print("list_tables OK")

            text, err = call(proc, 5, "describe_table", {"database": "shop", "table": "products"})
            assert "price" in text and "PRIMARY KEY" in text, text
            print("describe_table OK")

            text, err = call(proc, 6, "read_query",
                             {"database": "shop",
                              "sql": "SELECT name FROM products WHERE price > 10 ORDER BY price"})
            assert "Notebook" in text and "Bag" in text and "Pen" not in text, text
            print("read_query OK")

            text, err = call(proc, 7, "read_query",
                             {"database": "shop", "sql": "DELETE FROM products WHERE id = 1"})
            assert err and "only allows" in text, text
            print("write blocked in read_query OK")

            text, err = call(proc, 8, "export_csv",
                             {"database": "shop", "sql": "SELECT id, name FROM products ORDER BY id"})
            assert text.startswith("id,name") and "2,Pen" in text, text
            print("export_csv OK")

            # unknown database must be rejected
            text, err = call(proc, 9, "list_tables", {"database": "nope"})
            assert err, text
            print("unknown database rejected OK")

            # db file must be untouched: all 3 products still there
            con = sqlite3.connect(db)
            n = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            con.close()
            assert n == 3, n
            print("database unmodified OK")
        finally:
            proc.stdin.close()
            proc.terminate()

        # ---- write mode ----
        proc = spawn(db, extra=("--allow-write",))
        try:
            send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            names = [t["name"] for t in recv(proc)["result"]["tools"]]
            assert "write_query" in names, names
            text, err = call(proc, 3, "write_query",
                             {"database": "shop",
                              "sql": "INSERT INTO products VALUES (4, 'Desk', 999.0)"})
            assert not err and "Rows affected: 1" in text, text
            print("write mode OK")
        finally:
            proc.stdin.close()
            proc.terminate()

        print("\nALL TESTS PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(main())
