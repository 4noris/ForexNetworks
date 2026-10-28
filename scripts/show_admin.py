#!/usr/bin/env python3
import sqlite3
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.getenv('DB_PATH', ROOT / 'users.db'))

if not DB.exists():
    print('users.db not found at', DB)
    raise SystemExit(1)

conn = sqlite3.connect(DB)
cur = conn.cursor()

def table_info(table):
    cur.execute(f"PRAGMA table_info({table})")
    return cur.fetchall()

tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('Tables in DB:', tables)

candidate_tables = [t for t in tables if 'user' in t.lower() or t in ('users','app_users')]
print('Candidate user tables:', candidate_tables)

for t in candidate_tables:
    print('\nTable:', t)
    cols = table_info(t)
    print('Columns:', cols)
    # Try common username columns
    for colname in ('username','name','email'):
        if any(colname == c[1] for c in cols):
            try:
                cur.execute(f"SELECT * FROM {t} WHERE {colname}=? LIMIT 5", ('admin',))
                rows = cur.fetchall()
                print('Rows for admin via', colname, ':', rows)
            except Exception as e:
                print('Query error:', e)

conn.close()
