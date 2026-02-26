import sqlite3
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "kairos.db")

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Todos Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS todos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task TEXT NOT NULL,
        raw_input TEXT,
        category TEXT,
        priority TEXT,
        due_date DATE,
        due_time TEXT,
        is_scheduled INTEGER DEFAULT 1,
        status TEXT DEFAULT 'Pending',
        user_clarification TEXT,
        reasoning TEXT,
        vault_links TEXT,
        recurrence TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Migration: Add new columns to existing databases
    cursor.execute("PRAGMA table_info(todos)")
    columns = [col[1] for col in cursor.fetchall()]
    if "due_time" not in columns:
        cursor.execute("ALTER TABLE todos ADD COLUMN due_time TEXT")
        print("Migration: Added 'due_time' column to todos table")
    if "is_scheduled" not in columns:
        cursor.execute("ALTER TABLE todos ADD COLUMN is_scheduled INTEGER DEFAULT 1")
        print("Migration: Added 'is_scheduled' column to todos table")
    if "recurrence" not in columns:
        cursor.execute("ALTER TABLE todos ADD COLUMN recurrence TEXT")
        print("Migration: Added 'recurrence' column to todos table")

    # Patterns Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS patterns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pattern_type TEXT,
        pattern_data TEXT,
        confidence REAL,
        last_used TIMESTAMP,
        usage_count INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Insights Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS insights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start DATE,
        week_end DATE,
        report_markdown TEXT,
        metrics_json TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Audit Logs Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT,
        details TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")

    # Run check-in system migration
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "migration",
            "src/migrations/add_check_in_system.py"
        )
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration.migrate()
    except Exception as e:
        print(f"Check-in migration: {e}")

if __name__ == "__main__":
    init_db()
