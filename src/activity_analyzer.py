"""
ActivityAnalyzer: Uses Gemini Flash to analyze hourly activities
"""
import logging
import json
import os
from datetime import datetime
import google.generativeai as genai
from src.database import get_connection
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Configure Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

class ActivityAnalyzer:
    def __init__(self):
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    async def analyze_activity(self, user_response: str, check_in_id: int):
        """
        Analyze user's hourly activity against their todo list
        Returns dict with analysis results
        """
        try:
            # Load active todos
            active_todos = self._load_active_todos()

            # Load user context
            user_context = self._load_user_context()

            # Build prompt
            prompt = self._build_analysis_prompt(user_response, active_todos, user_context)

            # Call Gemini Flash
            response = self.model.generate_content(prompt)
            response_text = response.text.strip()

            # Parse JSON response
            try:
                # Extract JSON from markdown code blocks if present
                if "```json" in response_text:
                    response_text = response_text.split("```json")[1].split("```")[0].strip()
                elif "```" in response_text:
                    response_text = response_text.split("```")[1].split("```")[0].strip()

                analysis = json.loads(response_text)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse Gemini response: {e}\nResponse: {response_text}")
                # Fallback to neutral categorization
                analysis = {
                    "activity_summary": user_response[:100],
                    "productivity_type": "beneficial",
                    "matched_todo_id": None,
                    "alignment_score": 5,
                    "category": "Unknown",
                    "reasoning": "Analysis pending - manual review required",
                    "feedback": "Activity logged. I had trouble analyzing it automatically."
                }

            # Save to database
            self._save_activity_log(check_in_id, user_response, analysis)

            # Update check-in status
            self._update_check_in_status(check_in_id, 'completed')

            return analysis

        except Exception as e:
            logger.error(f"Activity analysis failed: {e}")
            # Return fallback analysis
            return {
                "activity_summary": user_response[:100],
                "productivity_type": "beneficial",
                "matched_todo_id": None,
                "alignment_score": 5,
                "category": "Unknown",
                "reasoning": str(e),
                "feedback": "Activity logged successfully."
            }

    def _load_active_todos(self):
        """Load active high/medium priority todos"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, task, category, priority, due_date
                   FROM todos
                   WHERE status = 'Pending'
                     AND priority IN ('HIGH', 'MEDIUM')
                   ORDER BY priority DESC, due_date ASC
                   LIMIT 20"""
            )
            rows = cursor.fetchall()
            conn.close()

            todos = []
            for row in rows:
                todos.append({
                    "id": row[0],
                    "task": row[1],
                    "category": row[2],
                    "priority": row[3],
                    "due_date": row[4]
                })
            return todos

        except Exception as e:
            logger.error(f"Failed to load todos: {e}")
            return []

    def _load_user_context(self):
        """Load user context from context_map.json"""
        try:
            context_path = "src/data/context_map.json"
            if os.path.exists(context_path):
                with open(context_path, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Failed to load user context: {e}")
            return {}

    def _build_analysis_prompt(self, user_response: str, active_todos: list, user_context: dict):
        """Build structured prompt for Gemini"""
        # Extract key context
        primary_goal = user_context.get("primary_goal", "Career Growth")
        priorities = user_context.get("priorities", [])

        # Format todos
        todos_text = ""
        if active_todos:
            for todo in active_todos:
                todos_text += f"- [ID: {todo['id']}] {todo['task']} (Category: {todo['category']}, Priority: {todo['priority']})\n"
        else:
            todos_text = "No active high-priority todos found."

        prompt = f"""You are an AI productivity coach analyzing hourly activity logs.

USER CONTEXT:
- Primary Goal: {primary_goal}
- Priorities: {', '.join(priorities) if priorities else 'Career, Fitness, Personal Development'}

ACTIVE TODO LIST:
{todos_text}

USER'S HOURLY ACTIVITY:
"{user_response}"

TASK:
Analyze this activity and determine:
1. Is it directly working on a todo? If yes, which one (provide ID)?
2. Productivity type:
   - "aligned": Working on a specific todo from the list
   - "beneficial": Productive and goal-aligned but not on todo list
   - "wasted": Unproductive time not contributing to goals
3. Alignment score: 0-10 (0=totally wasted, 10=perfectly aligned with primary goal)
4. Category: Career, Fitness, Personal, Entertainment, etc.
5. Brief reasoning
6. Encouraging feedback message (1-2 sentences)

IMPORTANT:
- Be honest about "wasted" time - YouTube/social media/gaming should be marked as wasted unless directly work-related
- Only mark as "aligned" if it directly matches a todo
- Be encouraging but truthful

Respond ONLY with valid JSON (no markdown, no extra text):
{{
  "activity_summary": "Brief summary of what user did",
  "productivity_type": "aligned|beneficial|wasted",
  "matched_todo_id": 15 or null,
  "alignment_score": 7,
  "category": "Career",
  "reasoning": "Why you categorized it this way",
  "feedback": "Encouraging message for user"
}}"""

        return prompt

    def _save_activity_log(self, check_in_id: int, user_response: str, analysis: dict):
        """Save activity analysis to database"""
        try:
            conn = get_connection()
            cursor = conn.cursor()

            cursor.execute(
                """INSERT INTO activity_logs
                   (timestamp, user_response, activity_summary, productivity_type,
                    alignment_score, matched_todo_id, category, reasoning, check_in_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(),
                    user_response,
                    analysis.get('activity_summary'),
                    analysis.get('productivity_type'),
                    analysis.get('alignment_score'),
                    analysis.get('matched_todo_id'),
                    analysis.get('category'),
                    analysis.get('reasoning'),
                    check_in_id
                )
            )

            conn.commit()
            conn.close()
            logger.info(f"Activity log saved for check-in {check_in_id}")

        except Exception as e:
            logger.error(f"Failed to save activity log: {e}")
            raise

    def _update_check_in_status(self, check_in_id: int, status: str):
        """Update check-in status"""
        try:
            conn = get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "UPDATE check_ins SET status = ?, response_time = ? WHERE id = ?",
                (status, datetime.now(), check_in_id)
            )

            conn.commit()
            conn.close()

        except Exception as e:
            logger.error(f"Failed to update check-in status: {e}")
