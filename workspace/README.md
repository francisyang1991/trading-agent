# OpenClaw Workspace

This workspace contains all the tools and scripts for running OpenClaw AI Assistant with Discord, WhatsApp, and stock trading analysis capabilities.

## Directory Structure

```
workspace/
├── scripts/
│   ├── discord_reader_service.js    # Discord message reader
│   ├── whatsapp_web_service.js      # WhatsApp Web integration
│   ├── whatsapp_business_service.js # WhatsApp Business API
│   ├── stock_message_analyzer.js    # Stock signal analyzer
│   ├── load_secrets.js              # AWS Secrets Manager loader
│   ├── secure_startup.js            # Secure startup checks
│   ├── security_monitor.sh          # Security monitoring script
│   ├── run_stock_analysis.sh        # Analysis runner with cron
│   └── polymarket_research.py       # Polymarket research tool
├── config/
│   ├── discord_servers.json         # Discord server configuration
│   ├── openclaw.json                # OpenClaw configuration
│   └── whatsapp-business.env.example # WhatsApp env template
├── data/
│   └── (analysis results stored here)
└── security_reports/
    └── (security reports stored here)
```

## Quick Start

### 1. Install Dependencies

```bash
cd workspace
npm install
```

### 2. Configure Environment Variables

```bash
# Required for Discord
export DISCORD_BOT_TOKEN="your_discord_token"

# Required for Gemini AI
export GEMINI_API_KEY="your_gemini_key"

# Optional for WhatsApp Business
export WA_PHONE_NUMBER_ID="your_phone_number_id"
export CLOUD_API_ACCESS_TOKEN="your_access_token"
```

### 3. Configure Discord Servers

Edit `config/discord_servers.json` with your server and channel IDs:

```json
{
  "servers": [
    {
      "name": "Your Server",
      "guild_id": "123456789",
      "channels": ["channel_id_1", "channel_id_2"]
    }
  ]
}
```

## Available Commands

### Discord Reader

```bash
# Start Discord bot to read messages
npm run discord

# Or directly:
DISCORD_BOT_TOKEN=your_token node scripts/discord_reader_service.js
```

### WhatsApp (Unofficial)

```bash
# Start WhatsApp Web integration (requires QR scan)
npm run whatsapp
```

### WhatsApp Business API

```bash
# Start WhatsApp Business webhook server
npm run whatsapp-business
```

### Stock Message Analyzer

```bash
# Run analysis (with Discord token)
npm run analyze

# Run analysis (demo mode without Discord)
node scripts/stock_message_analyzer.js

# Schedule hourly analysis via cron
npm run analyze:schedule
```

### Security Monitoring

```bash
# Run security check
npm run security

# Run secure startup validation
npm run startup
```

### AWS Secrets Manager

```bash
# Check AWS credentials
npm run secrets:check

# Load secrets from AWS Secrets Manager
npm run secrets:load
```

### Polymarket Research

```bash
# Show top markets
npm run polymarket

# Search markets
python3 scripts/polymarket_research.py --search "election"

# Filter by category
python3 scripts/polymarket_research.py --category politics

# Output as JSON
python3 scripts/polymarket_research.py --json
```

## Configuration Files

### discord_servers.json

Configure which Discord servers and channels to monitor:

- `servers`: Array of server configurations
  - `name`: Human-readable name
  - `guild_id`: Discord server ID
  - `channels`: Array of channel IDs to monitor
  - `enabled`: Whether to include in analysis

- `keywords`: Trading signal keywords
  - `bullish`: Words indicating bullish sentiment
  - `bearish`: Words indicating bearish sentiment
  - `tickers`: Known stock ticker symbols

### openclaw.json

OpenClaw gateway configuration including:

- Model settings
- Gateway port and auth
- Plugin configurations
- Custom tool definitions

## Output

Analysis results are saved to the `data/` directory:

- `stock_analysis_<timestamp>.json` - Full analysis data
- `stock_analysis_<timestamp>_summary.txt` - Human-readable summary

Security reports are saved to `security_reports/`.

## Security Notes

1. **Never commit** `.env` files or files containing API keys
2. Use **AWS Secrets Manager** in production
3. Run **security_monitor.sh** regularly
4. Keep dependencies updated

## Troubleshooting

### Discord Bot Not Connecting

1. Verify `DISCORD_BOT_TOKEN` is set
2. Check bot has been invited to servers
3. Ensure Message Content Intent is enabled in Discord Developer Portal

### WhatsApp QR Code Not Appearing

1. Ensure Chromium is installed: `npx puppeteer browsers install chrome`
2. Check for Puppeteer errors in console

### AWS Secrets Not Loading

1. Run `npm run secrets:check` to verify credentials
2. Ensure IAM role has `secretsmanager:GetSecretValue` permission
3. Check secret exists in correct region

## License

MIT
