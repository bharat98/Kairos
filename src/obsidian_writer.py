import os
from pathlib import Path
from datetime import datetime

class ObsidianWriter:
    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)
        if not self.vault_path.exists():
            raise ValueError(f"Vault path does not exist: {self.vault_path}")
            
        # Aligning with PRD requirements (Section 6.2 Step 6)
        self.inbox_path = self.vault_path / "To Do" / "TO-DO List.md"
        self.completed_path = self.vault_path / "To Do" / "Completed Tasks.md"
        
        # Ensure target directory exists
        self.inbox_path.parent.mkdir(parents=True, exist_ok=True)

    def append_task(self, task_data: dict):
        """
        Appends a formatted task as a table row to the TO-DO List.md file.
        Format: | Task | Priority | Status | Category | Due Date | Due Time | Reasoning |
        """
        try:
            # Create file with header if it doesn't exist
            if not self.inbox_path.exists():
                with open(self.inbox_path, "w", encoding='utf-8') as f:
                    f.write("# 📋 TO-DO List\n\n")
                    f.write("| ID | Task | Priority | Status | Category | Due Date | Due Time | Reasoning |\n")
                    f.write("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n")

            category = task_data.get("category", "General")
            priority = task_data.get("priority", "MEDIUM")
            name = task_data.get("task_name", "Untitled Task")
            reasoning = task_data.get("reasoning", "").replace("\n", " ") # Keep on one line
            status = task_data.get("status", "Pending")
            
            # Handle due date/time formatting
            raw_due_date = task_data.get("due_date")
            raw_due_time = task_data.get("due_time")
            is_scheduled = task_data.get("is_scheduled", True)
            
            if not is_scheduled or not raw_due_date:
                date_display = "📅 Unscheduled"
                time_display = "—"
            else:
                # Convert YYYY-MM-DD to DD-MM-YYYY
                try:
                    d = datetime.strptime(raw_due_date, "%Y-%m-%d")
                    date_display = d.strftime("%d-%m-%Y")
                except ValueError:
                    date_display = raw_due_date  # Use as-is if parsing fails
                
                # Convert HH:MM (24hr) to 12hr AM/PM
                if raw_due_time:
                    try:
                        t = datetime.strptime(raw_due_time, "%H:%M")
                        time_display = t.strftime("%I:%M %p").lstrip("0")
                    except ValueError:
                        time_display = raw_due_time  # Use as-is if parsing fails
                else:
                    time_display = "—"
            
            # Escape any pipe characters in the text
            name = name.replace("|", "\\|")
            reasoning = reasoning.replace("|", "\\|")

            entry = f"| {task_data.get('id', '—')} | {name} | {priority} | {status} | {category} | {date_display} | {time_display} | {reasoning} |\n"

            with open(self.inbox_path, "a", encoding='utf-8') as f:
                f.write(entry)
                
            return True
        except Exception as e:
            print(f"Error writing to Obsidian: {e}")
            return False

    def sync_all_tasks(self, active_tasks: list, completed_tasks: list):
        """
        Refreshes both Active and Completed lists in separate files.
        """
        try:
            # 1. Update Active Tasks (Overwrite TO-DO List.md)
            with open(self.inbox_path, "w", encoding='utf-8') as f:
                f.write("# 📋 TO-DO List\n\n")
                f.write("| ID | Task | Priority | Status | Category | Due Date | Due Time | Reasoning |\n")
                f.write("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n")
                
                for task in active_tasks:
                    line = self._format_task_row(task)
                    f.write(line)
            
            # 2. Update Completed Tasks (Overwrite or Append to Completed Tasks.md)
            # For now, we'll overwrite with the latest 10-20 passed from DB to keep it clean,
            # but ideally this file could grow. Since we pass 'completed_tasks' which is limited by query,
            # we overwrite to ensure the list matches the DB's "Recently Completed" view.
            if completed_tasks:
                with open(self.completed_path, "w", encoding='utf-8') as f:
                    f.write("# ✅ Recently Completed\n\n")
                    f.write("| ID | Task | Completed At | Category | Priority |\n")
                    f.write("| :--- | :--- | :--- | :--- | :--- |\n")
                    
                    for task in completed_tasks:
                        tid = task.get('id', '—')
                        comp_time = task.get('completed_at', '—')
                        name = task.get('task_name', 'Untitled').replace("|", "\\|")
                        cat = task.get('category', 'General')
                        prio = task.get('priority', 'MEDIUM')
                        
                        f.write(f"| {tid} | {name} | {comp_time} | {cat} | {prio} |\n")
                        
            return True
        except Exception as e:
            print(f"Error syncing to Obsidian: {e}")
            return False

    def _format_task_row(self, task_data: dict) -> str:
        """Helper to format a single task row."""
        tid = task_data.get("id", "—")
        category = task_data.get("category", "General")
        priority = task_data.get("priority", "MEDIUM")
        name = task_data.get("task_name", "Untitled Task").replace("|", "\\|")
        if task_data.get("recurrence"):
            name += " 🔁"
            
        reasoning = task_data.get("reasoning", "").replace("\n", " ").replace("|", "\\|")
        status = task_data.get("status", "Pending")
        
        # Handle due date/time formatting
        raw_due_date = task_data.get("due_date")
        raw_due_time = task_data.get("due_time")
        is_scheduled = task_data.get("is_scheduled", True)
        
        if not is_scheduled or not raw_due_date:
            date_display = "📅 Unscheduled"
            time_display = "—"
        else:
            try:
                d = datetime.strptime(raw_due_date, "%Y-%m-%d")
                date_display = d.strftime("%d-%m-%Y")
            except ValueError:
                date_display = raw_due_date
            
            if raw_due_time:
                try:
                    t = datetime.strptime(raw_due_time, "%H:%M")
                    time_display = t.strftime("%I:%M %p").lstrip("0")
                except ValueError:
                    time_display = raw_due_time
            else:
                time_display = "—"

        return f"| {tid} | {name} | {priority} | {status} | {category} | {date_display} | {time_display} | {reasoning} |\n"

if __name__ == "__main__":
    # Test script
    from dotenv import load_dotenv
    load_dotenv()
    
    vault_path = os.getenv("OBSIDIAN_VAULT_PATH")
    if vault_path:
        writer = ObsidianWriter(vault_path)
        test_data = {
            "task_name": "Test Kairos Sync",
            "category": "Career",
            "priority": "HIGH",
            "reasoning": "Verification of the sync engine implementation."
        }
        if writer.append_task(test_data):
            print(f"Successfully wrote to {writer.inbox_path}")
    else:
        print("OBSIDIAN_VAULT_PATH not set in .env")
