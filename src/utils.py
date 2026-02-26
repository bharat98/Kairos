import os
import logging
from datetime import datetime
from src.database import get_connection

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def log_audit(event_type, details):
    """Log an event to the audit_logs table."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO audit_logs (event_type, details) VALUES (?, ?)",
            (event_type, details)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log audit event: {e}")

def ensure_dirs():
    """Ensure required directories exist."""
    dirs = ["src/data/temp"]
    for d in dirs:
        if not os.path.exists(d):
            os.makedirs(d)
            logger.info(f"Created directory: {d}")

def get_temp_path(file_id, extension):
    """Generate a consistent temp path for media files."""
    ensure_dirs()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"src/data/temp/{timestamp}_{file_id}.{extension}"
