import logging
import os
import time
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from contextlib import asynccontextmanager
from prometheus_fastapi_instrumentator import Instrumentator
from ai import process_message, SplitBotRequest
from db import connect_db, close_db
from dotenv import load_dotenv
from splitwise.users import init_users_table, get_all_users, create_user, update_user, get_user_by_id
from psycopg.errors import UniqueViolation
from metrics import messages_processed_total, users_created_total, db_query_duration_seconds, db_errors_total

# Load environment variables
load_dotenv()

# Determine environment
ENV = os.getenv("ENV", "development").lower()
IS_DEV = ENV in ["dev", "development"]
IS_PROD = ENV in ["prod", "production"]

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.DEBUG if IS_DEV else logging.INFO
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan: startup and shutdown events."""
    # Startup: Connect to database
    try:
        connect_db()
        logger.info("Database connection established on startup")
        init_users_table()
    except Exception as e:
        logger.error(f"Failed to connect to database on startup: {str(e)}")
        raise
    
    yield
    
    # Shutdown: Close database connection
    try:
        close_db()
        logger.info("Database connection closed on shutdown")
    except Exception as e:
        logger.error(f"Error closing database connection on shutdown: {str(e)}")


app = FastAPI(
    title="Split Bot API",
    version="1.0.0",
    lifespan=lifespan
)

# Set up Prometheus instrumentation
instrumentator = Instrumentator()
instrumentator.instrument(app).expose(app)


class ImageBase64(BaseModel):
    """Model for base64-encoded image data"""
    data: str = Field(..., description="Base64-encoded image data")
    mtype: str = Field(..., description="MIME type of the image (e.g., 'image/png', 'image/jpeg')")


class ProcessMessageRequest(BaseModel):
    """Pydantic model for request validation matching SplitBotRequest"""
    message: str = Field(..., description="The message content")
    group_id: str = Field(..., description="The group ID for conversation context")
    sender: str = Field(..., description="The sender ID")
    platform_type: str = Field(..., description="Platform type (WHATSAPP or TELEGRAM)")
    image_url: Optional[str] = Field(None, description="Optional URL to an image for OCR processing")
    image_base64: Optional[ImageBase64] = Field(None, description="Optional base64-encoded image for OCR processing")
    bot_name: Optional[str] = Field("me", description="The bot name to use in the system prompt")


class ProcessMessageResponse(BaseModel):
    """Response model for the process_message endpoint"""
    response: Optional[str] = Field(None, description="The AI response message")
    error: Optional[str] = Field(None, description="Error message if any")


class UserResponse(BaseModel):
    """Response model for user data"""
    id: int = Field(..., description="User ID")
    name: str = Field(..., description="User's name")
    email: str = Field(..., description="User's email")
    telegram_username: Optional[str] = Field(None, description="User's Telegram username")
    whatsapp_number: Optional[str] = Field(None, description="User's WhatsApp number")
    whatsapp_lid: Optional[str] = Field(None, description="User's WhatsApp LID")
    created_at: datetime = Field(..., description="User creation timestamp")
    updated_at: datetime = Field(..., description="User last update timestamp")
    
    class Config:
        from_attributes = True


class CreateUserRequest(BaseModel):
    """Request model for creating a user"""
    name: str = Field(..., description="User's name")
    email: str = Field(..., description="User's email")
    telegram_username: Optional[str] = Field(None, description="User's Telegram username")
    whatsapp_number: Optional[str] = Field(None, description="User's WhatsApp number")
    whatsapp_lid: Optional[str] = Field(None, description="User's WhatsApp LID")


class UpdateUserRequest(BaseModel):
    """Request model for updating a user"""
    name: Optional[str] = Field(None, description="User's name")
    email: Optional[str] = Field(None, description="User's email")
    telegram_username: Optional[str] = Field(None, description="User's Telegram username")
    whatsapp_number: Optional[str] = Field(None, description="User's WhatsApp number")
    whatsapp_lid: Optional[str] = Field(None, description="User's WhatsApp LID")


@app.post("/process_message", response_model=ProcessMessageResponse)
async def process_message_endpoint(request: ProcessMessageRequest) -> ProcessMessageResponse:
    """
    Process a message using the Split Bot AI.
    
    Accepts a JSON body with message, group_id, sender, platform_type, and optional image_url.
    Returns the AI's response or an error message.
    """
    platform_type = request.platform_type.strip().upper()
    group_id = request.group_id.strip()
    
    try:
        # Convert Pydantic model to SplitBotRequest
        split_bot_request = SplitBotRequest(
            message=request.message,
            group_id=group_id,
            sender=request.sender,
            platform_type=platform_type,
            image_url=request.image_url,
            image_base64=request.image_base64,
            bot_name=request.bot_name or "me"
        )
        
        # Process the message
        ai_response = await process_message(split_bot_request)
        
        # Track successful message processing
        messages_processed_total.labels(
            platform_type=platform_type,
            group_id=group_id
        ).inc()
        
        return ProcessMessageResponse(
            response=ai_response,
            error=None
        )
    
    except ValueError as e:
        # Handle validation errors (e.g., OCR failures)
        logger.error(f"ValueError in process_message: {str(e)}")
        # Track failed message processing
        messages_processed_total.labels(
            platform_type=platform_type,
            group_id=group_id
        ).inc()
        return ProcessMessageResponse(
            response=None,
            error=str(e)
        )
    
    except Exception as e:
        # Handle any other unexpected errors
        logger.error(f"Unexpected error in process_message: {str(e)}", exc_info=True)
        # Track failed message processing
        messages_processed_total.labels(
            platform_type=platform_type,
            group_id=group_id
        ).inc()
        return ProcessMessageResponse(
            response=None,
            error=f"Internal server error: {str(e)}"
        )


@app.get("/users", response_model=List[UserResponse])
def get_users(
    limit: Optional[int] = Query(None, ge=1, description="Maximum number of users to return"),
    offset: int = Query(0, ge=0, description="Number of users to skip")
) -> List[UserResponse]:
    """
    Get all users from the splitwise database.
    
    Supports pagination via limit and offset query parameters.
    Returns a list of all users if no limit is specified.
    """
    start_time = time.time()
    try:
        users = get_all_users(limit=limit, offset=offset)
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="get_users").observe(duration)
        return [UserResponse(
            id=user.id,
            name=user.name,
            email=user.email,
            telegram_username=user.telegram_username,
            whatsapp_number=user.whatsapp_number,
            whatsapp_lid=user.whatsapp_lid,
            created_at=user.created_at,
            updated_at=user.updated_at
        ) for user in users]
    except Exception as e:
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="get_users").observe(duration)
        db_errors_total.labels(operation="get_users", error_type=type(e).__name__).inc()
        logger.error(f"Error getting users: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.post("/users", response_model=UserResponse, status_code=201)
def create_user_endpoint(request: CreateUserRequest) -> UserResponse:
    """
    Create a new user in the splitwise database.
    
    Requires name and email. Telegram username and WhatsApp number are optional.
    Returns the created user object.
    """
    start_time = time.time()
    try:
        user = create_user(
            name=request.name,
            email=request.email,
            telegram_username=request.telegram_username,
            whatsapp_number=request.whatsapp_number,
            whatsapp_lid=request.whatsapp_lid
        )
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="create_user").observe(duration)
        users_created_total.inc()
        return UserResponse(
            id=user.id,
            name=user.name,
            email=user.email,
            telegram_username=user.telegram_username,
            whatsapp_number=user.whatsapp_number,
            whatsapp_lid=user.whatsapp_lid,
            created_at=user.created_at,
            updated_at=user.updated_at
        )
    except UniqueViolation as e:
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="create_user").observe(duration)
        db_errors_total.labels(operation="create_user", error_type="UniqueViolation").inc()
        logger.error(f"User with email {request.email} already exists: {str(e)}")
        raise HTTPException(status_code=409, detail=f"User with email {request.email} already exists")
    except ValueError as e:
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="create_user").observe(duration)
        db_errors_total.labels(operation="create_user", error_type="ValueError").inc()
        logger.error(f"Validation error creating user: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="create_user").observe(duration)
        db_errors_total.labels(operation="create_user", error_type=type(e).__name__).inc()
        logger.error(f"Unexpected error creating user: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.put("/users/{user_id}", response_model=UserResponse)
def update_user_endpoint(user_id: int, request: UpdateUserRequest) -> UserResponse:
    """
    Update an existing user in the splitwise database.
    
    All fields are optional. Only provided fields will be updated.
    Returns the updated user object.
    """
    start_time = time.time()
    try:
        # Check if user exists
        existing_user = get_user_by_id(user_id)
        if not existing_user:
            raise HTTPException(status_code=404, detail=f"User with id {user_id} not found")
        
        # Update the user
        updated_user = update_user(
            user_id=user_id,
            name=request.name,
            email=request.email,
            telegram_username=request.telegram_username,
            whatsapp_number=request.whatsapp_number,
            whatsapp_lid=request.whatsapp_lid
        )
        
        if not updated_user:
            raise HTTPException(status_code=404, detail=f"User with id {user_id} not found")
        
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="update_user").observe(duration)
        
        return UserResponse(
            id=updated_user.id,
            name=updated_user.name,
            email=updated_user.email,
            telegram_username=updated_user.telegram_username,
            whatsapp_number=updated_user.whatsapp_number,
            whatsapp_lid=updated_user.whatsapp_lid,
            created_at=updated_user.created_at,
            updated_at=updated_user.updated_at
        )
    except HTTPException:
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="update_user").observe(duration)
        raise
    except UniqueViolation as e:
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="update_user").observe(duration)
        db_errors_total.labels(operation="update_user", error_type="UniqueViolation").inc()
        logger.error(f"User with email {request.email} already exists: {str(e)}")
        raise HTTPException(status_code=409, detail=f"User with email {request.email} already exists")
    except Exception as e:
        duration = time.time() - start_time
        db_query_duration_seconds.labels(operation="update_user").observe(duration)
        db_errors_total.labels(operation="update_user", error_type=type(e).__name__).inc()
        logger.error(f"Unexpected error updating user: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
