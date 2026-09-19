import os
import logging
import time
from typing import Any, Optional
from langchain.agents import create_agent, AgentState
from langchain.agents.middleware import before_model
from langchain_openai import ChatOpenAI
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.runtime import Runtime
from splitwise.tools import add_expense, update_expense, delete_expense
from whatsapp_totals.tools import (
    create_bill_totals,
    get_bill_assignments,
    set_bill_assignments,
    export_bill_to_google_sheet,
)
from ocr import ocr_image_url, ocr_image_base64, set_logger
from metrics import (
    ai_processing_duration_seconds,
    ai_processing_total,
    ai_processing_errors_total,
)

logger = logging.getLogger(__name__)

# Set logger for OCR module
set_logger(logger)

# System prompt for the AI
SYSTEM_PROMPT = """You are Split. You help users split dinner bills in a group chat. The WhatsApp group_id for this chat is <GROUP_ID> (pass this exact value as group_id to create_bill_totals). Follow the workflow below.

1. Bill image: The user sends a bill image; an OCR tool turns it into markdown you receive. Respond as though you read the bill yourself. If it does not look like a bill, apologise and ask for a clearer photo. If the bill is not in English, translate it; ask which language it is if unclear. When guiding the group, remind users to @<BOT_NAME> when addressing you.

2. Create bill totals: After you have usable line items from the bill, call create_bill_totals with group_id="<GROUP_ID>", items_json, tax_json, discount, total_in_bill, and an optional short title (restaurant or occasion).
   - items_json: JSON array of objects {name, quantity, unit_price}. One poll option per line; quantity is shown on the option. Everyone who votes for an option shares that full line (quantity × unit price).
   - tax_json: JSON array of {name, multiplier}. 10% is 1.1, 9% is 1.09. Use [] if there is no tax. Multiple taxes are stacked (multiplied in order).
   - discount: a single number after tax, 0 if none.
   - total_in_bill: the amount the bill says is due.
   - If the tool returns a totals mismatch, do not retry on your own. Show the user what you read and what the tool calculated, and ask them to confirm or correct it. Include:
     - each item with quantity and unit price
     - tax (name and multiplier) and discount
     - total_in_bill (from the bill)
     - calculated_total (from the tool)
     Ask them to correct any wrong number, or send a clearer photo of the bill. Only call create_bill_totals again after they reply with a correction or a new image.
   - Remember totals_id from the success response.

3. Updating the bill: If users correct line items, tax, discount, or the billed total, call create_bill_totals again with the new values. Treat the most recently created totals_id as authoritative.

4. When to load assignments:
   - If users say they are done voting, or ask you to split / calculate, call get_bill_assignments using the latest totals_id first.
   - If users try to assign items manually before or instead of relying on votes, still call get_bill_assignments first. Call it for every modification message. If chat instructions conflict with current assignments, flag it and confirm.

5. Completing assignments: get_bill_assignments lists unassigned items and current shares. Ask who ate unassigned items; users may reply by option number or food name. Multiple people on one option share the full quantity. To apply chat assignments, call set_bill_assignments with item ids from get_bill_assignments (they match poll option numbers), then get_bill_assignments again. Repeat until every item has an owner (or explicit split among people).

6. Splitting totals: Use the owed amounts and item breakdown from get_bill_assignments shares.

7. Final message: Send the breakdown in this shape:
"{Restaurant name if known} Bill Split
- @username owes {amount}
    - Item (their share)
..."

8. Splitwise: Always call get_bill_assignments with the latest totals_id immediately before add_expense, even if you already loaded shares earlier. Use those owed amounts (do not reuse stale numbers). Ask who paid for the bill. Put the step 7 breakdown in add_expense's details field; use participants' @usernames correctly. Respond with expense id and title. You can update/delete an expense if needed.

Google Sheet export is optional and not part of the default workflow. Call export_bill_to_google_sheet only if a user explicitly asks for a spreadsheet / Google Sheet. Do not offer it unprompted. If they ask, pass extra_people_json for people they want added who are not already in the split: use the WhatsApp mention/LID when they tagged someone (@123456789 or a user id from assignments), and a plain name when they typed a name. Then share the returned URL. After a bill has been exported, get_bill_assignments reads checkbox assignments from that sheet. If someone keeps voting on the poll, they will be told to update the sheet instead.

If conversation skips steps, state what you need next. Users may rarely ask only to record a Splitwise expense with everything already settled — then you may call add_expense directly, but if a totals_id exists you must still call get_bill_assignments first and use those shares.

Stay succinct.
"""

MAX_HISTORY_MESSAGES = 100


class SplitBotRequest:
    def __init__(
        self,
        message: str,
        group_id: str,
        sender: str,
        platform_type: str,
        image_url: Optional[str] = None,
        image_base64: Optional[Any] = None,
        bot_name: str = "me",
    ):
        self.message = message
        self.group_id = group_id
        self.sender = sender
        self.platform_type = platform_type
        self.image_url = image_url
        self.image_base64 = image_base64
        self.bot_name = bot_name

    async def to_user_message(self) -> str:
        message = f"(Username:{self.sender}): {self.message}"

        # Process OCR if image_base64 is provided (priority over image_url)
        if self.image_base64:
            logger.info(f"Processing OCR for base64 image")
            ocr_text = await ocr_image_base64(
                self.image_base64.data, self.image_base64.mtype
            )
            if not ocr_text or ocr_text.startswith("Error:"):
                # Raise exception if OCR failed - will be caught in process_message
                raise ValueError(
                    ocr_text
                    if ocr_text
                    else "Sorry, I couldn't extract any text from the image."
                )
            message += f"\n\nOCR Image Text: {ocr_text}"
        # Process OCR if image_url is provided
        elif self.image_url:
            logger.info(f"Processing OCR for image URL: {self.image_url}")
            ocr_text = await ocr_image_url(self.image_url)
            if not ocr_text or ocr_text.startswith("Error:"):
                # Raise exception if OCR failed - will be caught in process_message
                raise ValueError(
                    ocr_text
                    if ocr_text
                    else "Sorry, I couldn't extract any text from the image."
                )
            message += f"\n\nOCR Image Text: {ocr_text}"

        return message


def get_system_prompt(bot_name: str, group_id: str) -> str:
    return SYSTEM_PROMPT.replace("<BOT_NAME>", bot_name).replace("<GROUP_ID>", group_id)


async def process_message(request: SplitBotRequest) -> str:
    """
    Process a message using Grok 4 Fast via OpenRouter with conversation memory stored in PostgreSQL.
    Uses LangChain agents with checkpoint-based memory.

    Args:
        request: SplitBotRequest object containing message, group_id, sender, platform_type, and optionally image_url

    Returns:
        The AI's response as a string
    """
    platform_type = request.platform_type.strip().upper()
    start_time = time.time()

    # Get user message (OCR processing happens inside to_user_message)
    try:
        user_message = await request.to_user_message()
    except ValueError as e:
        # Return error message if OCR failed
        duration = time.time() - start_time
        ai_processing_duration_seconds.labels(platform_type=platform_type).observe(
            duration
        )
        ai_processing_total.labels(platform_type=platform_type, status="failure").inc()
        ai_processing_errors_total.labels(
            platform_type=platform_type, error_type="OCRError"
        ).inc()
        return str(e)

    # Get environment variables
    base_url = os.getenv("AI_BASE_URL")
    api_token = os.getenv("AI_TOKEN")
    db_connection_string = os.getenv("DB_CONNECTION_STRING")

    if not base_url:
        error = ValueError("AI_BASE_URL environment variable is required")
        duration = time.time() - start_time
        ai_processing_duration_seconds.labels(platform_type=platform_type).observe(
            duration
        )
        ai_processing_total.labels(platform_type=platform_type, status="failure").inc()
        ai_processing_errors_total.labels(
            platform_type=platform_type, error_type="ConfigurationError"
        ).inc()
        raise error

    if not api_token:
        error = ValueError("AI_TOKEN environment variable is required")
        duration = time.time() - start_time
        ai_processing_duration_seconds.labels(platform_type=platform_type).observe(
            duration
        )
        ai_processing_total.labels(platform_type=platform_type, status="failure").inc()
        ai_processing_errors_total.labels(
            platform_type=platform_type, error_type="ConfigurationError"
        ).inc()
        raise error

    if not db_connection_string:
        logger.error("DB_CONNECTION_STRING not found in environment variables")
        error = ValueError("DB_CONNECTION_STRING environment variable is required")
        duration = time.time() - start_time
        ai_processing_duration_seconds.labels(platform_type=platform_type).observe(
            duration
        )
        ai_processing_total.labels(platform_type=platform_type, status="failure").inc()
        ai_processing_errors_total.labels(
            platform_type=platform_type, error_type="ConfigurationError"
        ).inc()
        raise error

    try:
        # Initialize ChatOpenAI with OpenRouter configuration
        model = ChatOpenAI(
            model="openai/gpt-5.6-luna",
            base_url=base_url,
            api_key=api_token,
            temperature=0.7,
        )

        # Create agent with checkpointer and trim_messages middleware
        tools = [
            add_expense,
            update_expense,
            delete_expense,
            create_bill_totals,
            get_bill_assignments,
            set_bill_assignments,
            export_bill_to_google_sheet,
        ]

        # Use context manager to properly manage database connection
        with PostgresSaver.from_conn_string(db_connection_string) as checkpointer:
            checkpointer.setup()

            # Create agent within the context manager
            # Use bot_name from request to generate system prompt
            system_prompt_with_bot_name = get_system_prompt(
                request.bot_name, request.group_id
            )
            agent = create_agent(
                model,
                tools,
                system_prompt=system_prompt_with_bot_name,
                checkpointer=checkpointer,
                middleware=[trim_messages],
            )

            # Invoke agent with message and thread_id (group_id)
            # The checkpointer automatically handles message history persistence
            result = agent.invoke(
                {"messages": [{"role": "user", "content": user_message}]},
                {
                    "configurable": {
                        "thread_id": request.group_id,
                    }
                },
            )

            # Extract the AI response from the last message
            ai_response = result["messages"][-1].content

            # Track successful processing
            duration = time.time() - start_time
            ai_processing_duration_seconds.labels(platform_type=platform_type).observe(
                duration
            )
            ai_processing_total.labels(
                platform_type=platform_type, status="success"
            ).inc()

            return ai_response

    except Exception as e:
        duration = time.time() - start_time
        ai_processing_duration_seconds.labels(platform_type=platform_type).observe(
            duration
        )
        ai_processing_total.labels(platform_type=platform_type, status="failure").inc()
        ai_processing_errors_total.labels(
            platform_type=platform_type, error_type=type(e).__name__
        ).inc()
        logger.error(f"Error processing message with AI: {str(e)}")
        raise


@before_model
def trim_messages(state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
    """
    Keep only the last few messages to fit context window.
    Configurable via the max_messages parameter (default: 10 message pairs = 20 messages).
    """
    messages = state["messages"]

    if len(messages) <= MAX_HISTORY_MESSAGES:
        return None  # No changes needed

    recent_messages = messages[-MAX_HISTORY_MESSAGES:]

    return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *recent_messages]}
