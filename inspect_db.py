import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "kairos.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [row[0] for row in cursor.fetchall()]
print(f"DB_PATH: {DB_PATH}")
print("Tables:", tables)

if "todos" in tables:
    print("\n=== todos ===")
    cursor.execute("PRAGMA table_info(todos)")
    columns = [col[1] for col in cursor.fetchall()]
    print("Columns:", columns)

    cursor.execute("SELECT * FROM todos ORDER BY id DESC LIMIT 20")
    for row in cursor.fetchall():
        print(row)

conn.close()
