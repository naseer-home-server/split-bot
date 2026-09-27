import os
import json
import re
import httpx
from dataclasses import dataclass
from typing import List, Optional, Tuple
from langchain_core.tools import tool
from .users import search_users
from dotenv import load_dotenv

load_dotenv()

# Validate SPLITWISE_TOKEN is set
SPLITWISE_TOKEN = os.getenv("SPLITWISE_TOKEN")
if not SPLITWISE_TOKEN:
    raise ValueError("SPLITWISE_TOKEN environment variable is required")


@dataclass
class ExpenseUser:
    """Represents a user in an expense."""
    username: str  # Can be telegram_username or whatsapp_number
    owed_share: float
    paid_share: float


@dataclass
class AddExpenseRequest:
    """Request model for adding an expense."""
    cost: str
    description: str
    details: Optional[str] = None
    currency_code: str = "SGD"
    category_id: int = 25
    users: List[ExpenseUser] = None
    
    def __post_init__(self):
        if self.users is None:
            self.users = []


def replace_usernames_with_names(details: str, username_to_name: dict) -> str:
    """
    Replace usernames in the details string with their corresponding names.
    Handles usernames with or without @ symbol prefix.
    
    Args:
        details: The details string that may contain usernames
        username_to_name: Dictionary mapping username -> name
    
    Returns:
        str: The details string with usernames replaced by names
    """
    if not details or not username_to_name:
        return details
    
    result = details
    # Sort by length (longest first) to avoid partial replacements
    # e.g., if we have "john_doe" and "john", we want to replace "john_doe" first
    sorted_usernames = sorted(username_to_name.keys(), key=len, reverse=True)
    
    for username in sorted_usernames:
        name = username_to_name[username]
        # Escape special regex characters in username
        escaped_username = re.escape(username)
        
        # Replace @username with name (handle @ prefix first)
        pattern_with_at = r'@' + escaped_username + r'\b'
        result = re.sub(pattern_with_at, name, result)
        
        # Replace plain username with name (word boundary to avoid partial matches)
        pattern_plain = r'\b' + escaped_username + r'\b'
        result = re.sub(pattern_plain, name, result)
    
    return result


def validate_and_parse_expense_request(request_json: str) -> Tuple[Optional[AddExpenseRequest], Optional[str]]:
    """
    Validate and parse an expense request JSON string.
    
    Args:
        request_json: A JSON string representing the AddExpenseRequest
    
    Returns:
        tuple: (AddExpenseRequest, None) on success, or (None, error_message) on error
    """
    # Parse the JSON string
    try:
        request_data = json.loads(request_json)
    except json.JSONDecodeError as e:
        return None, f"Error: Invalid JSON format - {str(e)}. Please check that your JSON is properly formatted."
    
    # Validate required top-level fields
    if not isinstance(request_data, dict):
        return None, "Error: Request must be a JSON object"
    
    missing_fields = []
    if "cost" not in request_data:
        missing_fields.append("cost")
    if "description" not in request_data:
        missing_fields.append("description")
    if "users" not in request_data:
        missing_fields.append("users")
    
    if missing_fields:
        return None, f"Error: Missing required field(s): {', '.join(missing_fields)}"
    
    # Validate users array
    if not isinstance(request_data["users"], list):
        return None, "Error: 'users' must be an array"
    
    if len(request_data["users"]) == 0:
        return None, "Error: 'users' array cannot be empty. At least one user is required."
    
    # Validate each user object
    user_errors = []
    for idx, user in enumerate(request_data["users"]):
        if not isinstance(user, dict):
            user_errors.append(f"users[{idx}] must be an object")
            continue
        
        user_missing_fields = []
        if "username" not in user:
            user_missing_fields.append("username")
        if "owed_share" not in user:
            user_missing_fields.append("owed_share")
        if "paid_share" not in user:
            user_missing_fields.append("paid_share")
        
        if user_missing_fields:
            user_errors.append(f"users[{idx}] missing required field(s): {', '.join(user_missing_fields)}")
    
    if user_errors:
        return None, f"Error: {'. '.join(user_errors)}"
    
    # Create ExpenseUser objects
    try:
        users = [
            ExpenseUser(
                username=str(user["username"]),
                owed_share=float(user["owed_share"]),
                paid_share=float(user["paid_share"])
            )
            for user in request_data["users"]
        ]
    except (ValueError, TypeError) as e:
        return None, f"Error: Invalid value type in user data - {str(e)}. 'owed_share' and 'paid_share' must be numbers."
    
    # Create AddExpenseRequest object
    try:
        expense_request = AddExpenseRequest(
            cost=str(request_data["cost"]),
            description=request_data["description"],
            details=request_data.get("details"),
            currency_code=request_data.get("currency_code", "SGD"),
            category_id=request_data.get("category_id", 25),
            users=users
        )
    except Exception as e:
        return None, f"Error: Failed to create expense request - {str(e)}"
    
    return expense_request, None


@tool
def add_expense(request_json: str) -> str:
    """
    Add an expense to Splitwise.
    
    Args:
        request_json: A JSON string representing the AddExpenseRequest. Format:
        {
            "cost": "26",
            "description": "The name of the restaurant or the item being split. This will be used as the expense title in Splitwise.",
            "details": "The details of who owes how much and for what" (optional),
            "currency_code": "SGD" (optional, default: "SGD"),
            "category_id": 25 (optional, default: 25),
            "users": [
                {
                    "username": "username is the username part of the @username of the user. Make sure you don't pass the @ symbol in the username",
                    "owed_share": 10.0,
                    "paid_share": 25.0
                },
                ...
            ]
        }
    
    Returns:
        str: Success message with expense ID and expense title, or error message if request fails
    """
    try:
        # Validate and parse the request
        expense_request, error = validate_and_parse_expense_request(request_json)
        if error:
            return error
        
        # Validate that all usernames exist in the database
        # username could be either telegram_username or whatsapp_number
        # Pass the same value to both parameters - search_users uses OR logic
        missing_usernames = []
        user_emails = {}
        username_to_name = {}
        
        for user in expense_request.users:
            username = str(user.username)
            
            # Search for user by telegram_username OR whatsapp_number
            # search_users uses OR logic, so passing the same value to both will match either field
            found_users = search_users(telegram_username=username, whatsapp_lid=username)
            
            if found_users:
                db_user = found_users[0]
                user_emails[user.username] = db_user.email
                username_to_name[user.username] = db_user.name
            else:
                missing_usernames.append(username)
        
        if missing_usernames:
            return f"These users dont exist in the db ({','.join(missing_usernames)})"
        
        # Build the request payload
        # Splitwise requires the authenticated user (splitbot) to be involved in the expense.
        # We add 1 to the cost and include splitbot with owed_share=1 and paid_share=1,
        # ensuring splitbot is involved but has a net balance of 0 (never owes anyone anything).
        cost_float = float(expense_request.cost)
        cost_with_splitbot = str(cost_float + 1.0)
        
        payload = {
            "cost": cost_with_splitbot,
            "description": expense_request.description,
            "currency_code": expense_request.currency_code,
            "category_id": expense_request.category_id
        }
        
        if expense_request.details:
            # Replace usernames with names in the details field
            payload["details"] = replace_usernames_with_names(expense_request.details, username_to_name)
        
        # Add users to payload
        for idx, user in enumerate(expense_request.users):
            email = user_emails[user.username]
            payload[f"users__{idx}__email"] = email
            payload[f"users__{idx}__paid_share"] = str(user.paid_share)
            payload[f"users__{idx}__owed_share"] = str(user.owed_share)
        
        # Add splitbot user at the end
        # Splitwise requires splitbot to be involved, but we ensure it has zero net balance
        splitbot_idx = len(expense_request.users)
        payload[f"users__{splitbot_idx}__email"] = "splitbot2407@gmail.com"
        payload[f"users__{splitbot_idx}__paid_share"] = "1"
        payload[f"users__{splitbot_idx}__owed_share"] = "1"
        
        # Make the API request
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SPLITWISE_TOKEN}"
        }
        
        with httpx.Client() as client:
            response = client.post(
                "https://secure.splitwise.com/api/v3.0/create_expense",
                headers=headers,
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
        
        # Check for errors in the response
        if result.get("errors"):
            error_messages = result["errors"].get("base", [])
            if error_messages:
                return error_messages[0]
            return "Error: Unknown error occurred while adding expense"
        
        # Extract expense ID and description from successful response
        if result.get("expenses") and len(result["expenses"]) > 0:
            expense = result["expenses"][0]
            expense_id = expense.get("id")
            expense_description = expense.get("description", "Unknown")
            return f"Successfully added expense '{expense_description}' with expense ID: {expense_id}"
        else:
            return "Error: Expense was created but no expense ID was returned"
            
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def update_expense(expense_id: str, request_json: str) -> str:
    """
    Update an existing expense in Splitwise.
    
    Args:
        expense_id: The ID of the expense to update
        request_json: A JSON string representing the AddExpenseRequest. Format:
        {
            "cost": "26",
            "description": "The name of the restaurant or the item being split. This will be used as the expense title in Splitwise.",
            "details": "The details of who owes how much and for what" (optional),
            "currency_code": "SGD" (optional, default: "SGD"),
            "category_id": 25 (optional, default: 25),
            "users": [
                {
                    "username": "username is the username part of the @username of the user. Make sure you don't pass the @ symbol in the username",
                    "owed_share": 10.0,
                    "paid_share": 25.0
                },
                ...
            ]
        }
    
    Returns:
        str: Success message with expense ID and expense title, or error message if request fails
    """
    try:
        # Validate and parse the request
        expense_request, error = validate_and_parse_expense_request(request_json)
        if error:
            return error
        
        # Validate that all usernames exist in the database
        # username could be either telegram_username or whatsapp_number
        # Pass the same value to both parameters - search_users uses OR logic
        missing_usernames = []
        user_emails = {}
        username_to_name = {}
        
        for user in expense_request.users:
            username = str(user.username)
            
            # Search for user by telegram_username OR whatsapp_number
            # search_users uses OR logic, so passing the same value to both will match either field
            found_users = search_users(telegram_username=username, whatsapp_lid=username)
            
            if found_users:
                db_user = found_users[0]
                user_emails[user.username] = db_user.email
                username_to_name[user.username] = db_user.name
            else:
                missing_usernames.append(username)
        
        if missing_usernames:
            return f"These users dont exist in the db ({','.join(missing_usernames)})"
        
        # Build the request payload
        # Splitwise requires the authenticated user (splitbot) to be involved in the expense.
        # We add 1 to the cost and include splitbot with owed_share=1 and paid_share=1,
        # ensuring splitbot is involved but has a net balance of 0 (never owes anyone anything).
        cost_float = float(expense_request.cost)
        cost_with_splitbot = str(cost_float + 1.0)
        
        payload = {
            "cost": cost_with_splitbot,
            "description": expense_request.description,
            "currency_code": expense_request.currency_code,
            "category_id": expense_request.category_id
        }
        
        if expense_request.details:
            # Replace usernames with names in the details field
            payload["details"] = replace_usernames_with_names(expense_request.details, username_to_name)
        
        # Add users to payload
        for idx, user in enumerate(expense_request.users):
            email = user_emails[user.username]
            payload[f"users__{idx}__email"] = email
            payload[f"users__{idx}__paid_share"] = str(user.paid_share)
            payload[f"users__{idx}__owed_share"] = str(user.owed_share)
        
        # Add splitbot user at the end
        # Splitwise requires splitbot to be involved, but we ensure it has zero net balance
        splitbot_idx = len(expense_request.users)
        payload[f"users__{splitbot_idx}__email"] = "splitbot2407@gmail.com"
        payload[f"users__{splitbot_idx}__paid_share"] = "1"
        payload[f"users__{splitbot_idx}__owed_share"] = "1"
        
        # Make the API request
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {SPLITWISE_TOKEN}"
        }
        
        with httpx.Client() as client:
            response = client.post(
                f"https://secure.splitwise.com/api/v3.0/update_expense/{expense_id}",
                headers=headers,
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
        
        # Check for errors in the response
        if result.get("errors"):
            error_messages = result["errors"].get("base", [])
            if error_messages:
                return error_messages[0]
            return "Error: Unknown error occurred while updating expense"
        
        # Extract expense ID and description from successful response
        if result.get("expenses") and len(result["expenses"]) > 0:
            expense = result["expenses"][0]
            expense_id = expense.get("id")
            expense_description = expense.get("description", "Unknown")
            return f"Successfully updated expense '{expense_description}' with expense ID: {expense_id}"
        else:
            return "Error: Expense was updated but no expense ID was returned"
            
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def delete_expense(expense_id: str) -> str:
    """
    Delete an existing expense in Splitwise.
    
    Args:
        expense_id: The ID of the expense to delete
    
    Returns:
        str: Success message with expense ID and description, or error message if request fails
    """
    try:
        # Make the API request
        headers = {
            "Authorization": f"Bearer {SPLITWISE_TOKEN}"
        }
        
        with httpx.Client() as client:
            response = client.post(
                f"https://secure.splitwise.com/api/v3.0/delete_expense/{expense_id}",
                headers=headers,
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()
        
        # Check for errors in the response
        if result.get("errors"):
            error_messages = result["errors"].get("base", [])
            if error_messages:
                return error_messages[0]
            return "Error: Unknown error occurred while deleting expense"
        
        # Check for success response
        if result.get("success") is True:
            return f"Successfully deleted expense with ID: {expense_id}"
        else:
            return "Error: Expense deletion failed - success flag not returned"
            
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return f"Error: {str(e)}"
