# OpenClaw AWS Deployment Guide

This guide covers deploying OpenClaw AI Assistant on Amazon Web Services (AWS) for 24/7 operation with Discord, Telegram, and WhatsApp integration.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Amazon Web Services (AWS)                           │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    EC2 Instance (t3.small)                            │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │                    OpenClaw Gateway                             │  │  │
│  │  │  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │  │  │
│  │  │  │   Discord    │  │  Telegram    │  │      WhatsApp         │ │  │  │
│  │  │  │     Bot      │  │     Bot      │  │  Business/Web.js      │ │  │  │
│  │  │  └──────────────┘  └──────────────┘  └───────────────────────┘ │  │  │
│  │  │                                                                 │  │  │
│  │  │  ┌──────────────────────────────────────────────────────────┐  │  │  │
│  │  │  │              Stock Message Analyzer                      │  │  │  │
│  │  │  │  - Multi-server Discord Reader                          │  │  │  │
│  │  │  │  - Trading Signal Extraction                            │  │  │  │
│  │  │  │  - Sentiment Analysis                                   │  │  │  │
│  │  │  └──────────────────────────────────────────────────────────┘  │  │  │
│  │  │                                                                 │  │  │
│  │  │  Model: Gemini 3 Pro Preview  │  Port: 18789 (WebSocket)       │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                     │                                       │
│  ┌─────────────────┐    ┌──────────────────────┐    ┌─────────────────┐    │
│  │ Secrets Manager │    │   Security Group     │    │  CloudWatch     │    │
│  │   (API Keys)    │    │  (UFW + fail2ban)    │    │    (Logs)       │    │
│  └─────────────────┘    └──────────────────────┘    └─────────────────┘    │
│                                     │                                       │
└─────────────────────────────────────┼───────────────────────────────────────┘
                                      │
                                SSH (Key-based only)
```

## Prerequisites

1. **AWS Account** with billing enabled
2. **AWS CLI** installed and configured locally (`aws configure`)
3. **SSH Key Pair** (will be created automatically)
4. **API Keys**:
   - Google Gemini API Key (for AI model)
   - Discord Bot Token (optional, for Discord integration)
   - Telegram Bot Token (optional, for Telegram integration)
   - WhatsApp Business API credentials (optional, for WhatsApp Business)
5. **For WhatsApp Integration**:
   - WhatsApp Business Account (for Business API)
   - Or WhatsApp personal account (for whatsapp-web.js)
6. **For Discord Multi-Server Access**:
   - Bot invited to target servers with `Read Message History` permission
   - Server/Guild IDs and Channel IDs for monitoring
7. **For Enhanced Security**:
   - AWS IAM role with Secrets Manager access (recommended)
   - Static IP address for SSH access restriction

---

## Step 1: Configure AWS CLI

```bash
# Verify AWS CLI is installed
aws --version

# Configure AWS credentials (if not already done)
aws configure
# Enter:
# - AWS Access Key ID
# - AWS Secret Access Key
# - Default region: us-west-2 (or your preferred region)
# - Default output format: json
```

---

## Step 2: Create EC2 Instance

### Launch Instance with User Data Script

```bash
# Create user-data script for initial setup
cat > /tmp/openclaw-userdata.sh << 'EOF'
#!/bin/bash
# Update system
apt-get update -y
apt-get upgrade -y

# Install Node.js v22
curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs

# Install Docker
apt-get install -y docker.io
systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# Install OpenClaw globally
npm install -g openclaw@latest

# Create OpenClaw directory
mkdir -p /home/ubuntu/.openclaw
chown -R ubuntu:ubuntu /home/ubuntu/.openclaw
EOF

# Create EC2 instance with user-data
aws ec2 run-instances \
    --image-id ami-0c65adc9a9c7656f7 \
    --instance-type t3.small \
    --key-name openclaw-key \
    --security-group-ids sg-xxxxxxxxx \
    --user-data file:///tmp/openclaw-userdata.sh \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=openclaw-gateway}]' \
    --region us-west-2

# Note: Replace:
# - ami-0c65adc9a9c7656f7 with Ubuntu 22.04 LTS AMI ID for your region
# - sg-xxxxxxxxx with your security group ID (or create one first)
```

### Alternative: Create Instance via AWS Console

1. Go to **EC2 Dashboard** → **Launch Instance**
2. **Name**: `openclaw-gateway`
3. **AMI**: Ubuntu Server 22.04 LTS
4. **Instance Type**: `t3.small` (2 vCPU, 2 GB RAM)
5. **Key Pair**: Create new or select existing
6. **Network Settings**: 
   - Create security group allowing SSH (port 22) from your IP
   - Optionally allow port 18789 for direct access
7. **Advanced Details** → **User Data**: Paste the user-data script above
8. **Launch Instance**

### Get Instance IP Address

```bash
# Get public IP
aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=openclaw-gateway" \
    --query 'Reservations[*].Instances[*].[PublicIpAddress,State.Name]' \
    --output table

# Or check AWS Console → EC2 → Instances
```

---

## Step 3: Configure Security Group

```bash
# Create security group (if not exists)
aws ec2 create-security-group \
    --group-name openclaw-sg \
    --description "OpenClaw Gateway Security Group" \
    --region us-west-2

# Allow SSH from your IP (replace YOUR_IP)
aws ec2 authorize-security-group-ingress \
    --group-name openclaw-sg \
    --protocol tcp \
    --port 22 \
    --cidr YOUR_IP/32 \
    --region us-west-2

# Optional: Allow OpenClaw port (only if needed for direct access)
aws ec2 authorize-security-group-ingress \
    --group-name openclaw-sg \
    --protocol tcp \
    --port 18789 \
    --cidr YOUR_IP/32 \
    --region us-west-2
```

---

## Step 4: SSH into Instance

```bash
# Save SSH key locally (if created via AWS Console)
# Download the .pem file and set permissions
chmod 400 ~/.ssh/openclaw-key.pem

# SSH into instance
ssh -i ~/.ssh/openclaw-key.pem ubuntu@YOUR_INSTANCE_IP

# Verify installations
node --version    # Should show v22.x.x
docker --version  # Should show Docker version
openclaw --version  # Should show OpenClaw version
```

---

## Step 5: Configure OpenClaw

### Initial Configuration

```bash
# Run initial setup wizard
openclaw onboard

# Or configure manually
openclaw configure --section model
openclaw configure --section gateway
openclaw configure --section web
```

### Manual Configuration File

Create `~/.openclaw/openclaw.json`:

```json
{
  "agents": {
    "defaults": {
      "model": {
        "primary": "google/gemini-3-pro-preview"
      },
      "maxConcurrent": 8,
      "subagents": {
        "maxConcurrent": 16
      }
    }
  },
  "web": {
    "enabled": true
  },
  "commands": {
    "native": "auto",
    "nativeSkills": "auto"
  },
  "gateway": {
    "port": 18789,
    "mode": "local",
    "auth": {
      "token": "YOUR_RANDOM_TOKEN_HERE"
    }
  },
  "messages": {
    "ackReactionScope": "group-mentions"
  },
  "plugins": {
    "entries": {
      "discord": {
        "enabled": true
      }
    }
  }
}
```

**Generate a random token:**
```bash
openssl rand -hex 16
```

---

## Step 6: Set Up Environment Variables

```bash
# Add to ~/.bashrc
cat >> ~/.bashrc << 'EOF'
export GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
export DISCORD_BOT_TOKEN="YOUR_DISCORD_BOT_TOKEN"
EOF

# Source it
source ~/.bashrc

# Verify
echo $GEMINI_API_KEY
```

---

## Step 7: Add Discord Channel

### Get Discord Bot Token

1. Go to https://discord.com/developers/applications
2. Create New Application → Name it (e.g., "Stock Finder")
3. Go to **Bot** section → **Add Bot**
4. Copy the **Token** (click "Reset Token" if needed)
5. Enable **Message Content Intent** (under Privileged Gateway Intents)
6. Save changes

### Add Discord to OpenClaw

```bash
# Set environment variable
export DISCORD_BOT_TOKEN="YOUR_DISCORD_BOT_TOKEN"

# Add Discord channel
openclaw channels add --channel discord --use-env

# Verify
openclaw channels list
openclaw channels status
```

### Invite Bot to Discord Server

1. Go to **OAuth2** → **URL Generator**
2. Select scopes: `bot`, `applications.commands`
3. Select permissions: `Send Messages`, `Read Message History`
4. Copy the generated URL
5. Open URL in browser → Select server → Authorize

---

## Step 8: Set Up Systemd Service (Auto-Start)

```bash
# Create systemd service file
sudo tee /etc/systemd/system/openclaw.service << 'EOF'
[Unit]
Description=OpenClaw AI Assistant Gateway
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu
Environment="GEMINI_API_KEY=YOUR_GEMINI_API_KEY"
Environment="DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN"
ExecStart=/usr/bin/openclaw gateway --port 18789
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Replace YOUR_GEMINI_API_KEY and YOUR_DISCORD_BOT_TOKEN with actual values
sudo nano /etc/systemd/system/openclaw.service

# Reload systemd and enable service
sudo systemctl daemon-reload
sudo systemctl enable openclaw
sudo systemctl start openclaw

# Check status
sudo systemctl status openclaw
```

---

## Step 9: Install Development Tools (Optional)

```bash
# Install useful tools for agent development
sudo apt-get update
sudo apt-get install -y python3-pip jq curl wget git htop build-essential

# Install Python packages
pip3 install requests beautifulsoup4 pandas

# Install Playwright for browser automation
npm install -g playwright
npx playwright install chromium

# Create workspace directory
mkdir -p ~/workspace/{polymarket,scripts,data}
```

---

## Step 10: Create Example Research Script

```bash
# Create Polymarket research tool
cat > ~/workspace/scripts/polymarket_research.py << 'PYEOF'
#!/usr/bin/env python3
"""
Polymarket Research Tool
Scrapes publicly available market data for analysis
"""

import requests
import json
from datetime import datetime

def get_active_markets(limit=50):
    """Fetch active markets from Polymarket API (public endpoint)"""
    try:
        url = "https://gamma-api.polymarket.com/markets"
        params = {"active": "true", "closed": "false", "limit": limit}
        response = requests.get(url, params=params, timeout=30)
        if response.status_code == 200:
            return response.json()
        return {"error": f"Status {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}

def analyze_market(market_data):
    """Basic analysis of market odds"""
    results = []
    for market in market_data:
        if isinstance(market, dict):
            try:
                volume = float(market.get("volume", 0) or 0)
                liquidity = float(market.get("liquidity", 0) or 0)
            except:
                volume, liquidity = 0, 0
            results.append({
                "question": market.get("question", "Unknown"),
                "volume": volume,
                "liquidity": liquidity,
                "end_date": market.get("endDate", "Unknown"),
                "outcomes": market.get("outcomes", [])
            })
    return sorted(results, key=lambda x: x["volume"], reverse=True)

if __name__ == "__main__":
    print(f"=== Polymarket Research - {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")
    markets = get_active_markets()
    if "error" not in markets:
        print(f"Found {len(markets)} active markets\n")
        analysis = analyze_market(markets)
        print("Top 10 by Volume:\n")
        for i, m in enumerate(analysis[:10], 1):
            print(f"{i}. {m['question'][:60]}...")
            print(f"   Volume: ${m['volume']:,.0f} | Liquidity: ${m['liquidity']:,.0f}")
            print(f"   Ends: {m['end_date']}\n")
    else:
        print(f"Error: {markets['error']}")
PYEOF

chmod +x ~/workspace/scripts/polymarket_research.py

# Test the script
python3 ~/workspace/scripts/polymarket_research.py
```

---

## Step 11: Verify Deployment

```bash
# Check service status
sudo systemctl status openclaw

# View logs
sudo journalctl -u openclaw -f

# Check channel status
GEMINI_API_KEY="YOUR_KEY" DISCORD_BOT_TOKEN="YOUR_TOKEN" openclaw channels status

# Test Discord bot
# Send a message to your Discord bot - it should respond!
```

---

## Step 12: Add Additional Channels (Optional)

### Telegram Setup

1. Message [@BotFather](https://t.me/botfather) on Telegram
2. Send `/newbot` → Follow prompts
3. Copy the bot token
4. Add to OpenClaw:
   ```bash
   export TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_TOKEN"
   openclaw channels add --channel telegram --token "$TELEGRAM_BOT_TOKEN"
   sudo systemctl restart openclaw
   ```

### WhatsApp Setup (Basic)

```bash
# Requires interactive QR code scan
openclaw channels add --channel whatsapp
# Follow prompts to scan QR code with your phone
sudo systemctl restart openclaw
```

---

## Step 13: Enhanced WhatsApp Integration

OpenClaw supports WhatsApp through multiple methods. Choose the one that fits your use case.

### Option A: WhatsApp Business API (Official - Recommended for Production)

The official WhatsApp Business Platform API is recommended for business use cases.

#### Prerequisites

1. **Meta Business Account**: https://business.facebook.com
2. **WhatsApp Business Account**: Register at https://developers.facebook.com
3. **Verified Business Phone Number**

#### Installation

```bash
# Install official WhatsApp SDK
npm install whatsapp

# Or use yarn
yarn add whatsapp
```

#### Configuration

Add to `~/.openclaw/whatsapp-business.env`:

```bash
# WhatsApp Business API Configuration
WA_PHONE_NUMBER_ID="YOUR_PHONE_NUMBER_ID"
CLOUD_API_ACCESS_TOKEN="YOUR_ACCESS_TOKEN"
CLOUD_API_VERSION="v18.0"
WEBHOOK_ENDPOINT="/webhook/whatsapp"
WEBHOOK_VERIFICATION_TOKEN="YOUR_VERIFY_TOKEN"
```

#### Create WhatsApp Business Service

```bash
cat > ~/workspace/scripts/whatsapp_business_service.js << 'JSEOF'
/**
 * WhatsApp Business API Service
 * For sending and receiving messages via official API
 */

const WhatsApp = require('whatsapp');
const express = require('express');

class WhatsAppBusinessService {
  constructor() {
    this.client = new WhatsApp({
      phoneNumberId: process.env.WA_PHONE_NUMBER_ID,
      accessToken: process.env.CLOUD_API_ACCESS_TOKEN,
      version: process.env.CLOUD_API_VERSION || 'v18.0'
    });
  }

  async sendMessage(to, message) {
    try {
      const response = await this.client.messages.text({
        to: to,
        body: message
      });
      console.log(`Message sent: ${response.messages[0].id}`);
      return response;
    } catch (error) {
      console.error('WhatsApp send error:', error);
      throw error;
    }
  }

  async sendTemplate(to, templateName, variables = []) {
    try {
      const response = await this.client.messages.template({
        to: to,
        template: { name: templateName, language: { code: 'en_US' } },
        components: variables.length > 0 ? [{
          type: 'body',
          parameters: variables.map(v => ({ type: 'text', text: v }))
        }] : []
      });
      return response;
    } catch (error) {
      console.error('WhatsApp template error:', error);
      throw error;
    }
  }

  setupWebhook(app) {
    // Webhook verification
    app.get('/webhook/whatsapp', (req, res) => {
      const mode = req.query['hub.mode'];
      const token = req.query['hub.verify_token'];
      const challenge = req.query['hub.challenge'];

      if (mode === 'subscribe' && token === process.env.WEBHOOK_VERIFICATION_TOKEN) {
        res.status(200).send(challenge);
      } else {
        res.sendStatus(403);
      }
    });

    // Receive messages
    app.post('/webhook/whatsapp', (req, res) => {
      const body = req.body;
      
      if (body.object === 'whatsapp_business_account') {
        body.entry?.forEach(entry => {
          entry.changes?.forEach(change => {
            if (change.value.messages) {
              change.value.messages.forEach(msg => {
                this.handleIncomingMessage(msg, change.value.contacts[0]);
              });
            }
          });
        });
      }
      res.sendStatus(200);
    });
  }

  handleIncomingMessage(message, contact) {
    console.log(`Message from ${contact.wa_id}: ${message.text?.body || '[media]'}`);
    // Integrate with OpenClaw agent here
  }
}

module.exports = WhatsAppBusinessService;
JSEOF
```

### Option B: whatsapp-web.js (Unofficial - For Personal/Testing Use)

For personal use or testing, `whatsapp-web.js` provides full WhatsApp Web access.

> **Warning**: This is an unofficial library. WhatsApp may block accounts using unofficial clients. Use at your own risk.

#### Installation

```bash
# Install whatsapp-web.js and puppeteer
npm install whatsapp-web.js puppeteer

# Or with specific Chromium
npm install whatsapp-web.js
npx puppeteer browsers install chrome
```

#### Create WhatsApp Web Service

```bash
cat > ~/workspace/scripts/whatsapp_web_service.js << 'JSEOF'
/**
 * WhatsApp Web Service using whatsapp-web.js
 * Provides full WhatsApp Web functionality
 */

const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');

class WhatsAppWebService {
  constructor(sessionId = 'openclaw-session') {
    this.client = new Client({
      authStrategy: new LocalAuth({ clientId: sessionId }),
      puppeteer: {
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-gpu'
        ]
      }
    });
    
    this.isReady = false;
    this.setupEventHandlers();
  }

  setupEventHandlers() {
    this.client.on('qr', (qr) => {
      console.log('Scan QR code to authenticate:');
      qrcode.generate(qr, { small: true });
    });

    this.client.on('authenticated', () => {
      console.log('WhatsApp authenticated successfully');
    });

    this.client.on('ready', () => {
      console.log('WhatsApp client is ready');
      this.isReady = true;
    });

    this.client.on('message', async (msg) => {
      await this.handleIncomingMessage(msg);
    });

    this.client.on('disconnected', (reason) => {
      console.log('WhatsApp client disconnected:', reason);
      this.isReady = false;
    });
  }

  async initialize() {
    await this.client.initialize();
  }

  async sendMessage(chatId, message) {
    if (!this.isReady) throw new Error('WhatsApp client not ready');
    return await this.client.sendMessage(chatId, message);
  }

  async sendMedia(chatId, filePath, caption = '') {
    if (!this.isReady) throw new Error('WhatsApp client not ready');
    const media = MessageMedia.fromFilePath(filePath);
    return await this.client.sendMessage(chatId, media, { caption });
  }

  async getChats() {
    if (!this.isReady) throw new Error('WhatsApp client not ready');
    return await this.client.getChats();
  }

  async getChatMessages(chatId, limit = 50) {
    if (!this.isReady) throw new Error('WhatsApp client not ready');
    const chat = await this.client.getChatById(chatId);
    return await chat.fetchMessages({ limit });
  }

  async handleIncomingMessage(msg) {
    console.log(`[${new Date().toISOString()}] From: ${msg.from}`);
    console.log(`Message: ${msg.body}`);
    
    // Check if message contains stock-related keywords
    const stockKeywords = ['stock', 'trade', 'buy', 'sell', 'earnings', 'ticker', '$'];
    const isStockRelated = stockKeywords.some(kw => 
      msg.body.toLowerCase().includes(kw)
    );
    
    if (isStockRelated) {
      console.log('Stock-related message detected, forwarding to analyzer...');
      // Forward to stock message analyzer
    }
  }
}

// Usage example
if (require.main === module) {
  const service = new WhatsAppWebService();
  service.initialize().catch(console.error);
}

module.exports = WhatsAppWebService;
JSEOF

# Install required dependencies
npm install qrcode-terminal
```

#### Run WhatsApp Web Service

```bash
# Start the service (first time requires QR scan)
node ~/workspace/scripts/whatsapp_web_service.js
```

---

## Step 14: Discord Server Access and Message Reading

Access other Discord servers and read channel messages using discord.js.

### Prerequisites

1. **Bot Token**: From Discord Developer Portal
2. **Server Permissions**: Bot must be invited with `Read Message History` permission
3. **Channel IDs**: Target channels to monitor

### Installation

```bash
npm install discord.js
```

### Create Discord Message Reader Service

```bash
cat > ~/workspace/scripts/discord_reader_service.js << 'JSEOF'
/**
 * Discord Server Access and Message Reader
 * Reads messages from specified channels across multiple servers
 */

const { Client, GatewayIntentBits, Events, Collection } = require('discord.js');

class DiscordReaderService {
  constructor(token) {
    this.client = new Client({
      intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent,
        GatewayIntentBits.DirectMessages
      ]
    });
    
    this.token = token;
    this.monitoredChannels = new Set();
    this.messageCache = new Collection();
    
    this.setupEventHandlers();
  }

  setupEventHandlers() {
    this.client.on(Events.ClientReady, () => {
      console.log(`Discord bot logged in as ${this.client.user.tag}`);
      console.log(`Connected to ${this.client.guilds.cache.size} servers`);
      this.listServers();
    });

    this.client.on(Events.MessageCreate, async (message) => {
      if (this.monitoredChannels.has(message.channel.id)) {
        await this.processMessage(message);
      }
    });

    this.client.on(Events.Error, (error) => {
      console.error('Discord client error:', error);
    });
  }

  async connect() {
    await this.client.login(this.token);
  }

  listServers() {
    console.log('\n=== Connected Servers ===');
    this.client.guilds.cache.forEach(guild => {
      console.log(`- ${guild.name} (ID: ${guild.id})`);
    });
    console.log('========================\n');
  }

  async listChannels(guildId) {
    const guild = this.client.guilds.cache.get(guildId);
    if (!guild) {
      console.error(`Guild ${guildId} not found`);
      return [];
    }

    const channels = guild.channels.cache
      .filter(ch => ch.isTextBased())
      .map(ch => ({
        id: ch.id,
        name: ch.name,
        type: ch.type
      }));

    console.log(`\nChannels in ${guild.name}:`);
    channels.forEach(ch => console.log(`  #${ch.name} (${ch.id})`));
    
    return channels;
  }

  addMonitoredChannel(channelId) {
    this.monitoredChannels.add(channelId);
    console.log(`Now monitoring channel: ${channelId}`);
  }

  removeMonitoredChannel(channelId) {
    this.monitoredChannels.delete(channelId);
    console.log(`Stopped monitoring channel: ${channelId}`);
  }

  /**
   * Fetch historical messages from a channel
   * @param {string} channelId - Channel ID
   * @param {number} limit - Maximum messages to fetch (max 100 per request)
   * @param {string} before - Message ID to fetch messages before
   */
  async fetchMessages(channelId, limit = 100, before = null) {
    const channel = this.client.channels.cache.get(channelId);
    if (!channel) {
      throw new Error(`Channel ${channelId} not found or not accessible`);
    }

    const options = { limit: Math.min(limit, 100) };
    if (before) options.before = before;

    const messages = await channel.messages.fetch(options);
    return Array.from(messages.values());
  }

  /**
   * Fetch ALL messages from a channel (paginated)
   * @param {string} channelId - Channel ID
   * @param {number} maxMessages - Maximum total messages to fetch
   */
  async fetchAllMessages(channelId, maxMessages = 1000) {
    const allMessages = [];
    let lastMessageId = null;

    while (allMessages.length < maxMessages) {
      const batch = await this.fetchMessages(channelId, 100, lastMessageId);
      
      if (batch.length === 0) break;
      
      allMessages.push(...batch);
      lastMessageId = batch[batch.length - 1].id;

      // Rate limiting - wait 1 second between requests
      await new Promise(resolve => setTimeout(resolve, 1000));
    }

    console.log(`Fetched ${allMessages.length} messages from channel ${channelId}`);
    return allMessages.slice(0, maxMessages);
  }

  async processMessage(message) {
    const msgData = {
      id: message.id,
      content: message.content,
      author: message.author.username,
      timestamp: message.createdAt,
      channelId: message.channel.id,
      channelName: message.channel.name,
      guildName: message.guild?.name || 'DM'
    };

    // Cache the message
    if (!this.messageCache.has(message.channel.id)) {
      this.messageCache.set(message.channel.id, []);
    }
    this.messageCache.get(message.channel.id).push(msgData);

    // Keep only last 1000 messages per channel
    const channelCache = this.messageCache.get(message.channel.id);
    if (channelCache.length > 1000) {
      channelCache.shift();
    }

    return msgData;
  }

  getCachedMessages(channelId) {
    return this.messageCache.get(channelId) || [];
  }

  async disconnect() {
    await this.client.destroy();
    console.log('Discord client disconnected');
  }
}

module.exports = DiscordReaderService;
JSEOF
```

### Configuration for Multiple Servers

Create a configuration file for server credentials:

```bash
cat > ~/.openclaw/discord_servers.json << 'EOF'
{
  "servers": [
    {
      "name": "Trading Signals Alpha",
      "guild_id": "YOUR_GUILD_ID_1",
      "channels": ["CHANNEL_ID_1", "CHANNEL_ID_2"],
      "description": "Primary trading signals server"
    },
    {
      "name": "Stock Discussion Beta",
      "guild_id": "YOUR_GUILD_ID_2", 
      "channels": ["CHANNEL_ID_3"],
      "description": "General stock discussion"
    }
  ],
  "keywords": {
    "bullish": ["buy", "long", "calls", "bullish", "moon", "breakout", "upgrade"],
    "bearish": ["sell", "short", "puts", "bearish", "dump", "breakdown", "downgrade"],
    "tickers": ["$AAPL", "$TSLA", "$NVDA", "$SPY", "$QQQ"]
  }
}
EOF
```

---

## Step 15: Stock Trading Information Summarizer

Automatically read messages from monitored channels and summarize valuable stock trading information.

### Create Stock Message Analyzer

```bash
cat > ~/workspace/scripts/stock_message_analyzer.js << 'JSEOF'
/**
 * Stock Trading Message Analyzer
 * Reads messages from Discord/WhatsApp and extracts trading signals
 */

const DiscordReaderService = require('./discord_reader_service');
const fs = require('fs');
const path = require('path');

class StockMessageAnalyzer {
  constructor(discordToken) {
    this.discord = new DiscordReaderService(discordToken);
    this.config = this.loadConfig();
    this.summaries = [];
  }

  loadConfig() {
    const configPath = path.join(process.env.HOME, '.openclaw/discord_servers.json');
    if (fs.existsSync(configPath)) {
      return JSON.parse(fs.readFileSync(configPath, 'utf8'));
    }
    return { servers: [], keywords: { bullish: [], bearish: [], tickers: [] } };
  }

  /**
   * Extract ticker symbols from message text
   */
  extractTickers(text) {
    // Match $TICKER format
    const dollarTickers = text.match(/\$[A-Z]{1,5}/g) || [];
    
    // Match standalone uppercase 1-5 letter words that might be tickers
    const potentialTickers = text.match(/\b[A-Z]{1,5}\b/g) || [];
    
    // Filter known tickers
    const knownTickers = this.config.keywords?.tickers || [];
    const matched = potentialTickers.filter(t => 
      knownTickers.includes(`$${t}`) || knownTickers.includes(t)
    );

    return [...new Set([...dollarTickers, ...matched.map(t => `$${t}`)])];
  }

  /**
   * Analyze sentiment of a message
   */
  analyzeSentiment(text) {
    const lower = text.toLowerCase();
    const bullish = this.config.keywords?.bullish || [];
    const bearish = this.config.keywords?.bearish || [];

    let bullScore = 0;
    let bearScore = 0;

    bullish.forEach(word => {
      if (lower.includes(word)) bullScore++;
    });

    bearish.forEach(word => {
      if (lower.includes(word)) bearScore++;
    });

    if (bullScore > bearScore) return { sentiment: 'bullish', confidence: bullScore };
    if (bearScore > bullScore) return { sentiment: 'bearish', confidence: bearScore };
    return { sentiment: 'neutral', confidence: 0 };
  }

  /**
   * Extract price targets from message
   */
  extractPriceTargets(text) {
    const targets = [];
    
    // Match patterns like "PT $150", "target 150", "price target: $150"
    const patterns = [
      /(?:PT|price target|target)[:\s]*\$?(\d+(?:\.\d{2})?)/gi,
      /\$(\d+(?:\.\d{2})?)\s*(?:PT|target)/gi
    ];

    patterns.forEach(pattern => {
      const matches = text.matchAll(pattern);
      for (const match of matches) {
        targets.push(parseFloat(match[1]));
      }
    });

    return targets;
  }

  /**
   * Analyze a single message
   */
  analyzeMessage(message) {
    const tickers = this.extractTickers(message.content);
    const sentiment = this.analyzeSentiment(message.content);
    const priceTargets = this.extractPriceTargets(message.content);

    // Only include if message has trading-relevant content
    if (tickers.length === 0 && sentiment.sentiment === 'neutral') {
      return null;
    }

    return {
      timestamp: message.timestamp || message.createdAt,
      author: message.author || message.author?.username,
      channel: message.channelName || 'unknown',
      guild: message.guildName || 'unknown',
      tickers,
      sentiment: sentiment.sentiment,
      sentimentConfidence: sentiment.confidence,
      priceTargets,
      originalMessage: message.content.substring(0, 200)
    };
  }

  /**
   * Fetch and analyze all messages from configured channels
   */
  async analyzeAllChannels() {
    await this.discord.connect();
    
    // Wait for client to be ready
    await new Promise(resolve => setTimeout(resolve, 3000));

    const results = {
      timestamp: new Date().toISOString(),
      totalMessages: 0,
      relevantMessages: 0,
      byTicker: {},
      bySentiment: { bullish: [], bearish: [], neutral: [] },
      summary: ''
    };

    for (const server of this.config.servers) {
      console.log(`\nAnalyzing server: ${server.name}`);
      
      for (const channelId of server.channels) {
        try {
          console.log(`  Fetching messages from channel: ${channelId}`);
          const messages = await this.discord.fetchAllMessages(channelId, 500);
          results.totalMessages += messages.length;

          for (const msg of messages) {
            const analysis = this.analyzeMessage({
              content: msg.content,
              timestamp: msg.createdAt,
              author: msg.author?.username,
              channelName: msg.channel?.name,
              guildName: server.name
            });

            if (analysis) {
              results.relevantMessages++;
              
              // Group by ticker
              analysis.tickers.forEach(ticker => {
                if (!results.byTicker[ticker]) {
                  results.byTicker[ticker] = [];
                }
                results.byTicker[ticker].push(analysis);
              });

              // Group by sentiment
              results.bySentiment[analysis.sentiment].push(analysis);
            }
          }
        } catch (error) {
          console.error(`  Error fetching channel ${channelId}:`, error.message);
        }
      }
    }

    results.summary = this.generateSummary(results);
    await this.discord.disconnect();
    
    return results;
  }

  /**
   * Generate human-readable summary
   */
  generateSummary(results) {
    const lines = [
      `=== Stock Trading Intelligence Summary ===`,
      `Generated: ${results.timestamp}`,
      `Total messages analyzed: ${results.totalMessages}`,
      `Trading-relevant messages: ${results.relevantMessages}`,
      ``,
      `=== Sentiment Overview ===`,
      `Bullish signals: ${results.bySentiment.bullish.length}`,
      `Bearish signals: ${results.bySentiment.bearish.length}`,
      `Neutral mentions: ${results.bySentiment.neutral.length}`,
      ``
    ];

    // Top mentioned tickers
    const tickerCounts = Object.entries(results.byTicker)
      .map(([ticker, mentions]) => ({ ticker, count: mentions.length }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10);

    if (tickerCounts.length > 0) {
      lines.push(`=== Top Mentioned Tickers ===`);
      tickerCounts.forEach((t, i) => {
        const sentiments = results.byTicker[t.ticker];
        const bullish = sentiments.filter(s => s.sentiment === 'bullish').length;
        const bearish = sentiments.filter(s => s.sentiment === 'bearish').length;
        lines.push(`${i + 1}. ${t.ticker}: ${t.count} mentions (${bullish} bullish, ${bearish} bearish)`);
      });
      lines.push('');
    }

    // Recent actionable signals
    const recentBullish = results.bySentiment.bullish
      .filter(s => s.sentimentConfidence >= 2)
      .slice(0, 5);
    
    if (recentBullish.length > 0) {
      lines.push(`=== High Confidence Bullish Signals ===`);
      recentBullish.forEach(s => {
        lines.push(`- ${s.tickers.join(', ')} (${s.channel}): "${s.originalMessage.substring(0, 80)}..."`);
      });
      lines.push('');
    }

    const recentBearish = results.bySentiment.bearish
      .filter(s => s.sentimentConfidence >= 2)
      .slice(0, 5);
    
    if (recentBearish.length > 0) {
      lines.push(`=== High Confidence Bearish Signals ===`);
      recentBearish.forEach(s => {
        lines.push(`- ${s.tickers.join(', ')} (${s.channel}): "${s.originalMessage.substring(0, 80)}..."`);
      });
    }

    return lines.join('\n');
  }

  /**
   * Save analysis results to file
   */
  async saveResults(results, outputPath) {
    const output = {
      ...results,
      generatedAt: new Date().toISOString()
    };

    fs.writeFileSync(outputPath, JSON.stringify(output, null, 2));
    console.log(`\nResults saved to: ${outputPath}`);

    // Also save human-readable summary
    const summaryPath = outputPath.replace('.json', '_summary.txt');
    fs.writeFileSync(summaryPath, results.summary);
    console.log(`Summary saved to: ${summaryPath}`);
  }
}

// CLI usage
if (require.main === module) {
  const token = process.env.DISCORD_BOT_TOKEN;
  if (!token) {
    console.error('DISCORD_BOT_TOKEN environment variable required');
    process.exit(1);
  }

  const analyzer = new StockMessageAnalyzer(token);
  
  analyzer.analyzeAllChannels()
    .then(results => {
      console.log('\n' + results.summary);
      return analyzer.saveResults(results, `${process.env.HOME}/workspace/data/stock_analysis_${Date.now()}.json`);
    })
    .catch(error => {
      console.error('Analysis failed:', error);
      process.exit(1);
    });
}

module.exports = StockMessageAnalyzer;
JSEOF

# Create data directory
mkdir -p ~/workspace/data
```

### Scheduled Analysis Job

```bash
# Create cron job for hourly analysis
cat > ~/workspace/scripts/run_stock_analysis.sh << 'EOF'
#!/bin/bash
export DISCORD_BOT_TOKEN="YOUR_DISCORD_BOT_TOKEN"
cd ~/workspace/scripts
node stock_message_analyzer.js >> ~/workspace/data/analysis.log 2>&1
EOF

chmod +x ~/workspace/scripts/run_stock_analysis.sh

# Add to crontab (runs every hour)
(crontab -l 2>/dev/null; echo "0 * * * * ~/workspace/scripts/run_stock_analysis.sh") | crontab -
```

### Integration with OpenClaw

Add analysis results to OpenClaw's context:

```bash
cat >> ~/.openclaw/openclaw.json << 'EOF'
{
  "customTools": {
    "stockAnalysis": {
      "script": "~/workspace/scripts/stock_message_analyzer.js",
      "description": "Analyze Discord channels for stock trading signals"
    }
  }
}
EOF
```

---

## Step 16: Enhanced Security Configuration

Implement comprehensive security measures for production deployment.

### A. AWS Secrets Manager Integration

Store sensitive credentials in AWS Secrets Manager instead of environment variables.

```bash
# Create secrets in AWS Secrets Manager
aws secretsmanager create-secret \
    --name openclaw/production/api-keys \
    --description "OpenClaw API keys" \
    --secret-string '{
        "GEMINI_API_KEY": "YOUR_GEMINI_KEY",
        "DISCORD_BOT_TOKEN": "YOUR_DISCORD_TOKEN",
        "WHATSAPP_ACCESS_TOKEN": "YOUR_WHATSAPP_TOKEN"
    }' \
    --region us-west-2

# Create IAM policy for secrets access
aws iam create-policy \
    --policy-name OpenClawSecretsAccess \
    --policy-document '{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:DescribeSecret"
                ],
                "Resource": "arn:aws:secretsmanager:us-west-2:*:secret:openclaw/*"
            }
        ]
    }'

# Attach policy to EC2 instance role
aws iam attach-role-policy \
    --role-name OpenClawEC2Role \
    --policy-arn arn:aws:iam::YOUR_ACCOUNT_ID:policy/OpenClawSecretsAccess
```

#### Secrets Loader Script

```bash
cat > ~/workspace/scripts/load_secrets.js << 'JSEOF'
/**
 * AWS Secrets Manager Loader
 * Loads secrets at runtime instead of using environment variables
 */

const { SecretsManagerClient, GetSecretValueCommand } = require('@aws-sdk/client-secrets-manager');

class SecretsLoader {
  constructor(region = 'us-west-2') {
    this.client = new SecretsManagerClient({ region });
    this.cache = new Map();
    this.cacheExpiry = 5 * 60 * 1000; // 5 minutes
  }

  async getSecret(secretName) {
    // Check cache first
    const cached = this.cache.get(secretName);
    if (cached && Date.now() < cached.expiry) {
      return cached.value;
    }

    try {
      const command = new GetSecretValueCommand({ SecretId: secretName });
      const response = await this.client.send(command);
      
      let secretValue;
      if (response.SecretString) {
        secretValue = JSON.parse(response.SecretString);
      } else {
        const buff = Buffer.from(response.SecretBinary, 'base64');
        secretValue = JSON.parse(buff.toString('ascii'));
      }

      // Cache the secret
      this.cache.set(secretName, {
        value: secretValue,
        expiry: Date.now() + this.cacheExpiry
      });

      return secretValue;
    } catch (error) {
      console.error(`Failed to load secret ${secretName}:`, error.message);
      throw error;
    }
  }

  async loadAllSecrets() {
    const secrets = await this.getSecret('openclaw/production/api-keys');
    
    // Set as environment variables for compatibility
    Object.entries(secrets).forEach(([key, value]) => {
      process.env[key] = value;
    });

    console.log('Secrets loaded successfully');
    return secrets;
  }
}

module.exports = SecretsLoader;
JSEOF

# Install AWS SDK
npm install @aws-sdk/client-secrets-manager
```

### B. Network Security Hardening

```bash
# Create restrictive security group
aws ec2 create-security-group \
    --group-name openclaw-production-sg \
    --description "Production security group with strict rules" \
    --vpc-id YOUR_VPC_ID \
    --region us-west-2

# Get security group ID
SG_ID=$(aws ec2 describe-security-groups \
    --group-names openclaw-production-sg \
    --query 'SecurityGroups[0].GroupId' \
    --output text)

# Allow SSH only from specific IP (your office/home IP)
aws ec2 authorize-security-group-ingress \
    --group-id $SG_ID \
    --protocol tcp \
    --port 22 \
    --cidr YOUR_TRUSTED_IP/32

# Allow HTTPS outbound only (for API calls)
aws ec2 authorize-security-group-egress \
    --group-id $SG_ID \
    --protocol tcp \
    --port 443 \
    --cidr 0.0.0.0/0

# Block all other inbound traffic by default (VPC default)
```

### C. System Hardening on EC2

```bash
# SSH into instance and run hardening script
cat > /tmp/harden_system.sh << 'EOF'
#!/bin/bash
set -e

echo "=== OpenClaw Security Hardening Script ==="

# 1. System updates
echo "[1/8] Updating system packages..."
apt-get update -y
apt-get upgrade -y
apt-get autoremove -y

# 2. Configure automatic security updates
echo "[2/8] Enabling automatic security updates..."
apt-get install -y unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades << 'APTCONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
APTCONF

# 3. SSH hardening
echo "[3/8] Hardening SSH configuration..."
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup
cat >> /etc/ssh/sshd_config << 'SSHCONF'

# Security hardening
PermitRootLogin no
PasswordAuthentication no
MaxAuthTries 3
LoginGraceTime 30
ClientAliveInterval 300
ClientAliveCountMax 2
AllowUsers ubuntu
SSHCONF

# 4. Configure firewall (UFW)
echo "[4/8] Configuring firewall..."
apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw --force enable

# 5. Install and configure fail2ban
echo "[5/8] Installing fail2ban..."
apt-get install -y fail2ban
cat > /etc/fail2ban/jail.local << 'F2BCONF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 3

[sshd]
enabled = true
port = 22
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 86400
F2BCONF
systemctl enable fail2ban
systemctl start fail2ban

# 6. Disable unnecessary services
echo "[6/8] Disabling unnecessary services..."
systemctl disable --now cups 2>/dev/null || true
systemctl disable --now avahi-daemon 2>/dev/null || true

# 7. Set up audit logging
echo "[7/8] Configuring audit logging..."
apt-get install -y auditd
systemctl enable auditd
systemctl start auditd

# Add audit rules for sensitive files
cat > /etc/audit/rules.d/openclaw.rules << 'AUDITCONF'
-w /home/ubuntu/.openclaw/ -p wa -k openclaw_config
-w /etc/systemd/system/openclaw.service -p wa -k openclaw_service
-w /var/log/auth.log -p wa -k auth_log
AUDITCONF

# 8. Set file permissions
echo "[8/8] Setting secure file permissions..."
chmod 700 /home/ubuntu/.openclaw
chmod 600 /home/ubuntu/.openclaw/*.json 2>/dev/null || true
chmod 600 /home/ubuntu/.ssh/authorized_keys

# Restart services
systemctl restart sshd
systemctl restart auditd

echo "=== Security hardening complete ==="
EOF

chmod +x /tmp/harden_system.sh
sudo /tmp/harden_system.sh
```

### D. Application-Level Security

```bash
# Create secure startup script
cat > ~/workspace/scripts/secure_startup.js << 'JSEOF'
/**
 * Secure OpenClaw Startup
 * Implements security best practices at application level
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const SecretsLoader = require('./load_secrets');

class SecureStartup {
  constructor() {
    this.secretsLoader = new SecretsLoader();
    this.auditLog = [];
  }

  /**
   * Validate environment before starting
   */
  async validateEnvironment() {
    const checks = [];

    // Check Node.js version
    const nodeVersion = process.version;
    const majorVersion = parseInt(nodeVersion.slice(1).split('.')[0]);
    checks.push({
      name: 'Node.js version',
      passed: majorVersion >= 18,
      message: majorVersion >= 18 ? `v${majorVersion} OK` : `v${majorVersion} too old, need v18+`
    });

    // Check file permissions
    const configPath = path.join(process.env.HOME, '.openclaw/openclaw.json');
    if (fs.existsSync(configPath)) {
      const stats = fs.statSync(configPath);
      const mode = (stats.mode & 0o777).toString(8);
      checks.push({
        name: 'Config file permissions',
        passed: mode === '600',
        message: mode === '600' ? 'Secure (600)' : `Insecure (${mode}), should be 600`
      });
    }

    // Check if running as root (bad)
    checks.push({
      name: 'Not running as root',
      passed: process.getuid() !== 0,
      message: process.getuid() !== 0 ? 'OK' : 'WARNING: Running as root is insecure'
    });

    return checks;
  }

  /**
   * Generate secure tokens
   */
  generateSecureToken(length = 32) {
    return crypto.randomBytes(length).toString('hex');
  }

  /**
   * Encrypt sensitive data before storing
   */
  encryptData(data, key) {
    const iv = crypto.randomBytes(16);
    const cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(key, 'hex'), iv);
    let encrypted = cipher.update(JSON.stringify(data), 'utf8', 'hex');
    encrypted += cipher.final('hex');
    const authTag = cipher.getAuthTag();
    
    return {
      iv: iv.toString('hex'),
      encrypted,
      authTag: authTag.toString('hex')
    };
  }

  /**
   * Decrypt sensitive data
   */
  decryptData(encryptedData, key) {
    const decipher = crypto.createDecipheriv(
      'aes-256-gcm',
      Buffer.from(key, 'hex'),
      Buffer.from(encryptedData.iv, 'hex')
    );
    decipher.setAuthTag(Buffer.from(encryptedData.authTag, 'hex'));
    
    let decrypted = decipher.update(encryptedData.encrypted, 'hex', 'utf8');
    decrypted += decipher.final('utf8');
    return JSON.parse(decrypted);
  }

  /**
   * Log security-relevant events
   */
  logAuditEvent(event, details) {
    const logEntry = {
      timestamp: new Date().toISOString(),
      event,
      details,
      pid: process.pid
    };

    this.auditLog.push(logEntry);

    // Also write to file
    const logPath = path.join(process.env.HOME, '.openclaw/security_audit.log');
    fs.appendFileSync(logPath, JSON.stringify(logEntry) + '\n');
  }

  /**
   * Rate limit API calls
   */
  createRateLimiter(maxRequests = 100, windowMs = 60000) {
    const requests = new Map();

    return (identifier) => {
      const now = Date.now();
      const windowStart = now - windowMs;

      // Clean old entries
      requests.forEach((timestamps, key) => {
        requests.set(key, timestamps.filter(t => t > windowStart));
        if (requests.get(key).length === 0) requests.delete(key);
      });

      // Check limit
      const userRequests = requests.get(identifier) || [];
      if (userRequests.length >= maxRequests) {
        this.logAuditEvent('RATE_LIMIT_EXCEEDED', { identifier });
        return false;
      }

      // Add request
      userRequests.push(now);
      requests.set(identifier, userRequests);
      return true;
    };
  }

  /**
   * Secure startup sequence
   */
  async start() {
    console.log('=== Secure OpenClaw Startup ===\n');

    // 1. Validate environment
    console.log('Validating environment...');
    const checks = await this.validateEnvironment();
    checks.forEach(check => {
      const status = check.passed ? '✓' : '✗';
      console.log(`  ${status} ${check.name}: ${check.message}`);
    });

    const failed = checks.filter(c => !c.passed);
    if (failed.length > 0) {
      console.error('\nSecurity checks failed. Please fix issues before continuing.');
      this.logAuditEvent('STARTUP_FAILED', { failedChecks: failed.map(f => f.name) });
      process.exit(1);
    }

    // 2. Load secrets from AWS Secrets Manager
    console.log('\nLoading secrets from AWS Secrets Manager...');
    try {
      await this.secretsLoader.loadAllSecrets();
      console.log('  ✓ Secrets loaded successfully');
    } catch (error) {
      // Fallback to environment variables
      console.log('  ⚠ AWS Secrets Manager unavailable, using environment variables');
    }

    // 3. Log successful startup
    this.logAuditEvent('STARTUP_SUCCESS', { 
      nodeVersion: process.version,
      platform: process.platform 
    });

    console.log('\n=== Startup complete ===');
    return true;
  }
}

module.exports = SecureStartup;

// Run if executed directly
if (require.main === module) {
  const startup = new SecureStartup();
  startup.start().catch(console.error);
}
JSEOF
```

### E. Security Monitoring Dashboard

```bash
cat > ~/workspace/scripts/security_monitor.sh << 'EOF'
#!/bin/bash
# Security Monitoring Script

echo "=== OpenClaw Security Monitor ==="
echo "Time: $(date)"
echo ""

# Check for failed login attempts
echo "=== Failed Login Attempts (last 24h) ==="
grep "Failed password" /var/log/auth.log | tail -10

# Check fail2ban status
echo ""
echo "=== Fail2ban Status ==="
sudo fail2ban-client status sshd 2>/dev/null || echo "fail2ban not running"

# Check active connections
echo ""
echo "=== Active Network Connections ==="
netstat -tuln | grep LISTEN

# Check OpenClaw service
echo ""
echo "=== OpenClaw Service Status ==="
systemctl status openclaw --no-pager | head -15

# Check disk usage
echo ""
echo "=== Disk Usage ==="
df -h / | tail -1

# Check memory usage
echo ""
echo "=== Memory Usage ==="
free -h | grep Mem

# Check for security updates
echo ""
echo "=== Available Security Updates ==="
apt list --upgradable 2>/dev/null | grep -i security | head -5 || echo "None available"

echo ""
echo "=== End of Security Report ==="
EOF

chmod +x ~/workspace/scripts/security_monitor.sh

# Add to daily cron
(crontab -l 2>/dev/null; echo "0 8 * * * ~/workspace/scripts/security_monitor.sh >> ~/workspace/data/security_reports/daily_$(date +\%Y\%m\%d).log 2>&1") | crontab -

# Create reports directory
mkdir -p ~/workspace/data/security_reports
```

### F. Update Systemd Service with Security Features

```bash
# Update systemd service with security hardening
sudo tee /etc/systemd/system/openclaw.service << 'EOF'
[Unit]
Description=OpenClaw AI Assistant Gateway (Secured)
After=network.target
Documentation=https://docs.openclaw.ai

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/ubuntu/.openclaw /home/ubuntu/workspace
CapabilityBoundingSet=
AmbientCapabilities=
SeccompFilter=@system-service

# Environment (secrets loaded at runtime from AWS)
Environment="NODE_ENV=production"
Environment="AWS_REGION=us-west-2"

# Startup command with secure startup
ExecStartPre=/usr/bin/node /home/ubuntu/workspace/scripts/secure_startup.js
ExecStart=/usr/bin/openclaw gateway --port 18789
Restart=always
RestartSec=10

# Resource limits
MemoryMax=1G
CPUQuota=80%

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=openclaw

[Install]
WantedBy=multi-user.target
EOF

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl restart openclaw
```

---

## Monitoring and Maintenance

### View Logs

```bash
# Service logs
sudo journalctl -u openclaw -f

# OpenClaw logs
tail -f ~/.openclaw/gateway.log

# Or check system logs
tail -f /tmp/openclaw/openclaw-*.log
```

### Restart Service

```bash
sudo systemctl restart openclaw
```

### Update OpenClaw

```bash
npm install -g openclaw@latest
sudo systemctl restart openclaw
```

### Check Health

```bash
# Run doctor command
GEMINI_API_KEY="YOUR_KEY" openclaw doctor

# Check capabilities
GEMINI_API_KEY="YOUR_KEY" openclaw capabilities
```

---

## Troubleshooting

### Service Won't Start

```bash
# Check logs
sudo journalctl -u openclaw -n 50

# Common issues:
# 1. Missing API keys - check environment variables
# 2. Port already in use - check if another instance is running
# 3. Invalid config - run `openclaw doctor --fix`
```

### Discord Bot Not Responding

```bash
# Check if bot is online in Discord
# Verify token is correct
echo $DISCORD_BOT_TOKEN

# Check channel status
openclaw channels status

# Restart service
sudo systemctl restart openclaw
```

### Gateway Port Already in Use

```bash
# Find process using port 18789
sudo lsof -i :18789

# Kill process if needed
sudo kill -9 PID

# Or change port in openclaw.json
```

---

## Cost Estimates

| Component | Cost |
|-----------|------|
| **EC2 t3.small** (2 vCPU, 2 GB RAM) | ~$15/month |
| **EBS Storage** (20 GB) | ~$2/month |
| **Data Transfer** (minimal) | ~$1/month |
| **Total** | **~$18/month** |

**Note**: t3.small is eligible for AWS Free Tier (750 hours/month) for first 12 months.

---

## Security Best Practices

### Credential Security

1. **SSH Key Security**: Never commit `.pem` files to git
2. **API Keys**: Use AWS Secrets Manager for production; never hardcode in source
3. **Token Rotation**: Rotate all API tokens every 90 days minimum
4. **Principle of Least Privilege**: Grant minimum required permissions

### Network Security

5. **Security Groups**: Only allow necessary ports from trusted IPs
6. **VPC Isolation**: Deploy in private subnet with NAT gateway for outbound
7. **TLS Everywhere**: Ensure all API communications use HTTPS/WSS
8. **Disable Unused Ports**: Close ports 18789 if not using direct access

### System Security

9. **Regular Updates**: Enable automatic security updates (`unattended-upgrades`)
10. **Fail2ban**: Block brute-force SSH attempts automatically
11. **Audit Logging**: Enable `auditd` to track security-relevant events
12. **File Permissions**: Ensure config files have `600` permissions

### Application Security

13. **Rate Limiting**: Implement rate limits on all API endpoints
14. **Input Validation**: Sanitize all user inputs before processing
15. **Error Handling**: Never expose stack traces or internal errors to users
16. **Dependency Scanning**: Regularly audit npm packages for vulnerabilities

### Operational Security

17. **Backup Config**: Backup `~/.openclaw/openclaw.json` to S3 regularly
18. **Log Retention**: Keep security logs for at least 90 days
19. **Incident Response**: Have a plan for credential exposure incidents
20. **Access Reviews**: Quarterly review of who has SSH access

### Security Checklist

```bash
# Run this checklist before going to production
echo "=== Security Checklist ==="

# 1. Check SSH config
grep -q "PermitRootLogin no" /etc/ssh/sshd_config && echo "✓ Root login disabled" || echo "✗ Root login enabled"
grep -q "PasswordAuthentication no" /etc/ssh/sshd_config && echo "✓ Password auth disabled" || echo "✗ Password auth enabled"

# 2. Check firewall
sudo ufw status | grep -q "Status: active" && echo "✓ Firewall active" || echo "✗ Firewall inactive"

# 3. Check fail2ban
systemctl is-active fail2ban > /dev/null && echo "✓ Fail2ban running" || echo "✗ Fail2ban not running"

# 4. Check config permissions
stat -c %a ~/.openclaw/openclaw.json 2>/dev/null | grep -q "600" && echo "✓ Config permissions secure" || echo "✗ Config permissions insecure"

# 5. Check for hardcoded secrets
grep -r "API_KEY\|TOKEN\|SECRET" ~/.openclaw/*.json 2>/dev/null | grep -v "YOUR_" && echo "✗ Potential hardcoded secrets found" || echo "✓ No hardcoded secrets"

# 6. Check automatic updates
dpkg -l | grep -q unattended-upgrades && echo "✓ Auto-updates enabled" || echo "✗ Auto-updates not enabled"
```

---

## Quick Reference Commands

```bash
# Service management
sudo systemctl start openclaw
sudo systemctl stop openclaw
sudo systemctl restart openclaw
sudo systemctl status openclaw

# View logs
sudo journalctl -u openclaw -f

# Channel management
openclaw channels list
openclaw channels status
openclaw channels add --channel discord --use-env
openclaw channels add --channel whatsapp

# Configuration
openclaw configure --section web
openclaw doctor
openclaw doctor --fix

# SSH access
ssh -i ~/.ssh/openclaw-key.pem ubuntu@YOUR_INSTANCE_IP

# Security commands
~/workspace/scripts/security_monitor.sh     # Run security check
sudo fail2ban-client status sshd            # Check banned IPs
sudo ufw status                             # Check firewall status
sudo journalctl -u auditd -f                # View audit logs

# Stock analysis commands
node ~/workspace/scripts/stock_message_analyzer.js  # Run stock analysis
cat ~/workspace/data/stock_analysis_*_summary.txt   # View latest summary

# WhatsApp commands
node ~/workspace/scripts/whatsapp_web_service.js    # Start WhatsApp Web (QR scan)
node ~/workspace/scripts/whatsapp_business_service.js  # Start Business API

# Discord commands
node ~/workspace/scripts/discord_reader_service.js  # Start Discord reader

# AWS Secrets Manager
aws secretsmanager get-secret-value --secret-id openclaw/production/api-keys
aws secretsmanager update-secret --secret-id openclaw/production/api-keys --secret-string 'NEW_VALUE'
```

---

## Next Steps

1. **Customize Agent Prompt**: Set up initial prompt for your use case
2. **Add Skills**: Install additional OpenClaw skills as needed
3. **Monitor Performance**: Track API usage and costs
4. **Scale Up**: Upgrade to larger instance if needed
5. **Backup**: Set up automated backups of configuration
6. **Configure Discord Servers**: Add server credentials to `~/.openclaw/discord_servers.json`
7. **Set Up WhatsApp**: Choose between Business API or whatsapp-web.js
8. **Tune Stock Analyzer**: Customize keywords and tickers in configuration
9. **Enable Scheduled Analysis**: Configure cron jobs for regular stock signal analysis
10. **Security Audit**: Run security checklist and fix any issues
11. **Set Up Alerts**: Configure notifications for security events and trading signals

---

## Support

- **OpenClaw Docs**: https://docs.openclaw.ai
- **OpenClaw GitHub**: https://github.com/openclaw/openclaw
- **Discord Community**: (if available)

---

## Summary

✅ **Deployed**: OpenClaw Gateway on AWS EC2  
✅ **Configured**: Gemini 3 Pro Preview model  
✅ **Connected**: Discord bot integration  
✅ **WhatsApp**: Both Business API and whatsapp-web.js integration  
✅ **Discord Reader**: Multi-server message reading with credentials  
✅ **Stock Analyzer**: Automated trading signal summarization  
✅ **Security Hardened**: AWS Secrets Manager, fail2ban, UFW, audit logging  
✅ **Automated**: Systemd service for 24/7 operation  
✅ **Tools**: Development workspace with research and analysis scripts  

Your OpenClaw AI assistant is now running 24/7 on AWS with comprehensive messaging integration and security!
