# Documentation Index

All project documentation is organized into the following categories.

---

## Standards

Project conventions, coding standards, and CI enforcement.

| Document | Description |
|----------|-------------|
| [CODE_STANDARDS.md](standards/CODE_STANDARDS.md) | Code standards, module organization, directory structure, naming, data access |
| [CI_ENFORCEMENT.md](standards/CI_ENFORCEMENT.md) | Branch protection, required CI checks (Lint, Unit, Integration, Bot, CI Gate) |
| [TOKEN_EFFICIENCY.md](standards/TOKEN_EFFICIENCY.md) | Cursor token usage optimization, .cursorignore strategy |

---

## Testing

Test protocols, backtest methodology, and A/B testing procedures.

| Document | Description |
|----------|-------------|
| [AB_TEST_PROTOCOL.md](testing/AB_TEST_PROTOCOL.md) | A/B test protocol: benchmark dataset, 2x2 matrix, commands |
| [TESTING_PLAN.md](testing/TESTING_PLAN.md) | Strategy validation plan for >65% win rate |
| [BACKTEST_RECOMMENDATION.md](testing/BACKTEST_RECOMMENDATION.md) | Recommendation to keep walk-forward validator vs third-party frameworks |

---

## Deployment

Cloud deployment guides, troubleshooting, and migration.

| Document | Description |
|----------|-------------|
| [DEPLOYMENT_LESSONS.md](deployment/DEPLOYMENT_LESSONS.md) | Docker/cloud deployment gotchas and production debug lessons (merged) |
| [GCP_DEPLOYMENT.md](deployment/GCP_DEPLOYMENT.md) | GCP Compute Engine deployment: IB Gateway, Trading GUI, IAP |
| [OPENCLAW_AWS_DEPLOYMENT.md](deployment/OPENCLAW_AWS_DEPLOYMENT.md) | OpenClaw on AWS: EC2, Discord/Telegram/WhatsApp bots |
| [MIGRATION_GUIDE.md](deployment/MIGRATION_GUIDE.md) | New-laptop setup: Python, git, data dirs, portfolio restore |
| [CITRINI_EMAIL_PIPELINE.md](deployment/CITRINI_EMAIL_PIPELINE.md) | Citrini email-to-Discord pipeline: Gmail OAuth, LLM extraction, AWS scheduling |

---

## Status

Current project status, changelog, worklog, and lessons learned.

| Document | Description |
|----------|-------------|
| [WORKLOG.md](status/WORKLOG.md) | Session handover notes and development history |
| [PROJECT_STATUS.md](status/PROJECT_STATUS.md) | System capabilities, GUI status, improvement ideas |
| [SCANNER_CHANGELOG.md](status/SCANNER_CHANGELOG.md) | Scanner version history (Volume Pullback, etc.) |
| [LESSONS_LEARNED.md](status/LESSONS_LEARNED.md) | Trading insights from backtests and live performance |

---

## Architecture

System design, pipeline architecture, and data health.

| Document | Description |
|----------|-------------|
| [MODULAR_PIPELINE_PLAN.md](architecture/MODULAR_PIPELINE_PLAN.md) | Modular pipeline design: picker -> entry -> exit -> AB test (merged) |
| [IBKR_SYSTEM_DESIGN.md](architecture/IBKR_SYSTEM_DESIGN.md) | IBKR API system design: TWS vs Gateway, auth, concurrency |
| [DATA_HEALTH_CHECKIN.md](architecture/DATA_HEALTH_CHECKIN.md) | Weekly/monthly data health audit: price coverage, fundamentals, cron setup |
