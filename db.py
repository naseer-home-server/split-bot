import os
import logging
from typing import Optional
from dotenv import load_dotenv
import psycopg
from metrics import db_connection_status, db_errors_total

# Load environment variables
load_dotenv()

# Setup logging
logger = logging.getLogger(__name__)

# Database connection string
db_connection_string = os.getenv("DB_CONNECTION_STRING")
if not db_connection_string:
    raise ValueError("DB_CONNECTION_STRING environment variable is required")

# Global connection object
_conn: Optional[psycopg.Connection] = None


def get_connection() -> psycopg.Connection:
    """
    Get or create a database connection.
    
    Returns:
        psycopg.Connection: The database connection object
    """
    global _conn
    try:
        if _conn is None or _conn.closed:
            _conn = psycopg.connect(db_connection_string)
        # Update connection status metric
        db_connection_status.set(1)
        return _conn
    except Exception as e:
        # Update connection status metric on error
        db_connection_status.set(0)
        db_errors_total.labels(operation="get_connection", error_type=type(e).__name__).inc()
        raise


def connect_db():
    """Connect to the database."""
    try:
        get_connection()
        db_connection_status.set(1)
        logger.info("Connected to database")
    except Exception as e:
        db_connection_status.set(0)
        db_errors_total.labels(operation="connect_db", error_type=type(e).__name__).inc()
        logger.error(f"Error connecting to database: {str(e)}")
        raise


def close_db():
    """Close the database connection."""
    global _conn
    try:
        if _conn and not _conn.closed:
            _conn.close()
            _conn = None
            db_connection_status.set(0)
            logger.info("Closed database connection")
        else:
            db_connection_status.set(0)
    except Exception as e:
        db_connection_status.set(0)
        db_errors_total.labels(operation="close_db", error_type=type(e).__name__).inc()
        logger.error(f"Error closing database connection: {str(e)}")
        raise
