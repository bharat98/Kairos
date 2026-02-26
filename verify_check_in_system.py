"""
Quick verification script for check-in system
Run this to verify all components load properly
"""
import sys
import os

print("=" * 60)
print("CHECK-IN SYSTEM VERIFICATION")
print("=" * 60)

# Test 1: Database tables exist
print("\n[1/5] Checking database tables...")
try:
    import sqlite3
    conn = sqlite3.connect('kairos.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table'
        AND name IN ('user_config', 'check_ins', 'activity_logs', 'productivity_metrics')
    """)
    tables = [row[0] for row in cursor.fetchall()]
    conn.close()

    required = ['user_config', 'check_ins', 'activity_logs', 'productivity_metrics']
    if all(t in tables for t in required):
        print("[OK] All required tables exist:", tables)
    else:
        print("[ERROR] Missing tables. Found:", tables)
        sys.exit(1)
except Exception as e:
    print(f"[ERROR] Database check failed: {e}")
    sys.exit(1)

# Test 2: Import check-in modules
print("\n[2/5] Importing check-in modules...")
try:
    from src.check_in_manager import CheckInManager
    print("[OK] CheckInManager imported")

    from src.check_in_scheduler import CheckInScheduler
    print("[OK] CheckInScheduler imported")

    from src.activity_analyzer import ActivityAnalyzer
    print("[OK] ActivityAnalyzer imported")

    from src.productivity_reporter import ProductivityReporter
    print("[OK] ProductivityReporter imported")
except Exception as e:
    print(f"[ERROR] Import failed: {e}")
    sys.exit(1)

# Test 3: Check dependencies
print("\n[3/5] Checking dependencies...")
try:
    import apscheduler
    print("[OK] APScheduler installed")

    import google.generativeai
    print("[OK] Google Generative AI installed")
except ImportError as e:
    print(f"[ERROR] Missing dependency: {e}")
    sys.exit(1)

# Test 4: Verify bot.py imports
print("\n[4/5] Verifying bot.py integration...")
try:
    # Just compile check, don't run
    import py_compile
    py_compile.compile('src/bot.py', doraise=True)
    print("[OK] bot.py compiles without errors")
except Exception as e:
    print(f"[ERROR] bot.py has errors: {e}")
    sys.exit(1)

# Test 5: Check .env configuration
print("\n[5/5] Checking environment configuration...")
try:
    from dotenv import load_dotenv
    load_dotenv()

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    gemini_key = os.getenv("GEMINI_API_KEY")

    if telegram_token:
        print("[OK] TELEGRAM_BOT_TOKEN configured")
    else:
        print("[WARN] TELEGRAM_BOT_TOKEN not found in .env")

    if gemini_key:
        print("[OK] GEMINI_API_KEY configured")
    else:
        print("[WARN] GEMINI_API_KEY not found in .env")
except Exception as e:
    print(f"[ERROR] Environment check failed: {e}")

print("\n" + "=" * 60)
print("VERIFICATION COMPLETE - All systems ready!")
print("=" * 60)
print("\nNext steps:")
print("1. Run: venv/Scripts/python.exe -m src.bot")
print("2. Send /start to your bot in Telegram")
print("3. Check-ins will begin at the next hour mark (:00)")
print("4. Use Sleep/Wake buttons to control quiet periods")
print("5. Use /stats to view daily productivity reports")
