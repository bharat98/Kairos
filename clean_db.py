import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "kairos.db")

print(f"Cleaning Kairos database at: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Clear all tables
for table in ["todos", "audit_logs", "patterns", "insights"]:
    try:
        cursor.execute(f"DELETE FROM {table}")
        print(f"[OK] Cleared {cursor.rowcount} rows from {table}")
    except sqlite3.OperationalError as exc:
        print(f"[WARNING] Could not clear {table}: {exc}")

# Reset auto-increment counters
cursor.execute("DELETE FROM sqlite_sequence")
print("[OK] Reset auto-increment counters")

conn.commit()
conn.close()

print("[SUCCESS] Database cleanup complete")
