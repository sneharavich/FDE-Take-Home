# Mock Slack Server (for the take-home exercise)

This is a lightweight stand-in for Slack Incoming Webhooks.

It exposes:
- `POST /slack/webhook/{channel}` to receive webhook payloads
- `GET /health`
- `GET /logs?limit=N` to inspect the last N requests
- `GET /` - Web UI for viewing alerts in real-time

## Run locally

From the repo root:

```bash
pip install -r requirements.txt
uvicorn mock_slack.server:app --host 0.0.0.0 --port 9000
```

Then configure the candidate service to send to:

```bash
export SLACK_WEBHOOK_BASE_URL="http://localhost:9000/slack/webhook"
```

A message to channel `amer-risk-alerts` should be POSTed to:

```
http://localhost:9000/slack/webhook/amer-risk-alerts
```

## Failure simulation (recommended during review)

```bash
export MOCK_SLACK_FAIL_RATE_429=0.20
export MOCK_SLACK_FAIL_RATE_500=0.10
export MOCK_SLACK_MIN_RETRY_AFTER=1
export MOCK_SLACK_MAX_RETRY_AFTER=5
```

## Logging

Requests are logged as JSON Lines to:

- `MOCK_SLACK_LOG_PATH` (default: `./mock_slack_requests.jsonl`)

View recent requests:

```bash
tail -n 50 mock_slack_requests.jsonl
```

Or via API:

```bash
curl "http://localhost:9000/logs?limit=50"
```

## Web UI

The server includes a web UI for viewing alerts in real-time:

- Visit `http://localhost:9000/` in your browser
- Or open `../mock_slack_viewer.html` directly in your browser (standalone)

The viewer displays:
- Statistics (total webhooks, success/error counts)
- All received alerts with channel routing, timestamps, and status codes
- Filter by channel (AMER/EMEA/APAC) and status code
- Auto-refresh every 5 seconds

## Optional auth (if you ever expose this publicly)

```bash
export MOCK_SLACK_AUTH_TOKEN="some-secret"
```

Clients must then send:
- Header: `X-Mock-Slack-Token: some-secret`
