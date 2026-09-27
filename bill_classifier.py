"""
Classify OCR text as bill vs non-bill via TypeSafe Jev (OpenRouter Decisions API).

Uses POST https://openrouter.ai/api/alpha/decisions — not chat completions.
Fail closed: empty/garbled OCR, missing config, HTTP errors, and malformed
responses all return False (caller must stay silent).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

import httpx

logger = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.13"
QUESTION_ID = "is_bill"
DEFAULT_THRESHOLD = 0.5


def _openrouter_api_key() -> Optional[str]:
    """Reuse existing OpenRouter credentials; optional dedicated override."""
    return os.getenv("OPENROUTER_API_KEY") or os.getenv("AI_TOKEN")


def _jev_model() -> str:
    return os.getenv("JEV_MODEL", DEFAULT_MODEL)


def _bill_threshold() -> float:
    raw = os.getenv("JEV_BILL_THRESHOLD", str(DEFAULT_THRESHOLD))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid JEV_BILL_THRESHOLD; using default %s", DEFAULT_THRESHOLD)
        return DEFAULT_THRESHOLD
    if value < 0.0 or value > 1.0:
        logger.warning("JEV_BILL_THRESHOLD out of range; using default %s", DEFAULT_THRESHOLD)
        return DEFAULT_THRESHOLD
    return value


def parse_is_bill_noul(
    payload: Mapping[str, Any],
    question_id: str = QUESTION_ID,
) -> Optional[float]:
    """
    Extract the noul (P(yes)) for the bill question from a Decisions API body.

    Returns None when the response shape is missing or unusable.
    """
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        return None
    answer = answers.get(question_id)
    if not isinstance(answer, Mapping):
        return None

    noul = answer.get("noul")
    if isinstance(noul, bool) or not isinstance(noul, (int, float)):
        return None
    value = float(noul)
    if value < 0.0 or value > 1.0:
        return None
    return value


def _build_payload(ocr_text: str) -> dict[str, Any]:
    return {
        "model": _jev_model(),
        "state": {
            "ocr_text": ocr_text,
        },
        "questions": {
            QUESTION_ID: {
                "type": "noul",
                "instructions": (
                    "Does this OCR text come from a customer-facing restaurant, "
                    "cafe, bar, grocery, or similar receipt/bill that people would "
                    "split among diners or sharers?"
                ),
                "criteria": {
                    "true": (
                        "The text is a bill or receipt with purchasable line items "
                        "and/or a total amount due for food, drinks, or similar "
                        "shared expenses."
                    ),
                    "false": (
                        "The text is not a bill/receipt (meme, chat screenshot, ID, "
                        "random photo, ad, unrelated document), or is empty/garbled "
                        "so it cannot be identified as a bill."
                    ),
                },
            }
        },
    }


async def classify_ocr_as_bill(
    ocr_text: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """
    Return True only when Jev confidently classifies OCR as a bill.

    Empty/garbled OCR, missing API key, transport errors, non-200 responses,
    and malformed answers all return False (fail closed). Does not log OCR
    content or API secrets.
    """
    text = (ocr_text or "").strip()
    if not text or text.startswith("Error:"):
        logger.info("Bill classification: empty or failed OCR; treating as non-bill")
        return False

    api_key = _openrouter_api_key()
    if not api_key:
        logger.error("Bill classification: OpenRouter API key not configured")
        return False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = _build_payload(text)

    try:
        if client is None:
            async with httpx.AsyncClient(timeout=30.0) as http_client:
                response = await http_client.post(
                    DECISIONS_URL, headers=headers, json=payload
                )
        else:
            response = await client.post(DECISIONS_URL, headers=headers, json=payload)
    except Exception as exc:
        logger.error("Bill classification request failed: %s", type(exc).__name__)
        return False

    if response.status_code != 200:
        logger.error(
            "Bill classification HTTP error: status=%s", response.status_code
        )
        return False

    try:
        data = response.json()
    except Exception:
        logger.error("Bill classification: response body was not JSON")
        return False

    if not isinstance(data, Mapping):
        logger.error("Bill classification: unexpected JSON root type")
        return False

    noul = parse_is_bill_noul(data)
    if noul is None:
        logger.error("Bill classification: malformed or missing noul answer")
        return False

    threshold = _bill_threshold()
    is_bill = noul >= threshold
    logger.info(
        "Bill classification: is_bill=%s noul=%.3f threshold=%.3f",
        is_bill,
        noul,
        threshold,
    )
    return is_bill
