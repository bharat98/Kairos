"""
ProductivityReporter: Generate daily/weekly productivity reports
"""
import logging
from datetime import datetime, date, timedelta
from src.database import get_connection

logger = logging.getLogger(__name__)

class ProductivityReporter:
    def format_daily_report(self, target_date: date = None):
        """Generate user-friendly daily productivity report"""
        if target_date is None:
            target_date = date.today()

        try:
            stats = self._get_daily_stats(target_date)

            if stats['total_check_ins'] == 0:
                return (
                    "📊 **Daily Productivity Report**\n"
                    f"Date: {target_date.strftime('%Y-%m-%d')}\n\n"
                    "No check-in data available for this day.\n"
                    "Check-ins will start automatically at the next hour."
                )

            # Calculate response rate
            response_rate = 0
            if stats['total_check_ins'] > 0:
                response_rate = (stats['responded_check_ins'] / stats['total_check_ins']) * 100

            # Build report
            report = f"📊 **Daily Productivity Report**\n"
            report += f"Date: {target_date.strftime('%Y-%m-%d')}\n\n"

            # Check-in summary
            report += f"**Check-ins:** {stats['responded_check_ins']}/{stats['total_check_ins']} responded"
            if stats['sleeping_check_ins'] > 0:
                report += f" ({stats['sleeping_check_ins']} sleeping)"
            report += f" ({response_rate:.0f}%)\n\n"

            # Activity breakdown
            if stats['responded_check_ins'] > 0:
                report += "**Activity Breakdown:**\n"
                report += f"✅ Aligned (on todo list): {stats['aligned_activities']} hours\n"
                report += f"💡 Beneficial (goal-aligned): {stats['beneficial_activities']} hours\n"
                report += f"⚠️ Wasted time: {stats['wasted_activities']} hours\n\n"

                # Alignment score
                if stats['avg_alignment_score'] is not None:
                    report += f"**Alignment Score:** {stats['avg_alignment_score']:.1f}/10\n"

                # Productivity ratio
                if stats['productivity_ratio'] is not None:
                    report += f"**Productivity Ratio:** {stats['productivity_ratio']:.0f}%\n\n"

                # Category breakdown
                category_breakdown = self._get_category_breakdown(target_date)
                if category_breakdown:
                    report += "**Time by Category:**\n"
                    for category, hours in category_breakdown.items():
                        report += f"- {category}: {hours} hour{'s' if hours != 1 else ''}\n"

            # Missed check-ins
            if stats['missed_check_ins'] > 0:
                report += f"\n⚠️ **Missed Check-ins:** {stats['missed_check_ins']}\n"

            return report

        except Exception as e:
            logger.error(f"Failed to generate daily report: {e}")
            return "❌ Failed to generate report. Please try again."

    def _get_daily_stats(self, target_date: date):
        """Query database for daily statistics"""
        try:
            conn = get_connection()
            cursor = conn.cursor()

            # Date range for the day
            start_datetime = datetime.combine(target_date, datetime.min.time())
            end_datetime = datetime.combine(target_date, datetime.max.time())

            # Count check-ins by status
            cursor.execute(
                """SELECT status, COUNT(*) FROM check_ins
                   WHERE scheduled_time >= ? AND scheduled_time <= ?
                   GROUP BY status""",
                (start_datetime, end_datetime)
            )
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}

            # Count activities by type
            cursor.execute(
                """SELECT productivity_type, COUNT(*) FROM activity_logs
                   WHERE timestamp >= ? AND timestamp <= ?
                   GROUP BY productivity_type""",
                (start_datetime, end_datetime)
            )
            activity_counts = {row[0]: row[1] for row in cursor.fetchall()}

            # Calculate average alignment score
            cursor.execute(
                """SELECT AVG(alignment_score) FROM activity_logs
                   WHERE timestamp >= ? AND timestamp <= ?
                     AND productivity_type != 'sleeping'""",
                (start_datetime, end_datetime)
            )
            avg_score = cursor.fetchone()[0]

            conn.close()

            # Calculate totals
            total_check_ins = sum(status_counts.values())
            responded = status_counts.get('completed', 0)
            missed = status_counts.get('missed', 0)
            sleeping = status_counts.get('sleeping', 0)

            aligned = activity_counts.get('aligned', 0)
            beneficial = activity_counts.get('beneficial', 0)
            wasted = activity_counts.get('wasted', 0)

            # Calculate productivity ratio
            productivity_ratio = None
            productive_hours = aligned + beneficial
            if responded > 0:
                productivity_ratio = (productive_hours / responded) * 100

            return {
                'total_check_ins': total_check_ins,
                'responded_check_ins': responded,
                'missed_check_ins': missed,
                'sleeping_check_ins': sleeping,
                'aligned_activities': aligned,
                'beneficial_activities': beneficial,
                'wasted_activities': wasted,
                'avg_alignment_score': avg_score,
                'productivity_ratio': productivity_ratio
            }

        except Exception as e:
            logger.error(f"Failed to get daily stats: {e}")
            return {
                'total_check_ins': 0,
                'responded_check_ins': 0,
                'missed_check_ins': 0,
                'sleeping_check_ins': 0,
                'aligned_activities': 0,
                'beneficial_activities': 0,
                'wasted_activities': 0,
                'avg_alignment_score': None,
                'productivity_ratio': None
            }

    def _get_category_breakdown(self, target_date: date):
        """Get time spent by category"""
        try:
            conn = get_connection()
            cursor = conn.cursor()

            start_datetime = datetime.combine(target_date, datetime.min.time())
            end_datetime = datetime.combine(target_date, datetime.max.time())

            cursor.execute(
                """SELECT category, COUNT(*) FROM activity_logs
                   WHERE timestamp >= ? AND timestamp <= ?
                     AND category IS NOT NULL
                     AND productivity_type != 'sleeping'
                   GROUP BY category
                   ORDER BY COUNT(*) DESC""",
                (start_datetime, end_datetime)
            )

            categories = {}
            for row in cursor.fetchall():
                categories[row[0]] = row[1]

            conn.close()
            return categories

        except Exception as e:
            logger.error(f"Failed to get category breakdown: {e}")
            return {}

    def save_daily_metrics(self, target_date: date = None):
        """Aggregate and save daily metrics to productivity_metrics table"""
        if target_date is None:
            target_date = date.today()

        try:
            stats = self._get_daily_stats(target_date)

            conn = get_connection()
            cursor = conn.cursor()

            start_datetime = datetime.combine(target_date, datetime.min.time())
            end_datetime = datetime.combine(target_date, datetime.max.time())

            # Check if metrics already exist for this day
            cursor.execute(
                """SELECT id FROM productivity_metrics
                   WHERE period_start = ? AND period_type = 'daily'""",
                (start_datetime,)
            )

            if cursor.fetchone():
                # Update existing
                cursor.execute(
                    """UPDATE productivity_metrics
                       SET total_check_ins = ?,
                           responded_check_ins = ?,
                           missed_check_ins = ?,
                           sleeping_check_ins = ?,
                           aligned_activities = ?,
                           beneficial_activities = ?,
                           wasted_activities = ?,
                           avg_alignment_score = ?,
                           productivity_ratio = ?
                       WHERE period_start = ? AND period_type = 'daily'""",
                    (
                        stats['total_check_ins'],
                        stats['responded_check_ins'],
                        stats['missed_check_ins'],
                        stats['sleeping_check_ins'],
                        stats['aligned_activities'],
                        stats['beneficial_activities'],
                        stats['wasted_activities'],
                        stats['avg_alignment_score'],
                        stats['productivity_ratio'],
                        start_datetime
                    )
                )
            else:
                # Insert new
                cursor.execute(
                    """INSERT INTO productivity_metrics
                       (period_start, period_end, period_type,
                        total_check_ins, responded_check_ins, missed_check_ins, sleeping_check_ins,
                        aligned_activities, beneficial_activities, wasted_activities,
                        avg_alignment_score, productivity_ratio)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        start_datetime, end_datetime, 'daily',
                        stats['total_check_ins'],
                        stats['responded_check_ins'],
                        stats['missed_check_ins'],
                        stats['sleeping_check_ins'],
                        stats['aligned_activities'],
                        stats['beneficial_activities'],
                        stats['wasted_activities'],
                        stats['avg_alignment_score'],
                        stats['productivity_ratio']
                    )
                )

            conn.commit()
            conn.close()
            logger.info(f"Daily metrics saved for {target_date}")

        except Exception as e:
            logger.error(f"Failed to save daily metrics: {e}")
