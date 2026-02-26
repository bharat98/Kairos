"""
CheckInManager: Manages sending check-ins and handling Sleep/Wake buttons
"""
import logging
from datetime import datetime, timedelta, time as dt_time
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from src.database import get_connection

logger = logging.getLogger(__name__)

class CheckInManager:
    def __init__(self, bot):
        self.bot = bot
        self.pending_check_in_id = None

    async def send_check_in(self, chat_id: int):
        """Send hourly check-in message with Sleep/Wake buttons"""
        try:
            conn = get_connection()
            cursor = conn.cursor()

            # Create check-in record
            scheduled_time = datetime.now()
            cursor.execute(
                "INSERT INTO check_ins (scheduled_time, sent_time, status) VALUES (?, ?, ?)",
                (scheduled_time, scheduled_time, 'sent')
            )
            check_in_id = cursor.lastrowid
            conn.commit()
            conn.close()

            # Store as pending
            self.pending_check_in_id = check_in_id

            # Create inline keyboard with Sleep/Wake buttons
            keyboard = [
                [
                    InlineKeyboardButton("😴 Sleep", callback_data="checkin_sleep"),
                    InlineKeyboardButton("☀️ Wake", callback_data="checkin_wake")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Send check-in message
            message = (
                "⏰ **Hourly Check-In**\n\n"
                "What did you do in the last hour?\n\n"
                "💬 Reply with what you worked on, and I'll analyze how it aligns with your goals."
            )

            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )

            logger.info(f"✅ Check-in {check_in_id} sent to chat {chat_id}")
            return check_in_id

        except Exception as e:
            logger.error(f"Failed to send check-in: {e}")
            raise

    def get_pending_check_in(self):
        """Get the current pending check-in ID"""
        if not self.pending_check_in_id:
            # Try to find most recent sent check-in
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id FROM check_ins WHERE status = 'sent' ORDER BY sent_time DESC LIMIT 1"
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    self.pending_check_in_id = row[0]
            except Exception as e:
                logger.error(f"Failed to get pending check-in: {e}")
                return None

        return self.pending_check_in_id

    def clear_pending_check_in(self):
        """Clear the pending check-in after user responds"""
        self.pending_check_in_id = None

    async def handle_sleep_button(self, chat_id: int):
        """Handle Sleep button press"""
        try:
            conn = get_connection()
            cursor = conn.cursor()

            # Update user config
            now = datetime.now()
            cursor.execute(
                """UPDATE user_config
                   SET is_sleeping = 1,
                       sleep_start_time = ?,
                       updated_at = ?
                   WHERE chat_id = ?""",
                (now, now, chat_id)
            )

            conn.commit()
            conn.close()

            logger.info(f"User {chat_id} entered sleep mode at {now}")

        except Exception as e:
            logger.error(f"Failed to handle sleep button: {e}")
            raise

    async def handle_wake_button(self, chat_id: int):
        """Handle Wake button press and mark retroactive sleep periods"""
        try:
            conn = get_connection()
            cursor = conn.cursor()

            # Get sleep start time and default wake time
            cursor.execute(
                "SELECT sleep_start_time, default_wake_time FROM user_config WHERE chat_id = ?",
                (chat_id,)
            )
            row = cursor.fetchone()

            if not row or not row[0]:
                logger.warning("No sleep start time found")
                conn.close()
                return 0

            sleep_start_time = datetime.fromisoformat(row[0])
            default_wake_time_str = row[1] or "08:00"

            # Calculate default wake time for today
            today = datetime.now().date()
            wake_hour, wake_min = map(int, default_wake_time_str.split(':'))
            default_wake_time = datetime.combine(today, dt_time(wake_hour, wake_min))

            # Retroactive start is the later of sleep_start_time or default_wake_time
            retroactive_start = max(sleep_start_time, default_wake_time)
            now = datetime.now()

            # Mark check-ins as sleeping between retroactive_start and now
            cursor.execute(
                """UPDATE check_ins
                   SET status = 'sleeping'
                   WHERE scheduled_time >= ?
                     AND scheduled_time <= ?
                     AND status IN ('missed', 'pending')""",
                (retroactive_start, now)
            )

            # Create activity_logs entries for sleeping periods
            cursor.execute(
                """SELECT id, scheduled_time FROM check_ins
                   WHERE status = 'sleeping'
                     AND scheduled_time >= ?
                     AND scheduled_time <= ?""",
                (retroactive_start, now)
            )

            sleeping_check_ins = cursor.fetchall()
            for check_in_id, scheduled_time in sleeping_check_ins:
                # Check if activity log already exists
                cursor.execute(
                    "SELECT id FROM activity_logs WHERE check_in_id = ?",
                    (check_in_id,)
                )
                if not cursor.fetchone():
                    cursor.execute(
                        """INSERT INTO activity_logs
                           (timestamp, activity_summary, productivity_type, check_in_id)
                           VALUES (?, ?, ?, ?)""",
                        (scheduled_time, "Sleeping", "sleeping", check_in_id)
                    )

            # Update user config - wake up
            cursor.execute(
                """UPDATE user_config
                   SET is_sleeping = 0,
                       last_wake_time = ?,
                       updated_at = ?
                   WHERE chat_id = ?""",
                (now, now, chat_id)
            )

            conn.commit()
            conn.close()

            # Calculate hours slept
            hours_slept = (now - sleep_start_time).total_seconds() / 3600

            logger.info(f"User {chat_id} woke up. Slept for {hours_slept:.1f} hours")
            return round(hours_slept, 1)

        except Exception as e:
            logger.error(f"Failed to handle wake button: {e}")
            raise

    def mark_stale_as_missed(self):
        """Mark check-ins older than 90 minutes without response as missed"""
        try:
            conn = get_connection()
            cursor = conn.cursor()

            threshold = datetime.now() - timedelta(minutes=90)

            cursor.execute(
                """UPDATE check_ins
                   SET status = 'missed'
                   WHERE status = 'sent'
                     AND sent_time < ?""",
                (threshold,)
            )

            updated_count = cursor.rowcount
            conn.commit()
            conn.close()

            if updated_count > 0:
                logger.info(f"Marked {updated_count} stale check-ins as missed")

        except Exception as e:
            logger.error(f"Failed to mark stale check-ins: {e}")
