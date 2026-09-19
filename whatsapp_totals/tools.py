import json
import os
from typing import Any

import httpx
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()


def _whatsapp_base_url() -> str:
    raw = (os.getenv("WHATSAPP_API_URL") or "").strip().rstrip("/")
    if not raw:
        raise ValueError("WHATSAPP_API_URL environment variable is required")
    if not raw.startswith(("http://", "https://")):
        return f"http://{raw}"
    return raw


def _api_error_message(response: httpx.Response) -> str:
    try:
        body: dict[str, Any] = response.json()
        err = body.get("error", "Request failed")
        details = body.get("details")
        extra = []
        for key in ("item_total", "calculated_total", "total_in_bill", "difference"):
            if key in body:
                extra.append(f"{key}={body[key]}")
        parts = [str(err)]
        if details:
            parts.append(str(details))
        if extra:
            parts.append("; ".join(extra))
        return "Error: " + " — ".join(parts)
    except Exception:
        return f"Error: HTTP {response.status_code} — {response.text}"


def _money(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _format_snapshot(data: dict[str, Any]) -> str:
    lines = [
        f"totals_id: {data.get('totals_id')}",
        f"poll_id: {data.get('poll_id')}",
        f"item_total: {_money(data.get('item_total'))}",
        f"calculated_total: {_money(data.get('calculated_total'))}",
        f"total_in_bill: {_money(data.get('total_in_bill'))}",
        f"discount: {_money(data.get('discount'))}",
    ]

    tax = data.get("tax") or []
    if tax:
        tax_bits = []
        for t in tax:
            if isinstance(t, dict):
                tax_bits.append(f"{t.get('name', '?')} x{t.get('multiplier', '?')}")
            else:
                tax_bits.append(repr(t))
        lines.append("tax: " + ", ".join(tax_bits))
    else:
        lines.append("tax: none")

    lines.append("items:")
    items = data.get("items") or []
    assignments = data.get("assignments") or {}
    if not isinstance(assignments, dict):
        assignments = {}
    for item in items:
        if not isinstance(item, dict):
            lines.append(f"- {item!r}")
            continue
        item_id = str(item.get("id", "?"))
        users = assignments.get(item_id) or assignments.get(item.get("id")) or []
        if isinstance(users, list) and users:
            who = ", ".join(str(u) for u in users)
        else:
            who = "unassigned"
        lines.append(
            f"- id {item_id}: {item.get('poll_option', item.get('name', '?'))} "
            f"(qty {item.get('quantity', 1)} × {_money(item.get('unit_price'))}) → {who}"
        )

    unassigned = data.get("unassigned") or []
    if unassigned:
        names = []
        for item in unassigned:
            if isinstance(item, dict):
                names.append(str(item.get("poll_option") or item.get("name") or item.get("id")))
            else:
                names.append(repr(item))
        lines.append("unassigned items: " + ", ".join(names))
        lines.append(f"unassigned_owed: {_money(data.get('unassigned_owed'))}")
    else:
        lines.append("unassigned items: none")

    shares = data.get("shares") or []
    if shares:
        lines.append("shares (use these owed amounts; do not recalculate):")
        for share in shares:
            if not isinstance(share, dict):
                lines.append(f"- {share!r}")
                continue
            lines.append(
                f"- {share.get('user_id', '?')} food {_money(share.get('food_subtotal'))} "
                f"owes {_money(share.get('owed'))}"
            )
            for piece in share.get("items") or []:
                if isinstance(piece, dict):
                    lines.append(
                        f"    - id {piece.get('id', '?')}: {piece.get('name', '?')} "
                        f"share {_money(piece.get('share'))}"
                    )
    else:
        lines.append("shares: none (no one assigned yet)")

    lines.append("When calling set_bill_assignments, use item id values from this list (they match poll option numbers).")
    return "\n".join(lines)


@tool
def create_bill_totals(
    group_id: str,
    items_json: str,
    tax_json: str,
    total_in_bill: float,
    discount: float = 0.0,
    title: str = "Who had what?",
) -> str:
    """
    Create a stateful bill totals record and a WhatsApp poll of items.

    The server creates one poll option per line (quantity is included in the label).
    Everyone who votes for an option shares that line's full quantity × unit_price.
    It validates item_total * tax_multipliers - discount against total_in_bill, then sends the poll.

    Args:
        group_id: The group id of the chat you are in (pass <GROUP_ID> exactly).
        items_json: JSON array of {name, quantity, unit_price}. One object per bill line; do not
            expand quantity into duplicate items. Example:
            '[{"name":"Pizza","quantity":2,"unit_price":18},{"name":"Salad","quantity":1,"unit_price":12}]'
        tax_json: JSON array of {name, multiplier}. 10% is 1.1, 9% is 1.09. Use '[]' if none.
            Example: '[{"name":"GST","multiplier":1.1}]'
        total_in_bill: The amount the bill says is due.
        discount: Discount amount after tax; 0 if none.
        title: Short poll title.

    Returns:
        totals_id and poll_id on success, or a mismatch error with calculated vs billed totals.
        On mismatch, show the user the items, quantities, prices, tax, discount, total_in_bill,
        and calculated_total from the error; wait for them to correct a number or send another
        bill image before calling this tool again.
    """
    try:
        try:
            items = json.loads(items_json)
        except json.JSONDecodeError as e:
            return f"Error: items_json must be valid JSON — {e}"
        if not isinstance(items, list) or len(items) < 1:
            return "Error: items_json must be a non-empty JSON array of objects"

        try:
            tax = json.loads(tax_json) if tax_json.strip() else []
        except json.JSONDecodeError as e:
            return f"Error: tax_json must be valid JSON — {e}"
        if not isinstance(tax, list):
            return "Error: tax_json must be a JSON array of objects"

        payload = {
            "group_id": group_id,
            "title": title,
            "items": items,
            "tax": tax,
            "discount": discount,
            "total_in_bill": total_in_bill,
        }
        url = f"{_whatsapp_base_url()}/totals/create"
        with httpx.Client() as client:
            response = client.post(url, json=payload, timeout=30.0)

        if response.status_code != 200:
            return _api_error_message(response)

        data = response.json()
        if data.get("status") == "success" and data.get("totals_id") is not None:
            return (
                "Bill totals created successfully. "
                f"totals_id: {data['totals_id']}. "
                f"poll_id: {data.get('poll_id')}. "
                f"item_total: {_money(data.get('item_total'))}. "
                f"calculated_total: {_money(data.get('calculated_total'))}. "
                f"total_in_bill: {_money(data.get('total_in_bill'))}. "
                "Remember totals_id for get_bill_assignments and set_bill_assignments."
            )
        return f"Error: unexpected response — {data!r}"
    except httpx.HTTPError as e:
        return f"Error: HTTP request failed — {e}"
    except Exception as e:
        return f"Error: {e}"


@tool
def get_bill_assignments(totals_id: str) -> str:
    """
    Fetch current item assignments and computed per-person owed amounts for a bill totals record.

    Use this when users are done voting, ask to split, or assign items in chat.
    Use the owed amounts returned here for the split.

    Args:
        totals_id: The totals_id returned by create_bill_totals.

    Returns:
        Items, assignments, unassigned items, and per-person shares, or an error message.
    """
    try:
        url = f"{_whatsapp_base_url()}/totals/assignments"
        with httpx.Client() as client:
            response = client.get(url, params={"totals_id": totals_id}, timeout=30.0)

        if response.status_code != 200:
            return _api_error_message(response)

        data = response.json()
        if data.get("status") != "success":
            return f"Error: unexpected response — {data!r}"
        return _format_snapshot(data)
    except httpx.HTTPError as e:
        return f"Error: HTTP request failed — {e}"
    except Exception as e:
        return f"Error: {e}"


@tool
def set_bill_assignments(totals_id: str, assignments_json: str) -> str:
    """
    Set or update who is assigned to which bill item. Use when users assign items in chat
    instead of (or in addition to) voting on the poll.

    Args:
        totals_id: The totals_id returned by create_bill_totals.
        assignments_json: JSON object mapping item id to an array of user ids. Item ids match
            poll option numbers from get_bill_assignments (e.g. "1", "2"). An empty array
            unassigns that item. Other items are left unchanged. Example:
            '{"1":["60123456789"],"2":["60123456789","60987654321"]}'

    Returns:
        Updated assignments and shares, or an error message.
    """
    try:
        try:
            assignments = json.loads(assignments_json)
        except json.JSONDecodeError as e:
            return f"Error: assignments_json must be valid JSON — {e}"
        if not isinstance(assignments, dict):
            return "Error: assignments_json must be a JSON object of item id → user id list"

        parsed: dict[str, list[str]] = {}
        for key, users in assignments.items():
            if not isinstance(users, list):
                return f"Error: assignments[{key!r}] must be an array of user ids"
            parsed[str(key)] = [str(u) for u in users]

        payload = {"totals_id": int(totals_id), "assignments": parsed}
        url = f"{_whatsapp_base_url()}/totals/assignments"
        with httpx.Client() as client:
            response = client.put(url, json=payload, timeout=30.0)

        if response.status_code != 200:
            return _api_error_message(response)

        data = response.json()
        if data.get("status") != "success":
            return f"Error: unexpected response — {data!r}"
        return "Assignments updated.\n" + _format_snapshot(data)
    except httpx.HTTPError as e:
        return f"Error: HTTP request failed — {e}"
    except Exception as e:
        return f"Error: {e}"


@tool
def export_bill_to_google_sheet(totals_id: str, extra_people_json: str = "[]") -> str:
    """
    Export the current bill totals to a Google Sheet with live formulas and checkboxes.
    Completely optional: call this only when a user explicitly asks for a spreadsheet or Google Sheet.
    Do not call it as part of the normal split workflow.

    People already in the split get a column. Use extra_people_json to add more name columns.
    Re-exporting the same totals_id updates the existing sheet.

    Args:
        totals_id: The totals_id returned by create_bill_totals.
        extra_people_json: JSON array of extra display names to add as columns, e.g. '["Sam","Lee"]'.
            Use '[]' if no extra people.

    Returns:
        The Google Sheet URL, or an error message.
    """
    try:
        try:
            extra = json.loads(extra_people_json) if extra_people_json.strip() else []
        except json.JSONDecodeError as e:
            return f"Error: extra_people_json must be valid JSON — {e}"
        if not isinstance(extra, list):
            return "Error: extra_people_json must be a JSON array of names"
        names = [str(n).strip() for n in extra if str(n).strip()]

        payload = {"totals_id": int(totals_id), "extra_people": names}
        url = f"{_whatsapp_base_url()}/totals/export-sheet"
        with httpx.Client() as client:
            response = client.post(url, json=payload, timeout=60.0)

        if response.status_code != 200:
            return _api_error_message(response)

        data = response.json()
        if data.get("status") != "success":
            return f"Error: unexpected response — {data!r}"
        sheet_url = data.get("url")
        sheet_id = data.get("sheet_id")
        if not sheet_url:
            return f"Error: unexpected response — {data!r}"
        return f"Google Sheet ready. sheet_id: {sheet_id}. URL: {sheet_url}"
    except httpx.HTTPError as e:
        return f"Error: HTTP request failed — {e}"
    except Exception as e:
        return f"Error: {e}"
