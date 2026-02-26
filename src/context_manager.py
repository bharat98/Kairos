import os
import json
import logging
from pathlib import Path
import google.generativeai as genai
from dotenv import load_dotenv
from src.obsidian_reader import ObsidianReader
from src.utils import ensure_dirs, log_audit

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ContextManager:
    def __init__(self):
        load_dotenv(override=True)
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.vault_path = os.getenv("OBSIDIAN_VAULT_PATH")
        
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set in .env")
        if not self.vault_path:
            raise ValueError("OBSIDIAN_VAULT_PATH not set in .env")
            
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-3-pro-preview')
        self.reader = ObsidianReader(self.vault_path)
        
        # Ensure data directory exists
        Path("src/data").mkdir(parents=True, exist_ok=True)

    async def generate_context_map(self):
        """
        Reads vault via ObsidianReader and prompts Gemini 1.5 Pro to extract context.
        """
        logger.info("Starting vault analysis for context...")
        vault_content = self.reader.get_all_context_text()
        
        prompt = f"""
You are a strategic advisor analyzing a user's knowledge base (Obsidian vault) to extract their current life context and goals.

The user has following primary pillars in their life right now:
1. Health and fitness.
2. Career and livelihood goals.
3. Quality of life and maintenance. 

Analyze the following vault content and extract a structured JSON map.

VAULT CONTENT:
{vault_content}

OUTPUT FORMAT (JSON ONLY):
{{
  "primary_goals": [
    {{
      "goal": "Career Growth",
      "deadline": null,
      "description": "...",
      "priority": "HIGH"
    }},
    {{
       "goal": "Get Fit",
       "deadline": null,
       "description": "...",
       "priority": "HIGH"
    }}
  ],
  "active_projects": ["Project Name 1", "Project Name 2"],
  "skill_gaps": ["Skill 1", "Skill 2"],
  "recent_focus_areas": ["Area 1", "Area 2"],
  "critical_deadlines": [
    {{ "event": "...", "date": "..." }}
  ],
  "identity_context": "Brief summary of who the user is and what they value based on their files."
}}

Focus on accuracy. If information isn't found, use null or an empty list.
"""
        
        try:
            response = self.model.generate_content(prompt)
            # Find the JSON block in the response
            content = response.text
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            
            context_map = json.loads(content)
            
            # Save to src/data/context_map.json
            save_path = Path("src/data/context_map.json")
            with open(save_path, "w", encoding='utf-8') as f:
                json.dump(context_map, f, indent=2)
            
            logger.info(f"Context map successfully generated and saved to {save_path}")
            log_audit("context_refresh", "Regenerated context_map.json from vault analysis.")
            return context_map
            
        except Exception as e:
            logger.error(f"Error generating context map: {e}")
            return None

if __name__ == "__main__":
    import asyncio
    
    async def test():
        manager = ContextManager()
        result = await manager.generate_context_map()
        if result:
            print("Successfully extracted context!")
            print(json.dumps(result, indent=2))
            
    asyncio.run(test())
