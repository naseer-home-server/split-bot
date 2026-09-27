# Split Bot

Python FastAPI core for bill splitting: OCR on receipt images, LangChain agent,
WhatsApp poll orchestration, and Splitwise expense creation.

## Image bill gate (Jev)

After OCR on an inbound image, split-bot calls TypeSafe Jev via OpenRouter
`POST /api/alpha/decisions` (not chat completions). Non-bills, empty/garbled
OCR, and Jev/API failures fail closed: the API returns `response: null` with no
error so WhatsApp ingress stays silent.

## Environment

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `AI_TOKEN` | Yes (existing) | OpenRouter API key used for the chat agent; also used for Jev unless overridden |
| `AI_BASE_URL` | Yes (existing) | OpenRouter-compatible chat base URL for the agent |
| `OPENROUTER_API_KEY` | No | Optional override for Jev Decisions API auth (falls back to `AI_TOKEN`) |
| `JEV_MODEL` | No | Defaults to `typesafe/jev-1.13`; may set `~typesafe/jev-latest` |
| `JEV_BILL_THRESHOLD` | No | Minimum noul `P(yes)` to treat OCR as a bill (default `0.5`) |
| `MISTRAL_API_KEY` | Yes (existing) | Mistral OCR |
| `DB_CONNECTION_STRING` | Yes (existing) | Postgres for LangGraph checkpoints |

Do not commit real API keys. Configure secrets in Coolify.

## Tests

```bash
python -m unittest test_bill_classifier.py
```
