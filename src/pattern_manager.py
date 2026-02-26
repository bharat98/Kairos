import os
import json
import logging
import sqlite3
import google.generativeai as genai
from dotenv import load_dotenv
from src.utils import get_connection, log_audit

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PatternManager:
    def __init__(self):
        load_dotenv(override=True)
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set in .env")
            
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-3-flash-preview')

    def get_active_patterns(self):
        """Retrieves list of active patterns from the DB."""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT pattern_data FROM patterns WHERE confidence > 0.7")
            rows = cursor.fetchall()
            conn.close()
            return [row[0] for row in rows]
        except Exception as e:
            logger.error(f"Failed to fetch patterns: {e}")
            return []

    async def analyze_overrides(self):
        """
        Scans audit_logs for manual overrides and uses Gemini to find patterns.
        """
        logger.info("Analyzing manual overrides for pattern detection...")
        
        try:
            conn = get_connection()
            cursor = conn.cursor()
            # Find recent manual syncs
            cursor.execute("""
                SELECT details FROM audit_logs 
                WHERE event_type = 'manual_sync' 
                ORDER BY timestamp DESC LIMIT 20
            """)
            overrides = cursor.fetchall()
            
            if len(overrides) < 3:
                logger.info("Not enough overrides to detect patterns yet.")
                conn.close()
                return
            
            # Fetch the actual tasks for these overrides
            override_details = []
            for (detail,) in overrides:
                todo_id = detail.split("todo ")[1]
                cursor.execute("SELECT task, category, reasoning FROM todos WHERE id = ?", (todo_id,))
                task = cursor.fetchone()
                if task:
                    override_details.append(f"Task: {task[0]} | Category: {task[1]} | AI Reasoning: {task[2]}")
            
            conn.close()
            
            if not override_details:
                return

            prompt = f"""
Analyze the following list of tasks that the user FORCED to sync, overriding my strategic pushback.
Identify if there is a recurring theme or pattern (e.g., "The user always wants to sync grocery lists despite low career alignment").

OVERRIDDEN TASKS:
{chr(10).join(override_details)}

If a pattern is found, output a single sentence description of the pattern.
If no clear pattern is found, output "NONE".

Format: A simple string.
"""
            response = self.model.generate_content(prompt)
            pattern_text = response.text.strip()
            
            if pattern_text != "NONE":
                self._save_pattern(pattern_text)
                logger.info(f"New pattern detected: {pattern_text}")
                log_audit("pattern_detected", pattern_text)
                
        except Exception as e:
            logger.error(f"Pattern analysis failed: {e}")

    def _save_pattern(self, pattern_text):
        """Saves or updates a pattern in the database."""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            # Deduplication logic could go here, for now just insert
            cursor.execute("""
                INSERT INTO patterns (pattern_type, pattern_data, confidence, usage_count)
                VALUES (?, ?, ?, ?)
            """, ("Override", pattern_text, 0.8, 1))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to save pattern: {e}")

if __name__ == "__main__":
    import asyncio
    async def test():
        pm = PatternManager()
        await pm.analyze_overrides()
        print("Patterns:", pm.get_active_patterns())
    asyncio.run(test())
