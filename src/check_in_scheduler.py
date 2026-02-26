"""
CheckInScheduler: APScheduler-based hourly check-in system
"""
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from src.database import get_connection

logger = logging.getLogger(__name__)

class CheckInScheduler:
    def __init__(self, application, check_in_manager):
        self.application = application
        self.check_in_manager = check_in_manager
        self.scheduler = AsyncIOScheduler()

    def start(self):
        """Start the scheduler if user config exists"""
        try:
            # Check if user config exists
            chat_id = self._get_configured_chat_id()
            if not chat_id:
                logger.info("⏳ Check-in scheduler waiting for user setup (/start)")
                return False

            # Add hourly check-in job
            self.scheduler.add_job(
                self._send_hourly_check_in,
                CronTrigger(minute=0),  # Every hour at :00
                id='hourly_check_in',
                replace_existing=True
            )

            # Add cleanup job (every 30 minutes)
            self.scheduler.add_job(
                self._cleanup_stale_check_ins,
                CronTrigger(minute='*/30'),
                id='cleanup_check_ins',
                replace_existing=True
            )

            self.scheduler.start()
            logger.info("✅ Check-in scheduler started")
            return True

        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")
            return False

    async def _send_hourly_check_in(self):
        """Main hourly check-in job"""
        try:
            chat_id = self._get_configured_chat_id()
            if not chat_id:
                logger.warning("No chat_id configured, skipping check-in")
                return

            # Check 1: Is user sleeping?
            if self._is_user_sleeping(chat_id):
                logger.info("User is sleeping, skipping check-in")
                return

            # Check 2: Is user in a conversation?
            if self._is_user_busy(chat_id):
                logger.info("User is busy, scheduling retry")
                # Schedule retry in 5 minutes
                self.scheduler.add_job(
                    self._retry_check_in,
                    'date',
                    run_date=datetime.now().replace(second=0, microsecond=0) + \
                             __import__('datetime').timedelta(minutes=5),
                    args=[chat_id, 1]
                )
                return

            # Send check-in
            await self.check_in_manager.send_check_in(chat_id)

        except Exception as e:
            logger.error(f"Hourly check-in failed: {e}")

    async def _retry_check_in(self, chat_id: int, retry_count: int):
        """Retry check-in if user was busy"""
        try:
            # Max 3 retries
            if retry_count > 3:
                logger.info(f"Max retries reached, marking as missed")
                return

            # Check if still busy
            if self._is_user_busy(chat_id):
                # Schedule next retry (5, 10, 10 minutes intervals)
                next_interval = 5 if retry_count == 1 else 10
                logger.info(f"User still busy, retry {retry_count + 1} in {next_interval} mins")

                self.scheduler.add_job(
                    self._retry_check_in,
                    'date',
                    run_date=datetime.now().replace(second=0, microsecond=0) + \
                             __import__('datetime').timedelta(minutes=next_interval),
                    args=[chat_id, retry_count + 1]
                )
                return

            # User is free, send check-in
            await self.check_in_manager.send_check_in(chat_id)
            logger.info(f"Check-in sent after {retry_count} retries")

        except Exception as e:
            logger.error(f"Retry check-in failed: {e}")

    async def _cleanup_stale_check_ins(self):
        """Mark old unanswered check-ins as missed"""
        try:
            self.check_in_manager.mark_stale_as_missed()
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")

    def _get_configured_chat_id(self):
        """Get the configured chat_id from user_config"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id FROM user_config WHERE check_ins_enabled = 1 LIMIT 1")
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"Failed to get chat_id: {e}")
            return None

    def _is_user_sleeping(self, chat_id: int):
        """Check if user is in sleep mode"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT is_sleeping FROM user_config WHERE chat_id = ?", (chat_id,))
            row = cursor.fetchone()
            conn.close()
            return row[0] == 1 if row else False
        except Exception as e:
            logger.error(f"Failed to check sleep status: {e}")
            return False

    def _is_user_busy(self, chat_id: int):
        """Check if user is in a conversation flow"""
        try:
            # Access bot's user_data context
            # Note: This requires accessing application's context
            # We'll check by looking for recent unanswered check-ins instead
            # A more robust solution would track conversation state in database

            # For now, assume user is NOT busy unless we find evidence
            # This can be enhanced later with conversation state tracking
            return False

        except Exception as e:
            logger.error(f"Failed to check busy status: {e}")
            return False

    def stop(self):
        """Stop the scheduler"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Check-in scheduler stopped")
