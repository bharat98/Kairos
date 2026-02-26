"""
Migration: Add check-in system tables
Creates tables for proactive hourly check-ins and activity tracking
"""
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "kairos.db")

def migrate():
    """Create check-in system tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Table 1: user_config - Store user settings and sleep state
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER UNIQUE NOT NULL,
            check_ins_enabled INTEGER DEFAULT 1,
            is_sleeping INTEGER DEFAULT 0,
            sleep_start_time TIMESTAMP,
            default_wake_time TEXT DEFAULT '08:00',
            last_wake_time TIMESTAMP,
            timezone TEXT DEFAULT 'UTC',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Table 2: check_ins - Track each check-in message
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS check_ins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_time TIMESTAMP NOT NULL,
            sent_time TIMESTAMP,
            response_time TIMESTAMP,
            status TEXT DEFAULT 'pending',
            retry_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Table 3: activity_logs - Store analyzed hourly activities
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            user_response TEXT,
            activity_summary TEXT,
            productivity_type TEXT,
            alignment_score INTEGER,
            matched_todo_id INTEGER,
            category TEXT,
            reasoning TEXT,
            check_in_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (matched_todo_id) REFERENCES todos(id),
            FOREIGN KEY (check_in_id) REFERENCES check_ins(id)
        )
        ''')

        # Table 4: productivity_metrics - Aggregated statistics
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS productivity_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_start TIMESTAMP NOT NULL,
            period_end TIMESTAMP NOT NULL,
            period_type TEXT,
            total_check_ins INTEGER DEFAULT 0,
            responded_check_ins INTEGER DEFAULT 0,
            missed_check_ins INTEGER DEFAULT 0,
            sleeping_check_ins INTEGER DEFAULT 0,
            aligned_activities INTEGER DEFAULT 0,
            beneficial_activities INTEGER DEFAULT 0,
            wasted_activities INTEGER DEFAULT 0,
            avg_alignment_score REAL,
            productivity_ratio REAL,
            metrics_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Create indexes for performance
        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_activity_timestamp
        ON activity_logs(timestamp)
        ''')

        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_activity_type
        ON activity_logs(productivity_type)
        ''')

        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_checkin_scheduled
        ON check_ins(scheduled_time)
        ''')

        cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_checkin_status
        ON check_ins(status)
        ''')

        conn.commit()
        print("[OK] Check-in system migration completed successfully")
        print("   - user_config table created")
        print("   - check_ins table created")
        print("   - activity_logs table created")
        print("   - productivity_metrics table created")
        print("   - Indexes created for performance")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Migration failed: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
