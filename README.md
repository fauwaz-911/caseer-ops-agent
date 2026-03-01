# Ops Agent — AI Executive Assistant

Production-grade modular orchestration system:
**Notion → Reminder Engine → Telegram**

---

## Architecture

```
FastAPI (app.main)
    └── Lifespan: starts/stops APScheduler
          ├── morning_job    [cron 10:00 UTC]
          ├── evening_job    [cron 18:00 UTC]
          └── reminder_job   [interval 30 min]
                  │
          reminder_engine.py
                  │
          notion_client.py ──→ Notion API
                  │
          telegram.py ──→ Telegram Bot API
```

## Module Responsibilities

| Module | Role |
|---|---|
| `config.py` | Env validation, typed Settings singleton |
| `logger.py` | Console (INFO) + rotating JSON file (DEBUG) |
| `main.py` | FastAPI app, lifespan, all control endpoints |
| `scheduler.py` | APScheduler cron/interval jobs with error listeners |
| `reminder_engine.py` | Task urgency classification + Telegram dispatch |
| `notion_client.py` | Notion API queries, typed Task model |
| `telegram.py` | HTTP POST with retry + back-off |

## Setup

```bash
# 1. Clone and install
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Fill in your secrets in .env

# 3. Run locally
uvicorn app.main:app --reload
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Root liveness check |
| `GET /health` | Status + scheduler job details |
| `GET /test-telegram` | Pipeline connectivity test |
| `GET /force-reminder` | Manually run reminder engine |
| `GET /force-morning` | Fire morning brief |
| `GET /force-evening` | Fire evening wrap-up |
| `GET /send-update` | Push current task list to Telegram |

All control endpoints accept `?chat_id=` and `?db_id=` query params for multi-user routing.

## Logs

- **Console**: INFO level, human-readable timestamps
- **File**: `logs/ops_agent.log` — DEBUG level, JSON-structured, rotating (10 MB × 5)

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | — | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | ✅ | — | Target chat/channel ID |
| `NOTION_API_KEY` | ✅ | — | Notion integration secret |
| `NOTION_TASKS_DB_ID` | ✅ | — | Notion database ID |
| `MORNING_HOUR` | — | `10` | Hour for morning brief (UTC) |
| `MORNING_MINUTE` | — | `0` | Minute for morning brief |
| `EVENING_HOUR` | — | `18` | Hour for evening wrap-up |
| `REMINDER_INTERVAL_MINUTES` | — | `30` | Reminder polling interval |
| `SCHEDULER_TIMEZONE` | — | `UTC` | APScheduler timezone |
| `TELEGRAM_MAX_RETRIES` | — | `3` | Telegram send retries |
| `TELEGRAM_RETRY_BACKOFF` | — | `2.0` | Base seconds between retries |

## Deploy to Render

Push to GitHub and connect the repo in Render. Set the four required
environment variables as Render secrets. The `render.yaml` handles the rest.
