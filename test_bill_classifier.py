"""Unit tests for TypeSafe Jev bill classification (network mocked)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import httpx

from bill_classifier import (
    DECISIONS_URL,
    QUESTION_ID,
    classify_ocr_as_bill,
    parse_is_bill_noul,
)


BILL_OCR = """
# Cafe Luna
1x Pasta Carbonara    18.00
1x House Salad         9.50
Tax 10%                2.75
Total                 30.25
"""

NON_BILL_OCR = """
Happy birthday!
Look at this cute dog at the park.
Sent from my iPhone
"""


def _decisions_handler(noul: float | None, status_code: int = 200, malformed: bool = False):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == DECISIONS_URL
        assert request.method == "POST"
        assert request.headers.get("Authorization", "").startswith("Bearer ")
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"]
        assert "ocr_text" in body["state"]
        assert QUESTION_ID in body["questions"]
        assert body["questions"][QUESTION_ID]["type"] == "noul"

        if status_code != 200:
            return httpx.Response(status_code, json={"error": "upstream"})

        if malformed:
            return httpx.Response(200, json={"answers": {QUESTION_ID: {"type": "noul"}}})

        return httpx.Response(
            200,
            json={
                "id": "gen-dec-test",
                "model": "typesafe/jev-1.13-test",
                "answers": {
                    QUESTION_ID: {"type": "noul", "noul": noul},
                },
                "usage": {"input_tokens": 10, "output_tokens": 1, "cost": 0.0},
            },
        )

    return handler


class ParseIsBillNoulTests(unittest.TestCase):
    def test_valid_noul(self):
        self.assertEqual(
            parse_is_bill_noul(
                {"answers": {QUESTION_ID: {"type": "noul", "noul": 0.91}}}
            ),
            0.91,
        )

    def test_missing_answers(self):
        self.assertIsNone(parse_is_bill_noul({}))

    def test_wrong_question_id(self):
        self.assertIsNone(
            parse_is_bill_noul({"answers": {"other": {"type": "noul", "noul": 0.9}}})
        )

    def test_non_numeric_noul(self):
        self.assertIsNone(
            parse_is_bill_noul(
                {"answers": {QUESTION_ID: {"type": "noul", "noul": "yes"}}}
            )
        )

    def test_out_of_range_noul(self):
        self.assertIsNone(
            parse_is_bill_noul(
                {"answers": {QUESTION_ID: {"type": "noul", "noul": 1.5}}}
            )
        )


class ClassifyOcrAsBillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "AI_TOKEN": "test-openrouter-token",
                "JEV_MODEL": "typesafe/jev-1.13",
                "JEV_BILL_THRESHOLD": "0.5",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()

    async def test_bill_positive(self):
        transport = httpx.MockTransport(_decisions_handler(0.93))
        async with httpx.AsyncClient(transport=transport) as client:
            self.assertTrue(await classify_ocr_as_bill(BILL_OCR, client=client))

    async def test_non_bill_negative(self):
        transport = httpx.MockTransport(_decisions_handler(0.12))
        async with httpx.AsyncClient(transport=transport) as client:
            self.assertFalse(await classify_ocr_as_bill(NON_BILL_OCR, client=client))

    async def test_empty_ocr_fail_closed_without_network(self):
        called = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["count"] += 1
            return httpx.Response(500)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            self.assertFalse(await classify_ocr_as_bill("   ", client=client))
            self.assertFalse(await classify_ocr_as_bill("", client=client))
            self.assertFalse(
                await classify_ocr_as_bill("Error: OCR failed", client=client)
            )
        self.assertEqual(called["count"], 0)

    async def test_malformed_response_fail_closed(self):
        transport = httpx.MockTransport(
            _decisions_handler(None, malformed=True)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            self.assertFalse(await classify_ocr_as_bill(BILL_OCR, client=client))

    async def test_http_error_fail_closed(self):
        transport = httpx.MockTransport(_decisions_handler(0.99, status_code=503))
        async with httpx.AsyncClient(transport=transport) as client:
            self.assertFalse(await classify_ocr_as_bill(BILL_OCR, client=client))

    async def test_transport_error_fail_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            self.assertFalse(await classify_ocr_as_bill(BILL_OCR, client=client))

    async def test_missing_api_key_fail_closed(self):
        with patch.dict(os.environ, {"AI_TOKEN": "", "OPENROUTER_API_KEY": ""}, clear=False):
            # Ensure both are unset for this test
            os.environ.pop("AI_TOKEN", None)
            os.environ.pop("OPENROUTER_API_KEY", None)

            called = {"count": 0}

            def handler(request: httpx.Request) -> httpx.Response:
                called["count"] += 1
                return httpx.Response(200, json={})

            transport = httpx.MockTransport(handler)
            async with httpx.AsyncClient(transport=transport) as client:
                self.assertFalse(await classify_ocr_as_bill(BILL_OCR, client=client))
            self.assertEqual(called["count"], 0)

    async def test_threshold_boundary(self):
        with patch.dict(os.environ, {"JEV_BILL_THRESHOLD": "0.8"}, clear=False):
            transport = httpx.MockTransport(_decisions_handler(0.79))
            async with httpx.AsyncClient(transport=transport) as client:
                self.assertFalse(await classify_ocr_as_bill(BILL_OCR, client=client))

            transport = httpx.MockTransport(_decisions_handler(0.80))
            async with httpx.AsyncClient(transport=transport) as client:
                self.assertTrue(await classify_ocr_as_bill(BILL_OCR, client=client))


class RequireBillOcrIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise SplitBotRequest image gate without invoking the agent."""

    async def test_non_bill_image_raises_silent_skip(self):
        from ai import SilentSkip, SplitBotRequest

        class FakeImage:
            data = "abc"
            mtype = "image/jpeg"

        req = SplitBotRequest(
            message="",
            group_id="g1",
            sender="u1",
            platform_type="WHATSAPP",
            image_base64=FakeImage(),
        )

        with patch("ai.ocr_image_base64", return_value=NON_BILL_OCR), patch(
            "ai.classify_ocr_as_bill", return_value=False
        ):
            with self.assertRaises(SilentSkip):
                await req.to_user_message()

    async def test_bill_image_continues(self):
        from ai import SplitBotRequest

        class FakeImage:
            data = "abc"
            mtype = "image/jpeg"

        req = SplitBotRequest(
            message="here",
            group_id="g1",
            sender="u1",
            platform_type="WHATSAPP",
            image_base64=FakeImage(),
        )

        with patch("ai.ocr_image_base64", return_value=BILL_OCR), patch(
            "ai.classify_ocr_as_bill", return_value=True
        ):
            message = await req.to_user_message()
            self.assertIn("OCR Image Text:", message)
            self.assertIn("Pasta Carbonara", message)

    async def test_empty_ocr_raises_silent_skip(self):
        from ai import SilentSkip, SplitBotRequest

        class FakeImage:
            data = "abc"
            mtype = "image/jpeg"

        req = SplitBotRequest(
            message="",
            group_id="g1",
            sender="u1",
            platform_type="WHATSAPP",
            image_base64=FakeImage(),
        )

        with patch("ai.ocr_image_base64", return_value=""), patch(
            "ai.classify_ocr_as_bill"
        ) as classify:
            with self.assertRaises(SilentSkip):
                await req.to_user_message()
            classify.assert_not_called()

    async def test_process_message_returns_none_on_silent_skip(self):
        from ai import SilentSkip, SplitBotRequest, process_message

        req = SplitBotRequest(
            message="hi",
            group_id="g1",
            sender="u1",
            platform_type="WHATSAPP",
        )

        with patch.object(
            SplitBotRequest, "to_user_message", side_effect=SilentSkip()
        ):
            result = await process_message(req)
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
