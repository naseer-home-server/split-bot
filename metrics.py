"""
Prometheus metrics definitions for the Split Bot server.
"""
from prometheus_client import Counter, Histogram, Gauge

# AI Processing Metrics
ai_processing_duration_seconds = Histogram(
    'ai_processing_duration_seconds',
    'Duration of AI processing in seconds',
    ['platform_type']
)

ai_processing_total = Counter(
    'ai_processing_total',
    'Total number of AI processing requests',
    ['platform_type', 'status']  # status: success or failure
)

ai_processing_errors_total = Counter(
    'ai_processing_errors_total',
    'Total number of AI processing errors',
    ['platform_type', 'error_type']
)

# OCR Metrics
ocr_processing_duration_seconds = Histogram(
    'ocr_processing_duration_seconds',
    'Duration of OCR processing in seconds',
    ['source_type']  # source_type: url or base64
)

ocr_processing_total = Counter(
    'ocr_processing_total',
    'Total number of OCR processing requests',
    ['source_type', 'status']  # status: success or failure
)

ocr_processing_errors_total = Counter(
    'ocr_processing_errors_total',
    'Total number of OCR processing errors',
    ['source_type', 'error_type']
)

# Database Metrics
db_connection_status = Gauge(
    'db_connection_status',
    'Database connection status (1 = connected, 0 = disconnected)'
)

db_query_duration_seconds = Histogram(
    'db_query_duration_seconds',
    'Duration of database queries in seconds',
    ['operation']  # operation: get_users, create_user, etc.
)

db_errors_total = Counter(
    'db_errors_total',
    'Total number of database errors',
    ['operation', 'error_type']
)

# Business Metrics
messages_processed_total = Counter(
    'messages_processed_total',
    'Total number of messages processed',
    ['platform_type', 'group_id']
)

users_created_total = Counter(
    'users_created_total',
    'Total number of users created'
)
