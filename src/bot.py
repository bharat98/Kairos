import os
import logging
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, BotCommand
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from dotenv import load_dotenv
from src.utils import log_audit, ensure_dirs, get_temp_path, get_connection
from src.check_in_scheduler import CheckInScheduler
from src.check_in_manager import CheckInManager
from src.activity_analyzer import ActivityAnalyzer
from src.productivity_reporter import ProductivityReporter

# --- Date/Time Formatting Helpers ---
def format_due_date_display(due_date: str, due_time: str, is_scheduled: bool = True) -> str:
    """Convert YYYY-MM-DD HH:MM to user-friendly format: 29-01-2026 @ 2:30 PM"""
    if not is_scheduled or not due_date:
        return "📅 Unscheduled"
    
    try:
        d = datetime.strptime(due_date, "%Y-%m-%d")
        date_str = d.strftime("%d-%m-%Y")
    except ValueError:
        date_str = due_date
    
    if due_time:
        try:
            t = datetime.strptime(due_time, "%H:%M")
            time_str = t.strftime("%I:%M %p").lstrip("0")  # 2:30 PM
            return f"{date_str} @ {time_str}"
        except ValueError:
            return f"{date_str} @ {due_time}"
    return date_str

async def mark_task_complete(query, context, task_id: str, custom_time: str = None):
    """Mark a task as complete in the database."""
    from datetime import datetime
    from src.database import get_connection
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Get task details first
        cursor.execute("SELECT task FROM todos WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            await query.message.reply_text(f"❌ Task ID {task_id} not found.", reply_markup=get_inline_menu())
            conn.close()
            return
        
        task_name = row[0]
        
        # Determine completion time
        if custom_time:
            completed_at = custom_time  # Will be parsed by caller
        else:
            completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Update task
        cursor.execute(
            "UPDATE todos SET status = 'Completed', completed_at = ? WHERE id = ?",
            (completed_at, task_id)
        )
        conn.commit()
        conn.close()
        
        # Reset state
        context.user_data["state"] = None
        context.user_data["pending_done_id"] = None
        
        await query.message.reply_text(
            f"🎉 **Task Completed!**\n\n"
            f"✅ {task_name}\n"
            f"⏰ Completed: {completed_at}",
            parse_mode="Markdown",
            reply_markup=get_inline_menu()
        )
        log_audit("task_completed", f"Task {task_id} marked complete")
        
        # Check for recurrence
        await check_and_regenerate_recurring(query, context, task_id)
        
    except Exception as e:
        logger.error(f"Failed to mark task complete: {e}")
        await query.message.reply_text(f"❌ Error: {e}", reply_markup=get_inline_menu())

async def check_and_regenerate_recurring(update: Update, context: ContextTypes.DEFAULT_TYPE, task_id: int):
    """Checks if a completed task is recurring and creates the next instance."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Fetch recurrence rules from the COMPLETED task
        cursor.execute("SELECT task, category, priority, recurrence, reasoning FROM todos WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        
        if not row or not row[3]: # No recurrence
            conn.close()
            return
            
        task_name, category, priority, recurrence, reasoning = row
        recurrence = recurrence.lower()
        
        # Calculate next due date
        import datetime as dt
        from datetime import timedelta
        today = dt.date.today()
        next_due = None
        
        if "daily" in recurrence or "every day" in recurrence:
            next_due = today + timedelta(days=1)
        elif "weekly" in recurrence:
             next_due = today + timedelta(days=7)
        # Add more parsing logic here as needed (e.g. regex for "every X days")
        
        if next_due: 
            # Create NEW task
            cursor.execute(
                "INSERT INTO todos (task, raw_input, category, priority, due_date, due_time, is_scheduled, reasoning, status, recurrence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_name, f"Recurring: {task_name}", category, priority, next_due.strftime("%Y-%m-%d"), None, 1, f"Regenerated from Task {task_id} ({recurrence})", "Pending", row[3]) # Keep original recurrence string
            )
            new_id = cursor.lastrowid
            conn.commit()
            
            # Notify User
            next_due_str = next_due.strftime("%a, %b %d")
            await update.message.reply_text(
                f"🔄 **Recurring Task Regenerated!**\n"
                f"📅 Next due: {next_due_str}\n"
                f"🆔 New ID: {new_id}",
                parse_mode="Markdown"
            )
            
        conn.close()
        
    except Exception as e:
        logger.error(f"Regeneration failed: {e}")

# Load environment variables
load_dotenv(override=True)
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
VAULT_PATH = os.getenv("OBSIDIAN_VAULT_PATH")

from src.triage_engine import TriageEngine
from src.context_manager import ContextManager
from src.obsidian_writer import ObsidianWriter

# Initialize Engines
triage_engine = TriageEngine()
context_manager = ContextManager()
obsidian_writer = ObsidianWriter(VAULT_PATH) if VAULT_PATH else None

# Initialize Check-In System (initialized in post_init)
check_in_manager = None
activity_analyzer = ActivityAnalyzer()
productivity_reporter = ProductivityReporter()
check_in_scheduler = None

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def execute_full_sync():
    """Performs a full sync of active and completed tasks to Obsidian."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Fetch Active
        cursor.execute("SELECT id, task, category, priority, due_date, due_time, is_scheduled, reasoning, status, recurrence FROM todos WHERE status='Pending' ORDER BY created_at DESC")
        active_rows = cursor.fetchall()
        active_tasks = []
        for r in active_rows:
            active_tasks.append({
                "id": r[0], "task_name": r[1], "category": r[2], "priority": r[3],
                "due_date": r[4], "due_time": r[5], "is_scheduled": r[6] == 1,
                "reasoning": r[7], "status": r[8], "recurrence": r[9]
            })
            
        # Fetch Recently Completed
        cursor.execute("SELECT id, task, category, priority, completed_at FROM todos WHERE status='Completed' ORDER BY completed_at DESC LIMIT 10")
        comp_rows = cursor.fetchall()
        completed_tasks = []
        for r in comp_rows:
            completed_tasks.append({
                "id": r[0], "task_name": r[1], "category": r[2], "priority": r[3],
                "completed_at": r[4]
            })
        
        conn.close()
        
        if obsidian_writer:
            obsidian_writer.sync_all_tasks(active_tasks, completed_tasks)
            logger.info("Obsidian full sync completed.")
            return True
        return False
            
    except Exception as e:
        logger.error(f"Sync failed: {e}")
        return False

# Main Menu Keyboard (Reply keyboard - bottom bar)
def get_main_menu_keyboard():
    keyboard = [
        ["🏁 Start", "📋 Unscheduled"],
        ["✅ Done", "📈 Stats"],
        ["🔄 Refresh Context"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Main Menu - Inline Keyboard (appears in message)
def get_inline_menu():
    """Returns an InlineKeyboardMarkup with main action buttons."""
    keyboard = [
        [InlineKeyboardButton("➕ Add Task", callback_data="menu_add"),
         InlineKeyboardButton("✏️ Edit Task", callback_data="menu_edit"),
        InlineKeyboardButton("✅ Done", callback_data="menu_done")],
        [InlineKeyboardButton("🔍 Query Vault", callback_data="menu_query")],
        [InlineKeyboardButton("📋 Unscheduled", callback_data="menu_unscheduled"),
         InlineKeyboardButton("📅 Schedule", callback_data="menu_schedule")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="menu_refresh")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    chat_id = update.effective_chat.id

    # Auto-save chat_id to user_config on first start
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM user_config WHERE chat_id = ?", (chat_id,))
        if not cursor.fetchone():
            # First time - create config
            cursor.execute(
                "INSERT INTO user_config (chat_id, check_ins_enabled) VALUES (?, 1)",
                (chat_id,)
            )
            conn.commit()
            logger.info(f"✅ Auto-configured check-ins for chat_id: {chat_id}")

            # Start scheduler now that config exists
            global check_in_scheduler
            if check_in_scheduler and not check_in_scheduler.scheduler.running:
                check_in_scheduler.start()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to setup user config: {e}")

    log_audit("command", f"/start by {user.username} ({user.id})")

    welcome_text = (
        f"Hello {user.first_name}! I am Kairos, your Intelligent Life Sorter.\n\n"
        "I'm here to help you stay aligned with your primary goals.\n\n"
        "🆕 **Hourly Check-Ins Now Active!**\n"
        "I'll ask you every hour what you did. This helps track alignment between planned tasks and actual work.\n\n"
        "Use 😴 Sleep / ☀️ Wake buttons to control quiet periods.\n\n"
        "Use the buttons below to interact!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_inline_menu())
    # Set persistent keyboard at bottom
    await update.message.reply_text("⌨️ _Quick access buttons enabled below_", parse_mode="Markdown", reply_markup=get_main_menu_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    log_audit("command", f"/help by {update.effective_user.id}")
    
    help_text = (
        "🤖 **Kairos Bot Help**\n\n"
        "➕ **Add Task**: Use `/add <task>` or the button below.\n"
        "🔍 **Query Vault**: Use `/query <question>` to ask about your vault.\n"
        "📋 **Unscheduled**: Use `/unscheduled` to list tasks needing a date.\n"
        "📅 **Schedule**: Use `/schedule <id> <date> [time]` to schedule a task.\n"
        "🔄 **Refresh**: Use `/refresh_context` to update my context from your vault.\n"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=get_main_menu_keyboard())

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show daily productivity statistics."""
    log_audit("command", f"/stats by {update.effective_user.id}")

    report = productivity_reporter.format_daily_report()

    await update.message.reply_text(
        report,
        parse_mode="Markdown",
        reply_markup=get_main_menu_keyboard()
    )

async def query_task_db(search_term: str):
    """Helper to search pending tasks by keyword."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, task FROM todos WHERE status='Pending' AND task LIKE ? ORDER BY created_at DESC LIMIT 5", (f"%{search_term}%",))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logger.error(f"Search failed: {e}")
        return []

async def refresh_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggers a deep vault scan."""
    log_audit("command", f"/refresh_context by {update.effective_user.id}")
    await update.message.reply_text("🔍 Starting deep vault scan... this take a minute.")
    
    result = await context_manager.generate_context_map()
    
    # Trigger full sync to Obsidian
    sync_success = await execute_full_sync()

    if result and sync_success:
        await update.message.reply_text("✅ Context update & Obsidian Sync complete!", reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text("⚠️ Context refresh failed or Sync failed.", reply_markup=get_main_menu_keyboard())

async def add_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Explicitly triage a new task."""
    text = " ".join(context.args) if context.args else ""
    if not text:
        # Two-step flow: ask for task in next message
        context.user_data["state"] = "AWAITING_ADD_TASK"
        await update.message.reply_text(
            "➕ **What task would you like to add?**\n\n_Send me the task description (e.g., 'Submit application by Friday')_",
            parse_mode="Markdown", reply_markup=get_main_menu_keyboard()
        )
        return
    await process_task(update, context, text)

async def query_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Query the vault context and recent tasks."""
    query_text = " ".join(context.args) if context.args else ""
    if not query_text:
        # Two-step flow: ask for question in next message
        context.user_data["state"] = "AWAITING_QUERY"
        await update.message.reply_text(
            "🔍 **What would you like to know?**\n\n_Ask me about your goals, recent tasks, or vault content._",
            parse_mode="Markdown", reply_markup=get_main_menu_keyboard()
        )
        return

    log_audit("query", f"User {update.effective_user.id}: {query_text}")
    status_msg = await update.message.reply_text("🔍 Searching personal knowledge base...")
    
    # 1. Load Vault Context
    vault_context = triage_engine._load_context()
    
    # 2. Load Recent Tasks from DB
    db_context = "No recent tasks found in database."
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT task, category, priority, due_date, reasoning FROM todos ORDER BY created_at DESC LIMIT 10")
        rows = cursor.fetchall()
        conn.close()
        if rows:
            tasks = [f"- {r[0]} (Priority: {r[2]}, Due: {r[3]})" for r in rows]
            db_context = "RECENT TASKS FROM DATABASE:\n" + "\n".join(tasks)
    except Exception as e:
        logger.error(f"DB Query failed: {e}")

    prompt = f"""
You are Kairos, a strategic advisor. Answer the following question based on the user's vault context and recent tasks.

VAULT CONTEXT:
{vault_context}

{db_context}

USER QUESTION:
{query_text}

Provide a concise, helpful answer. If the answer is in the recent tasks, highlight that.
"""
    response = triage_engine.model.generate_content(prompt)
    await status_msg.edit_text(response.text, reply_markup=get_main_menu_keyboard())

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle raw text based on current state or shortcuts."""
    text = update.message.text
    user_data = context.user_data
    current_state = user_data.get("state")

    # PRIORITY 1: Check for pending check-in response
    # Only if user is NOT in another conversation flow
    if not current_state and check_in_manager:
        pending_check_in_id = check_in_manager.get_pending_check_in()

        if pending_check_in_id:
            # User is responding to check-in
            status_msg = await update.message.reply_text("🔍 Analyzing your activity...")

            try:
                analysis = await activity_analyzer.analyze_activity(text, pending_check_in_id)

                # Clear pending check-in
                check_in_manager.clear_pending_check_in()

                # Emoji mapping
                emoji_map = {
                    'aligned': '✅',
                    'beneficial': '💡',
                    'wasted': '⚠️',
                    'missed': '❌',
                    'sleeping': '😴'
                }
                emoji = emoji_map.get(analysis['productivity_type'], '📝')

                response = (
                    f"{emoji} **Activity Logged**\n\n"
                    f"**Summary:** {analysis['activity_summary']}\n"
                    f"**Type:** {analysis['productivity_type'].title()}\n"
                    f"**Alignment Score:** {analysis['alignment_score']}/10\n"
                    f"**Category:** {analysis['category']}\n\n"
                    f"💬 {analysis['feedback']}"
                )

                if analysis.get('matched_todo_id'):
                    response += f"\n\n✓ Matched to Task ID: {analysis['matched_todo_id']}"

                await status_msg.edit_text(response, parse_mode="Markdown")
                log_audit("activity_logged", f"Check-in {pending_check_in_id}: {text[:50]}")
                return

            except Exception as e:
                logger.error(f"Activity analysis failed: {e}")
                await status_msg.edit_text("✅ Activity logged. Analysis pending.")
                check_in_manager.clear_pending_check_in()
                return

    # PRIORITY 2: Check for keyboard shortcuts (these always work regardless of state)
    if text == "✅ Done":
        # Trigger same logic as /done command (interactive)
        context.args = [] # No ID provided
        await done_command(update, context)
        return
    elif text == "🏁 Start":
        await start(update, context)
        return
    elif text == "📋 Unscheduled":
        await list_unscheduled_command(update, context)
        return
    elif text == "🔄 Refresh Context":
        user_data["state"] = None
        await refresh_context(update, context)
        return
    elif text == "📈 Stats":
        user_data["state"] = None
        await stats_command(update, context)
        return

    # Handle state-based input
    if current_state == "AWAITING_ADD_TASK":
        user_data["state"] = None
        await process_task(update, context, text)
        return

    if current_state == "AWAITING_DONE_ID":
        task_id = text.strip()
        # Verify ID exists first
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT task FROM todos WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            await update.message.reply_text(f"❌ Task ID {task_id} not found.", reply_markup=get_inline_menu())
            user_data["state"] = None
            return

        # Ask for completion time
        user_data["pending_done_id"] = task_id
        user_data["state"] = None # Clear state so buttons work
        
        keyboard = [
            [InlineKeyboardButton("✅ Completed Now", callback_data=f"complete_now_{task_id}"),
             InlineKeyboardButton("📝 Custom Time", callback_data=f"complete_custom_{task_id}")]
        ]
        await update.message.reply_text(
            f"✅ Found: **{row[0]}**\n\n🕐 **When did you complete it?**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if current_state == "AWAITING_DONE_SEARCH":
        user_data["state"] = None
        search_query = text
        rows = await query_task_db(search_query)
        
        if not rows:
            await update.message.reply_text(f"❌ No pending tasks found matching '{search_query}'.", reply_markup=get_inline_menu())
            return
            
        keyboard = []
        for r in rows:
            # r = (id, task)
            btn_text = f"{r[1]} [ID: {r[0]}]"
            # Truncate if too long
            if len(btn_text) > 40:
                btn_text = btn_text[:37] + "..."
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"done_task_{row[0]}")])
            
        await update.message.reply_text(
            f"🔍 **Search Results for '{search_query}':**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # --- Edit States ---
    if current_state == "AWAITING_EDIT_ID":
        task_id = text.strip()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT task FROM todos WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            await update.message.reply_text(f"❌ Task ID {task_id} not found.", reply_markup=get_main_menu_keyboard())
            user_data["state"] = None
            return
            
        user_data["pending_edit_id"] = task_id
        user_data["state"] = "AWAITING_EDIT_INSTRUCTION"
        await update.message.reply_text(
            f"✏️ **Editing Task {task_id}:** {row[0]}\n\nTell me your edits (e.g., 'Change priority to HIGH', 'due friday')",
            parse_mode="Markdown"
        )
        return

    if current_state == "AWAITING_EDIT_SEARCH":
        user_data["state"] = None
        search_query = text
        rows = await query_task_db(search_query)
        
        if not rows:
            await update.message.reply_text(f"❌ No pending tasks found matching '{search_query}'.", reply_markup=get_inline_menu())
            return
            
        keyboard = []
        for r in rows:
            btn_text = f"{r[1]} [ID: {r[0]}]"
            if len(btn_text) > 40:
                btn_text = btn_text[:37] + "..."
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"edit_task_{r[0]}")])
            
        await update.message.reply_text(
            f"🔍 **Select Task to Edit:**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if current_state == "AWAITING_EDIT_INSTRUCTION":
        user_data["state"] = None
        task_id = user_data.get("pending_edit_id")
        if not task_id:
            await update.message.reply_text("❌ Error: Lost task ID.", reply_markup=get_main_menu_keyboard())
            return
            
        instruction = text
        # Fetch original task for context
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT raw_input FROM todos WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            combined_text = f"Original task: {row[0]}\nEdit instruction: {instruction}"
            await update.message.reply_text(f"🔄 Applying edit to Task {task_id}...")
            await process_task(update, context, combined_text, update_id=int(task_id))
        else:
             await update.message.reply_text(f"❌ Task ID {task_id} not found.", reply_markup=get_main_menu_keyboard())
        return

    if current_state == "AWAITING_CUSTOM_COMPLETE_TIME":
        task_id = user_data.get("pending_done_id")
        if task_id:
            await mark_task_complete(update, context, task_id, text.strip())
        else:
            await update.message.reply_text("❌ Session expired. Please start again.", reply_markup=get_inline_menu())
        return
    
    if current_state == "AWAITING_QUERY":
        user_data["state"] = None
        # Process the query (reuse logic from query_command)
        log_audit("query", f"User {update.effective_user.id}: {text}")
        status_msg = await update.message.reply_text("🔍 Searching personal knowledge base...")
        vault_context = triage_engine._load_context()
        db_context = "No recent tasks found in database."
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT task, priority, due_date FROM todos ORDER BY created_at DESC LIMIT 5")
            rows = cursor.fetchall()
            conn.close()
            if rows:
                db_context = "RECENT TASKS:\n" + "\n".join([f"- [{r[1]}] {r[0]} (Due: {r[2] or 'Unscheduled'})" for r in rows])
        except Exception as e:
            logger.error(f"DB Query failed: {e}")
        
        prompt = f"""You are Kairos, a strategic advisor. Answer the following question based on the user's vault context and recent tasks.
VAULT CONTEXT:\n{vault_context}\n{db_context}\nUSER QUESTION:\n{text}\nProvide a concise, helpful answer."""
        response = triage_engine.model.generate_content(prompt)
        await status_msg.edit_text(response.text, reply_markup=get_main_menu_keyboard())
        return
    
    if current_state == "AWAITING_SCHEDULE":
        # Parse schedule input: "<id> <date> [time]"
        user_data["state"] = None
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text("❌ Please provide: `<task_id> <date>`\nExample: `15 Friday 3pm`", parse_mode="Markdown")
            return
        # Reuse schedule logic
        context.args = parts  # Simulate args for schedule_task_command
        await schedule_task_command(update, context)
        return

    # Check for Clarification State
    if current_state == "AWAITING_CLARIFICATION":
        todo_id = user_data.get("pending_todo_id")
        await process_clarification(update, context, todo_id, text)
        return

    # Default: if it looks like they tried to add a task, prompt them
    if len(text.split()) > 3:
        await update.message.reply_text(
            "💡 It looks like you want to add a task. Tap **➕ Add Task** or type `/add`",
            parse_mode="Markdown", reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "I didn't quite get that. Use the menu button (/) or tap a button below.",
            reply_markup=get_main_menu_keyboard()
        )

async def process_task(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, update_id: int = None):
    """Common logic for triaging a task. Supports updating existing tasks."""
    status_msg = await update.message.reply_text("🤔 Analyzing task...")
    triage = await triage_engine.triage_task(text)
    
    # Determine scheduling status
    has_due_date = triage.get("due_date") is not None
    is_scheduled = 1 if has_due_date else 0
    
    # Add scheduling clarification if no date provided and no other clarification pending
    if not has_due_date and not triage.get("clarification_needed") and not triage.get("scheduling_unclear"):
        triage["clarification_needed"] = "When would you like to complete this? Give me a date (e.g., 'Friday'), date+time (e.g., 'tomorrow at 3pm'), or say 'unscheduled' to add to backlog."
    
    # DB Logic: Update or Insert
    todo_id = update_id
    try:
        conn = get_connection()
        cursor = conn.cursor()
        if update_id:
            cursor.execute(
                "UPDATE todos SET task=?, raw_input=?, category=?, priority=?, due_date=?, due_time=?, is_scheduled=?, reasoning=?, recurrence=?, status='Pending', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (triage.get("task_name", text), text, triage.get("category"), triage.get("priority"), triage.get("due_date"), triage.get("due_time"), is_scheduled, triage.get("reasoning"), triage.get("recurrence"), update_id)
            )
        else:
            cursor.execute(
                "INSERT INTO todos (task, raw_input, category, priority, due_date, due_time, is_scheduled, reasoning, status, recurrence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (triage.get("task_name", text), text, triage.get("category"), triage.get("priority"), triage.get("due_date"), triage.get("due_time"), is_scheduled, triage.get("reasoning"), "Pending", triage.get("recurrence"))
            )
            todo_id = cursor.lastrowid
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to save/update todos: {e}")

    # Format response with user-friendly date/time
    is_update = "Updated" if update_id else "Captured"
    due_display = format_due_date_display(triage.get("due_date"), triage.get("due_time"), is_scheduled == 1)
    
    response_parts = [
        f"✅ **Task {is_update} [ID: {todo_id}]**: {triage.get('task_name')}",
        f"📊 **Priority**: {triage.get('priority')}",
        f"📂 **Category**: {triage.get('category')}",
        f"📅 **Due**: {due_display}",
    ]
    if triage.get("recurrence"):
        response_parts.append(f"🔁 **Recurrence**: {triage.get('recurrence')}")
    
    # Prepare triage data for Obsidian sync with is_scheduled
    triage["is_scheduled"] = is_scheduled == 1
    triage["id"] = todo_id
    
    # Sync to Obsidian
    synced = False
    if obsidian_writer and triage.get("priority") != "LOW" and not triage.get("clarification_needed"):
        if update_id:
            # For updates, perform full sync to avoid duplicates
            await execute_full_sync()
            response_parts.append("📝 *Synced to Obsidian (Updated)*")
            synced = True
        else:
            # For new tasks, safe to append
            if obsidian_writer.append_task(triage):
                response_parts.append("📝 *Synced to Obsidian*")
                synced = True

    response_parts.append(f"\n**Reasoning**: {triage.get('reasoning')}")
    
    keyboard = []
    if triage.get("pushback"):
        response_parts.append(f"\n✋ **Pushback**: {triage.get('pushback')}")
        if triage.get("priority") == "LOW":
            keyboard.append([InlineKeyboardButton("🚀 Force Sync", callback_data=f"sync_{todo_id}")])
    
    if triage.get("suggested_alternative"):
        response_parts.append(f"\n💡 **Alternative**: {triage.get('suggested_alternative')}")

    if triage.get("clarification_needed"):
        response_parts.append(f"\n❓ **Clarification**: {triage.get('clarification_needed')}")
        context.user_data["state"] = "AWAITING_CLARIFICATION"
        context.user_data["pending_todo_id"] = todo_id
        response_parts.append(f"\n_(I'm waiting for your reply regarding Task {todo_id})_")

    inline_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    inline_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await status_msg.edit_text("\n".join(response_parts), parse_mode="Markdown", reply_markup=inline_markup)
    # await update.message.reply_text("Options:", reply_markup=get_main_menu_keyboard())

async def process_clarification(update: Update, context: ContextTypes.DEFAULT_TYPE, todo_id: int, reply: str):
    """Combines a reply with a pending task and updates it."""
    log_audit("clarification", f"Replying to {todo_id}: {reply}")
    context.user_data["state"] = None
    context.user_data["pending_todo_id"] = None
    
    # Check if user EXPLICITLY wants to mark as unscheduled
    # Only match if the reply is primarily about not scheduling (not just contains "not sure" in a date context)
    reply_lower = reply.lower().strip()
    explicit_unscheduled = [
        "unscheduled", "no date", "backlog", "no deadline", 
        "add to backlog", "put in backlog", "skip scheduling",
        "don't schedule", "no due date"
    ]
    
    # Only trigger if the reply is SHORT and matches unscheduled intent
    # Avoid triggering on "I'll do it tomorrow, but not sure about the time"
    is_unscheduled_intent = (
        len(reply_lower.split()) <= 5 and 
        any(kw in reply_lower for kw in explicit_unscheduled)
    )
    
    if is_unscheduled_intent:
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE todos SET is_scheduled=0, due_date=NULL, due_time=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?", (todo_id,))
            conn.commit()
            
            # Fetch task details for Obsidian sync
            cursor.execute("SELECT task, category, priority, reasoning FROM todos WHERE id=?", (todo_id,))
            row = cursor.fetchone()
            conn.close()
            
            # Sync to Obsidian ONLY HERE (final state)
            if row and obsidian_writer:
                triage_data = {
                    "id": todo_id,
                    "task_name": row[0], "category": row[1], "priority": row[2],
                    "due_date": None, "due_time": None, "is_scheduled": False, "reasoning": row[3]
                }
                obsidian_writer.append_task(triage_data)
            
            await update.message.reply_text(
                f"📋 **Task {todo_id} moved to Unscheduled backlog.**\nUse `/schedule {todo_id} <date> [time]` when you're ready to schedule it.",
                parse_mode="Markdown", reply_markup=get_main_menu_keyboard()
            )
            return
        except Exception as e:
            logger.error(f"Failed to mark as unscheduled: {e}")
    
    # Otherwise, re-triage with the clarification info
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT raw_input FROM todos WHERE id = ?", (todo_id,))
        original_input = cursor.fetchone()[0]
        conn.close()
        
        combined_text = f"Original task: {original_input}\nClarification info: {reply}"
        await update.message.reply_text(f"🔄 Refining Task {todo_id} with your reply...")
        await process_task(update, context, combined_text, update_id=todo_id)
    except Exception as e:
        logger.error(f"Clarification failed: {e}")
        await update.message.reply_text("❌ Error updating task.", reply_markup=get_main_menu_keyboard())

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Handle Sleep/Wake buttons (add at start)
    if query.data == "checkin_sleep":
        await check_in_manager.handle_sleep_button(update.effective_chat.id)
        await query.message.reply_text(
            "😴 **Sleep mode activated**\n\n"
            "I'll pause check-ins until you press ☀️ Wake.\n"
            "Sleep well!",
            parse_mode="Markdown"
        )
        return

    elif query.data == "checkin_wake":
        hours_slept = await check_in_manager.handle_wake_button(update.effective_chat.id)
        await query.message.reply_text(
            f"☀️ **Welcome back!**\n\n"
            f"You slept for ~{hours_slept} hours.\n"
            f"Check-ins resumed. Let's make today count!",
            parse_mode="Markdown"
        )
        return

    # Handle inline menu buttons
    if query.data == "menu_add":
        context.user_data["state"] = "AWAITING_ADD_TASK"
        await query.message.reply_text(
            "➕ **What task would you like to add?**\n\n_Send me the task description (e.g., 'Submit application by Friday')_",
            parse_mode="Markdown"
        )
        return
    elif query.data == "menu_query":
        context.user_data["state"] = "AWAITING_QUERY"
        await query.message.reply_text(
            "🔍 **What would you like to know?**\n\n_Ask me about your goals, recent tasks, or vault content._",
            parse_mode="Markdown"
        )
        return
    elif query.data == "menu_unscheduled":
        # Fetch and display unscheduled tasks
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, task, priority, category FROM todos WHERE is_scheduled=0 AND status='Pending' ORDER BY created_at DESC")
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                await query.message.reply_text("✅ No unscheduled tasks! All tasks have due dates.", reply_markup=get_inline_menu())
                return
            
            response = "📋 **Unscheduled Tasks (Backlog)**\n\n"
            for row in rows:
                response += f"**[ID: {row[0]}]** {row[1]}\n   └─ {row[3]} | {row[2]} priority\n\n"
            
            response += "_Use `/schedule <id> <date> [time]` to schedule a task._"
            await query.message.reply_text(response, parse_mode="Markdown", reply_markup=get_inline_menu())
        except Exception as e:
            logger.error(f"Failed to list unscheduled: {e}")
        return
    elif query.data == "menu_schedule":
        context.user_data["state"] = "AWAITING_SCHEDULE"
        await query.message.reply_text(
            "📅 **Schedule a Task**\n\n"
            "Send me the task ID and date:\n"
            "`<task_id> <date> [time]`\n\n"
            "Examples:\n• `30 Friday`\n• `30 tomorrow 3pm`",
            parse_mode="Markdown"
        )
        return
    elif query.data == "menu_done":
        # Done button - show options to enter ID or search
        keyboard = [
            [InlineKeyboardButton("📝 Enter Task ID", callback_data="done_enter_id"),
             InlineKeyboardButton("🔍 Search Tasks", callback_data="done_search")]
        ]
        await query.message.reply_text(
            "✅ **Mark Task as Complete**\n\n"
            "Which task did you complete?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # --- Edit Button Handlers ---
    elif query.data == "menu_edit":
        context.user_data["state"] = None
        keyboard = [
            [InlineKeyboardButton("📝 Enter Task ID", callback_data="edit_enter_id"),
             InlineKeyboardButton("🔍 Search Tasks", callback_data="edit_search")]
        ]
        await query.message.reply_text(
            "✏️ **Edit Task**\n\n"
            "Which task do you want to edit?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return 
    elif query.data == "edit_enter_id":
        context.user_data["state"] = "AWAITING_EDIT_ID"
        await query.message.reply_text(
            "📝 **Enter Task ID to Edit**:\n\n_Send the number (e.g., 21)_",
            parse_mode="Markdown"
        )
        return
    elif query.data == "edit_search":
        context.user_data["state"] = "AWAITING_EDIT_SEARCH"
        await query.message.reply_text(
            "🔍 **Search Task to Edit**:\n\n_Type a keyword (e.g., 'call', 'apply')_",
            parse_mode="Markdown"
        )
        return
    elif query.data.startswith("edit_task_"):
        task_id = query.data.split("_")[2]
        context.user_data["pending_edit_id"] = task_id
        context.user_data["state"] = "AWAITING_EDIT_INSTRUCTION"
        
        # Fetch task name
        task_name = "Task"
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT task FROM todos WHERE id=?", (task_id,))
            row = cursor.fetchone()
            if row:
                task_name = row[0]
            conn.close()
        except:
            pass

        await query.message.reply_text(
            f"✏️ **Editing: {task_name} [ID: {task_id}]**\n\n"
            "Tell me your edits (e.g., 'Change priority to HIGH', 'due friday')",
            parse_mode="Markdown"
        )
        return

    elif query.data == "done_enter_id":
        context.user_data["state"] = "AWAITING_DONE_ID"
        await query.message.reply_text(
            "📝 **Enter the Task ID** you completed:\n\n"
            "_Send the number (e.g., 21)_",
            parse_mode="Markdown"
        )
        return
    elif query.data == "done_search":
        context.user_data["state"] = "AWAITING_DONE_SEARCH"
        await query.message.reply_text(
            "🔍 **Search for your task**\n\n"
            "_Type a keyword to search (e.g., 'call', 'apply')_",
            parse_mode="Markdown"
        )
        return
    elif query.data.startswith("done_task_"):
        # User selected a task from search results
        task_id = query.data.split("_")[2]
        context.user_data["pending_done_id"] = task_id
        keyboard = [
            [InlineKeyboardButton("✅ Completed Now", callback_data=f"complete_now_{task_id}"),
             InlineKeyboardButton("📝 Custom Time", callback_data=f"complete_custom_{task_id}")]
        ]
        await query.message.reply_text(
            f"🕐 **When did you complete Task ID: {task_id}?**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    elif query.data.startswith("complete_now_"):
        task_id = query.data.split("_")[2]
        # Mark complete with current timestamp
        await mark_task_complete(query, context, task_id, None)
        return
    elif query.data.startswith("complete_custom_"):
        task_id = query.data.split("_")[2]
        context.user_data["state"] = "AWAITING_CUSTOM_COMPLETE_TIME"
        context.user_data["pending_done_id"] = task_id
        await query.message.reply_text(
            "📝 **When was it completed?**\n\n"
            "_Send the date/time (e.g., 'yesterday 3pm', 'Jan 28 2pm')_",
            parse_mode="Markdown"
        )
        return
    elif query.data == "menu_stats":
        await query.message.reply_text("📈 Stats feature coming soon in Phase 4!", reply_markup=get_inline_menu())
        return
    elif query.data == "menu_refresh":
        await query.message.reply_text("🔍 Starting deep vault scan & full sync... this may take a minute.")
        # 1. Update Context
        result = await context_manager.generate_context_map()
        # 2. Full Obsidian Sync
        sync_success = await execute_full_sync()
        if result and sync_success:
            await query.message.reply_text("✅ Context update & Obsidian Sync complete!", reply_markup=get_inline_menu())
        else:
            await query.message.reply_text("⚠️ Context refresh failed or Sync failed.", reply_markup=get_inline_menu())
        return

    # Force Sync Button Handler
    elif query.data.startswith("sync_"):
        todo_id = query.data.split("_")[1]
        try:
            # Trigger full sync
            await execute_full_sync()
            await query.message.reply_text("🚀 **Force Sync Complete!**", reply_markup=get_inline_menu())
        except Exception as e:
            logger.error(f"Force sync failed: {e}")
            await query.message.reply_text("❌ Sync failed.", reply_markup=get_inline_menu())
        return
        return
    elif query.data == "menu_stats":
        await query.message.reply_text("📈 Stats feature coming soon in Phase 4!", reply_markup=get_inline_menu())
        return
    elif query.data == "menu_refresh":
        await query.message.reply_text("🔍 Starting deep vault scan & full sync... this may take a minute.")
        
        # 1. Update Context
        result = await context_manager.generate_context_map()
        
        # 2. Full Obsidian Sync
        sync_success = await execute_full_sync()
        
        if result and sync_success:
            await query.message.reply_text("✅ Context update & Obsidian Sync complete!", reply_markup=get_inline_menu())
        else:
            await query.message.reply_text("⚠️ Context refresh failed or Sync failed.", reply_markup=get_inline_menu())
        return
    
    # Handle sync buttons
    if query.data.startswith("sync_"):
        todo_id = query.data.split("_")[1]
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT task, category, priority, due_date, due_time, is_scheduled, reasoning FROM todos WHERE id = ?", (todo_id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                # Use sync_all_tasks logic instead of append to preserve file structure
                cursor.execute("SELECT id, task, category, priority, due_date, due_time, is_scheduled, reasoning, status, recurrence FROM todos WHERE status='Pending' ORDER BY created_at DESC")
                active_rows = cursor.fetchall()
                active_tasks = []
                for r in active_rows:
                    active_tasks.append({
                        "id": r[0], "task_name": r[1], "category": r[2], "priority": r[3],
                        "due_date": r[4], "due_time": r[5], "is_scheduled": r[6] == 1,
                        "reasoning": r[7], "status": r[8], "recurrence": r[9]
                    })
                    
                cursor.execute("SELECT id, task, category, priority, completed_at FROM todos WHERE status='Completed' ORDER BY completed_at DESC LIMIT 10")
                comp_rows = cursor.fetchall()
                completed_tasks = []
                for r in comp_rows:
                    completed_tasks.append({
                        "id": r[0], "task_name": r[1], "category": r[2], "priority": r[3],
                        "completed_at": r[4]
                    })
                
                if obsidian_writer:
                    obsidian_writer.sync_all_tasks(active_tasks, completed_tasks)
                    await query.edit_message_text(text=query.message.text + "\n\n🚀 **Force Synced!**", parse_mode="Markdown")
                    await triage_engine.pm.analyze_overrides()
        except Exception as e:
            logger.error(f"Force sync failed: {e}")

# --- New Commands for Scheduling ---
async def list_unscheduled_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all unscheduled tasks."""
    log_audit("command", f"/unscheduled by {update.effective_user.id}")
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, task, priority, category FROM todos WHERE is_scheduled=0 AND status='Pending' ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            await update.message.reply_text("✅ No unscheduled tasks! All tasks have due dates.", reply_markup=get_main_menu_keyboard())
            return
        
        response = "📋 **Unscheduled Tasks (Backlog)**\n\n"
        for row in rows:
            response += f"**[ID: {row[0]}]** {row[1]}\n   └─ {row[3]} | {row[2]} priority\n\n"
        
        response += "_Use `/schedule <id> <date> [time]` to schedule a task._"
        await update.message.reply_text(response, parse_mode="Markdown", reply_markup=get_main_menu_keyboard())
    except Exception as e:
        logger.error(f"Failed to list unscheduled: {e}")
        await update.message.reply_text("❌ Error fetching unscheduled tasks.", reply_markup=get_main_menu_keyboard())

async def schedule_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Schedule an unscheduled task: /schedule <id> <date> [time]"""
    log_audit("command", f"/schedule by {update.effective_user.id}")
    args = context.args
    
    if not args or len(args) < 2:
        # Two-step flow: ask for schedule details in next message
        context.user_data["state"] = "AWAITING_SCHEDULE"
        await update.message.reply_text(
            "📅 **Schedule a Task**\n\n"
            "Send me the task ID and date:\n"
            "`<task_id> <date> [time]`\n\n"
            "Examples:\n"
            "• `30 Friday`\n"
            "• `30 tomorrow 3pm`\n"
            "• `30 2026-02-01 14:00`",
            parse_mode="Markdown", reply_markup=get_main_menu_keyboard()
        )
        return
    
    try:
        todo_id = int(args[0])
        date_time_str = " ".join(args[1:])
        
        # Let Gemini parse the natural language date/time
        parse_prompt = f"""
Parse the following date/time string and return JSON:
Input: "{date_time_str}"
Current date: {datetime.now().strftime('%Y-%m-%d')}

Return ONLY valid JSON:
{{
  "due_date": "YYYY-MM-DD",
  "due_time": "HH:MM" or null if no time specified
}}
"""
        response = triage_engine.model.generate_content(parse_prompt)
        text = response.text
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()
        
        parsed = json.loads(text)
        due_date = parsed.get("due_date")
        due_time = parsed.get("due_time")
        
        if not due_date:
            await update.message.reply_text("❌ Couldn't parse that date. Try: `/schedule 30 2026-02-01`", parse_mode="Markdown")
            return
        
        # Update DB
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE todos SET due_date=?, due_time=?, is_scheduled=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (due_date, due_time, todo_id)
        )
        if cursor.rowcount == 0:
            await update.message.reply_text(f"❌ Task ID {todo_id} not found.", reply_markup=get_main_menu_keyboard())
            conn.close()
            return
        conn.commit()
        conn.close()
        
        due_display = format_due_date_display(due_date, due_time, True)
        await update.message.reply_text(
            f"✅ **Task {todo_id} scheduled!**\n📅 Due: {due_display}",
            parse_mode="Markdown", reply_markup=get_main_menu_keyboard()
        )
    except ValueError:
        await update.message.reply_text("❌ Invalid task ID. Usage: `/schedule <id> <date>`", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Schedule command failed: {e}")
        await update.message.reply_text("❌ Error scheduling task.", reply_markup=get_main_menu_keyboard())

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /done command."""
    if not context.args:
        # Show interactive options
        context.user_data["state"] = None
        keyboard = [
            [InlineKeyboardButton("📝 Enter Task ID", callback_data="done_enter_id"),
             InlineKeyboardButton("🔍 Search Tasks", callback_data="done_search")]
        ]
        await update.message.reply_text(
            "✅ **Mark Task as Complete**\n\n"
            "Which task did you complete? Use buttons below or `/done <id>`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Handle direct ID: /done 21
    task_id = context.args[0]
    
    # Verify ID exists first
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT task FROM todos WHERE id = ?", (task_id,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        await update.message.reply_text(f"❌ Task ID {task_id} not found.", reply_markup=get_inline_menu())
        return

    # Ask for completion time
    context.user_data["pending_done_id"] = task_id
    
    keyboard = [
        [InlineKeyboardButton("✅ Completed Now", callback_data=f"complete_now_{task_id}"),
         InlineKeyboardButton("📝 Custom Time", callback_data=f"complete_custom_{task_id}")]
    ]
    await update.message.reply_text(
        f"✅ Found: **{row[0]}**\n\n🕐 **When did you complete it?**",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /edit command: /edit <id> <instruction> OR interactive mode."""
    log_audit("command", f"/edit by {update.effective_user.id}")
    args = context.args
    
    if not args:
        # Interactive Mode
        context.user_data["state"] = None
        keyboard = [
            [InlineKeyboardButton("📝 Enter Task ID", callback_data="edit_enter_id"),
             InlineKeyboardButton("🔍 Search Tasks", callback_data="edit_search")]
        ]
        await update.message.reply_text(
            "✏️ **Edit Task**\n\n"
            "Which task do you want to edit?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: `/edit <id> <instruction>` or `/edit` (interactive)", parse_mode="Markdown")
        return

    task_id = context.args[0]
    instruction = " ".join(context.args[1:])
    
    # Check if task exists
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT raw_input FROM todos WHERE id = ?", (task_id,))
    row = cursor.fetchone()
    conn.close() # Close connection explicitly
    
    if not row:
        await update.message.reply_text(f"❌ Task ID {task_id} not found.", reply_markup=get_main_menu_keyboard())
        return

    combined_text = f"Original task: {row[0]}\nEdit instruction: {instruction}"
    await update.message.reply_text(f"🔄 Applying edit to Task {task_id}...")
    await process_task(update, context, combined_text, update_id=int(task_id))
    
    # 1. Try simple key=value parsing first
    if "=" in instruction and " " not in instruction: # Simple case like priority=HIGH
        try:
            key, val = instruction.split("=", 1)
            edits[key.lower()] = val
        except:
            pass
            
    # 2. If not simple, use LLM
    if not edits:
        msg = await update.message.reply_text("🤔 Parsing edits...")
        edits = await triage_engine.parse_edit_request(instruction)
        await msg.delete()
    
    if not edits:
        await update.message.reply_text("❌ Could not understand the edit request.", reply_markup=get_inline_menu())
        conn.close()
        return

    # Apply updates
    fields = []
    values = []
    
    valid_fields = ["task_name", "category", "priority", "due_date", "due_time", "reasoning"]
    field_map = {"task_name": "task"} # Map JSON field to DB column if different
    
    for k, v in edits.items():
        db_col = field_map.get(k, k)
        if db_col in ["task", "category", "priority", "due_date", "due_time", "reasoning"]:
            fields.append(f"{db_col}=?")
            values.append(v)
            
    if not fields:
        await update.message.reply_text("❌ No valid fields to update.", reply_markup=get_inline_menu())
        conn.close()
        return
        
    values.append(task_id) # For WHERE clause
    
    try:
        query = f"UPDATE todos SET {', '.join(fields)}, updated_at=CURRENT_TIMESTAMP WHERE id=?"
        cursor.execute(query, tuple(values))
        conn.commit()
        
        # Fetch updated row for sync
        cursor.execute("SELECT task, category, priority, due_date, due_time, is_scheduled, reasoning FROM todos WHERE id = ?", (task_id,))
        new_row = cursor.fetchone()
        conn.close()
        
        # Sync to Obsidian
        if new_row and obsidian_writer:
             triage_data = {
                "task_name": new_row[0], "category": new_row[1], "priority": new_row[2],
                "due_date": new_row[3], "due_time": new_row[4], "is_scheduled": new_row[5] == 1, "reasoning": new_row[6]
            }
             # Note: append_task appends. Ideally we should update. 
             # For now, append_task will add a new entry. In a real update, we might need a dedicated update method in ObsidianWriter.
             # Given current scope, we'll re-append with a note or just accept append.
             # Better: just append as a "Correction" or "Update".
             obsidian_writer.append_task(triage_data)

        # Confirm to user
        changes = "\n".join([f"• {k}: {v}" for k, v in edits.items()])
        await update.message.reply_text(f"✅ **Task {task_id} Updated!**\n{changes}", parse_mode="Markdown", reply_markup=get_inline_menu())
        
    except Exception as e:
        logger.error(f"Edit failed: {e}")
        await update.message.reply_text(f"❌ Edit failed: {e}", reply_markup=get_inline_menu())

async def handle_multimodal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.message.voice:
        file_id, ext, msg_type = update.message.voice.file_id, "ogg", "voice"
    elif update.message.photo:
        file_id, ext, msg_type = update.message.photo[-1].file_id, "jpg", "photo"
    else: return
    log_audit(f"message_{msg_type}", f"User {user_id}")
    status_msg = await update.message.reply_text(f"📥 Received {msg_type}. Processing...")
    file = await context.bot.get_file(file_id)
    save_path = get_temp_path(file_id, ext)
    await file.download_to_drive(save_path)
    await status_msg.edit_text(f"✅ {msg_type.capitalize()} saved.", reply_markup=get_main_menu_keyboard())

async def post_init(application):
    """Set up bot commands menu after initialization."""
    commands = [
        BotCommand("start", "Start the bot and show menu"),
        BotCommand("add", "Add a new task"),
        BotCommand("edit", "Edit task: /edit <id> <change>"),
        BotCommand("query", "Query your vault"),
        BotCommand("done", "Mark task complete: /done <id>"),
        BotCommand("unscheduled", "View unscheduled tasks"),
        BotCommand("schedule", "Schedule a task: /schedule <id> <date>"),
        BotCommand("stats", "View productivity statistics"),
        BotCommand("help", "Show help information"),
        BotCommand("refresh_context", "Refresh vault context"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands menu set successfully")

    # Initialize check-in system
    global check_in_manager, check_in_scheduler
    check_in_manager = CheckInManager(application.bot)
    check_in_scheduler = CheckInScheduler(application, check_in_manager)

    # Scheduler will auto-start if user_config exists
    if check_in_scheduler.start():
        logger.info("✅ Check-in scheduler started")
    else:
        logger.info("⚠️ Check-in scheduler waiting for user setup")

def main():
    ensure_dirs()
    if not TOKEN: return
    application = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("refresh_context", refresh_context))
    application.add_handler(CommandHandler("add", add_task_command))
    application.add_handler(CommandHandler("query", query_command))
    application.add_handler(CommandHandler("unscheduled", list_unscheduled_command))
    application.add_handler(CommandHandler("unscheduled", list_unscheduled_command))
    application.add_handler(CommandHandler("schedule", schedule_task_command))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(CommandHandler("edit", edit_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.add_handler(MessageHandler(filters.VOICE | filters.PHOTO, handle_multimodal))
    logger.info("Kairos Bot starting (v5.1-menu)...")
    application.run_polling()

if __name__ == "__main__":
    main()
