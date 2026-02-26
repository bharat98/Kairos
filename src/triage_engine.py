import os
import json
import logging
from typing import Optional, Dict, Any
import google.generativeai as genai
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from src.pattern_manager import PatternManager

class TriageEngine:
    def __init__(self):
        load_dotenv(override=True)
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set in .env")
            
        genai.configure(api_key=self.api_key)
        # Using 3-flash-preview for low-latency
        self.model = genai.GenerativeModel('gemini-3-flash-preview')
        self.context_path = "src/data/context_map.json"
        self.pm = PatternManager()

    def _load_context(self) -> str:
        """Loads the context map as a string."""
        if not os.path.exists(self.context_path):
            logger.warning("Context map not found. Triage will be less accurate.")
            return "No context available."
        
        try:
            with open(self.context_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error loading context map: {e}")
            return "Error loading context."

    async def triage_task(self, user_input: str, media_paths: Optional[list] = None) -> Dict[str, Any]:
        """
        Triages a task based on user input and cached context.
        Supports multimodal input if media_paths are provided.
        Detects 'human override' keyword to respect user's explicit values.
        """
        context_string = self._load_context()
        patterns = self.pm.get_active_patterns()
        patterns_str = "\n".join([f"- {p}" for p in patterns]) if patterns else "No recurring patterns detected yet."
        
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        current_day = datetime.now().strftime("%A")  # e.g., "Wednesday"
        
        # Human Override Detection
        is_override = "human override" in user_input.lower()
        override_instruction = ""
        if is_override:
            override_instruction = """
HUMAN OVERRIDE ACTIVE: The user has explicitly invoked "human override". 
You MUST:
1. Respect ANY priority, category, or date the user specifies - do NOT override their choice
2. Set pushback to null (no pushback when user overrides)
3. Set suggested_alternative to null
4. If user says "priority HIGH", set priority to HIGH regardless of your analysis
5. Still parse dates correctly
"""
            logger.info("Human override detected in input")
        
        prompt = f"""
You are an intelligent triage agent for "Kairos - Life Sorter". 
Your goal is to categorize and prioritize a new task based on the user's strategic context and learned patterns.

CURRENT DATE: {current_date} ({current_day})

USER STRATEGIC CONTEXT:
{context_string}

LEARNED USER PATTERNS (Overrides):
{patterns_str}

NEW INPUT:
{user_input}

{override_instruction}
ANALYSIS RULES:
1. Alignment: Does this align with "Get Fit", "Career Growth", or "Live a Good Quality Life" (daily maintenance/hygiene)?
2. Priority:
   - HIGH: Directly impacts the critical career deadline or critical health.
   - MEDIUM: Aligned with Career/Fitness. Routine "Quality Life" tasks (hygiene, chores) should be MEDIUM or LOW unless critical.
   - LOW: Tangential, curiosity-driven, hobbies, or minor daily maintenance.
3. Pushback:
   - If a task is misaligned (not in the 3 pillars), provide "Strategic Pushback".
   - Do NOT push back on "Live a Good Quality Life" tasks (brushing, showering, etc.), but categorize them as MEDIUM/LOW.
   - Push back on excessive distractions (e.g., "watch 10 hours of TV").
4. Alternatives: If priority is LOW and task is a distraction, suggest 1-2 specific high-priority alternatives.

DATE PARSING RULES:
- ALWAYS convert natural language dates to YYYY-MM-DD format using the CURRENT DATE above as reference.
- "saturday" or "coming saturday" → Calculate the next Saturday from {current_date}
- "tomorrow" → {current_date} + 1 day
- "next week" → {current_date} + 7 days
- "day after tomorrow" → {current_date} + 2 days
- If a date is mentioned (even informally like "friday", "this weekend"), you MUST return a valid YYYY-MM-DD. If unable, ask user for clarification.
- Only return null if the user explicitly says "no date", "unscheduled", or truly never mentions any timeframe.

OUTPUT FORMAT (JSON ONLY):
{{
  "task_name": "Concise version of the task",
  "category": "Career | Fitness | Projects | Personal | Hobby",
  "priority": "HIGH | MEDIUM | LOW",
  "due_date": "YYYY-MM-DD (parse natural language dates!) or null if truly no date mentioned",
  "due_time": "HH:MM (24hr format) or null if not mentioned or only a date was given",
  "recurrence": "daily | weekly | weekly:Mon,Wed | monthly | every X days | null",
  "scheduling_unclear": true if user mentioned deadline vaguely like 'soon'/'later'/'eventually' or false otherwise,
  "reasoning": "Brief explanation of why this priority/category was chosen",
  "alignment_score": 0-10,
  "pushback": "Message to user if priority is LOW or alignment is weak, otherwise null",
  "suggested_alternative": "A suggested high-priority task based on context, otherwise null",
  "clarification_needed": "Ask a specific question if the task purpose is unclear, otherwise null"
}}
"""
        
        try:
            # Handle multimodal if paths provided
            contents = [prompt]
            if media_paths:
                for path in media_paths:
                    if os.path.exists(path):
                        # Simple implementation for now, assuming image/voice handling is implemented in Phase 2
                        pass
            
            response = self.model.generate_content(contents)
            
            # Extract JSON
            text = response.text
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            
            return json.loads(text)
            
        except Exception as e:
            logger.error(f"Triage failed: {e}")
            return {
                "task_name": user_input[:50],
                "category": "Unknown",
                "priority": "MEDIUM",
                "reasoning": f"Triage engine error: {e}",
                "alignment_score": 0,
                "pushback": None,
                "clarification_needed": None
            }

    async def parse_edit_request(self, edit_instruction: str) -> Dict[str, Any]:
        """
        Parses a natural language edit instruction and returns structured fields to update.
        Example: "change priority to high" -> {"priority": "HIGH"}
        """
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        prompt = f"""
You are a helper parsing edit requests for a todo list task.
CURRENT DATE: {current_date}

USER INSTRUCTION: "{edit_instruction}"

Extract the fields the user wants to change.
Fields allowed: task_name, priority (HIGH/MEDIUM/LOW), category, due_date (YYYY-MM-DD), due_time (HH:MM).

- If user mentions "tomorrow", "friday", etc., calculate the YYYY-MM-DD date based on Current Date.
- Return ONLY a JSON object with the fields that need updating.
- Ignore polite conversational text.

Example Input: "change priority to high and move to personal category"
Example Output: {{ "priority": "HIGH", "category": "Personal" }}

Output JSON:
"""
        try:
            response = self.model.generate_content(prompt)
            text = response.text.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            
            return json.loads(text)
        except Exception as e:
            logger.error(f"Edit parse failed: {e}")
            return {}

if __name__ == "__main__":
    import asyncio
    
    async def test():
        engine = TriageEngine()
        test_inputs = [
            "Submit application for a target role",
            "Learn how to bake sourdough bread",
            "Go for a 5km run",
            "Research AWS Security best practices"
        ]
        
        for inp in test_inputs:
            print(f"\n--- Testing: {inp} ---")
            result = await engine.triage_task(inp)
            print(json.dumps(result, indent=2))
            
    asyncio.run(test())
