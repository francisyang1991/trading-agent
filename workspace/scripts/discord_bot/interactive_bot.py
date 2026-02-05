#!/usr/bin/env python3
"""
Interactive Discord Bot
- Listens for @mentions in channels
- When tagged with a stock ticker, analyzes last 60 days of signals
- Provides detailed fundamental analysis + entry/exit suggestions
"""

import os
import sys
import re
import json
import discord
from discord.ext import commands
from datetime import datetime, timedelta, timezone

# Add paths
sys.path.append(os.path.join(os.path.dirname(__file__), '../core_analysis'))
from llm_analyzer import _call_minimax_anthropic, get_stock_context

# Configuration
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DATA_FILE = os.path.join(os.path.dirname(__file__), '../../data/real_discord_messages_goku_wilson_60d.txt')

GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461"]
WILSON_CHANNELS = ["1211549165629476924"]

# Bot setup with intents
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

def extract_ticker(text):
    """Extract stock ticker from message"""
    # Match $TICKER or just TICKER (uppercase, 1-5 chars)
    patterns = [
        r'\$([A-Z]{1,5})\b',  # $AAPL format
        r'\b([A-Z]{1,5})\b'    # Plain AAPL format
    ]
    
    # Common words to exclude
    exclude = {'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 
               'HAS', 'HIS', 'HOW', 'ITS', 'MAY', 'NEW', 'NOW', 'OLD', 'SEE',
               'WAY', 'WHO', 'BOT', 'GET', 'LET', 'PUT', 'SAY', 'USE', 'YES',
               'BUY', 'SELL', 'HOLD', 'LONG', 'SHORT', 'WHAT', 'WHEN', 'THIS',
               'THAT', 'WITH', 'FROM', 'HAVE', 'WILL', 'YOUR', 'ABOUT', 'THINK'}
    
    for pattern in patterns:
        matches = re.findall(pattern, text.upper())
        for match in matches:
            if match not in exclude and len(match) >= 2:
                return match
    return None

def get_ticker_messages(ticker, days=60):
    """Get all messages mentioning a ticker from cached data"""
    if not os.path.exists(DATA_FILE):
        return [], "Data file not found. Run scraper first."
    
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ticker_upper = ticker.upper()
    ticker_patterns = [f"${ticker_upper}", ticker_upper]
    
    messages = []
    
    for channel_id, msgs in data.items():
        source = "Goku" if channel_id in GOKU_CHANNELS else "Wilson"
        for msg in msgs:
            content = msg.get('content', '')
            ts = msg.get('timestamp', '')
            
            # Check if ticker is mentioned
            if any(p in content.upper() for p in ticker_patterns):
                try:
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    if dt > cutoff:
                        messages.append({
                            "source": source,
                            "date": ts[:10],
                            "content": content,
                            "author": msg.get('author', {}).get('username', 'Unknown')
                        })
                except:
                    continue
    
    # Sort by date (newest first)
    messages.sort(key=lambda x: x['date'], reverse=True)
    return messages, None

def generate_stock_analysis(ticker, messages):
    """Generate detailed analysis using LLM"""
    if not messages:
        return f"No signals found for ${ticker} in the last 60 days."
    
    # Get current price data
    stock_ctx = get_stock_context(ticker)
    
    price_info = ""
    if stock_ctx and 'error' not in stock_ctx:
        price_info = f"""
CURRENT MARKET DATA:
- Price: ${stock_ctx.get('current_price', 'N/A')}
- EMA8: ${stock_ctx.get('ema8', 'N/A')} ({'above' if stock_ctx.get('above_ema8') else 'below'})
- EMA21: ${stock_ctx.get('ema21', 'N/A')} ({'above' if stock_ctx.get('above_ema21') else 'below'})
- EMA50: ${stock_ctx.get('ema50', 'N/A')}
- 52W Range: ${stock_ctx.get('52w_low', 'N/A')} - ${stock_ctx.get('52w_high', 'N/A')}
"""
    
    # Format messages
    goku_msgs = [m for m in messages if m['source'] == 'Goku']
    wilson_msgs = [m for m in messages if m['source'] == 'Wilson']
    
    goku_text = "\n".join([f"[{m['date']}] {m['author']}: {m['content']}" for m in goku_msgs[:20]])
    wilson_text = "\n".join([f"[{m['date']}] {m['author']}: {m['content']}" for m in wilson_msgs[:20]])
    
    prompt = f"""You are an elite trading analyst. Analyze ${ticker} based on signals from two top traders.

{price_info}

GOKU (Technical Analysis) - {len(goku_msgs)} mentions:
{goku_text if goku_text else "No technical signals from Goku"}

WILSON (Fundamental Analysis) - {len(wilson_msgs)} mentions:
{wilson_text if wilson_text else "No fundamental signals from Wilson"}

Provide a comprehensive Discord-formatted analysis:

*${ticker} DEEP DIVE*

📊 *FUNDAMENTAL ANALYSIS*
- Business quality / Valuation
- Key catalysts or risks mentioned
- Wilson's thesis summary

📈 *TECHNICAL SETUP*  
- Chart pattern / Trend
- Key support/resistance levels
- Goku's setup summary

🎯 *ENTRY STRATEGY*
- Ideal entry zone: $X - $Y
- Entry trigger: [what to wait for]
- Position size suggestion: [conservative/moderate/aggressive]

🛑 *EXIT STRATEGY*
- Stop loss: $X (reason)
- Target 1: $Y (+X%)
- Target 2: $Z (+Y%)
- Time horizon: [days/weeks]

⚖️ *RISK/REWARD*
- R:R ratio
- Confidence: [HIGH/MEDIUM/LOW]
- Key risk: [main concern]

Keep it under 1800 characters. Use single asterisks for bold (*text*).
"""

    response = _call_minimax_anthropic(prompt, 2000, stock_ctx, ticker, messages)
    
    content = ""
    if "raw_response" in response:
        content = response["raw_response"]
    elif "raw_llm" in response:
        content = response["raw_llm"]
    
    # Clean up
    content = content.replace("```", "").replace("**", "*")
    
    if len(content) > 1900:
        content = content[:1900] + "..."
    
    return content

@bot.event
async def on_ready():
    print(f"Bot is online: {bot.user}")
    print(f"Servers: {len(bot.guilds)}")

@bot.event
async def on_message(message):
    # Ignore own messages
    if message.author == bot.user:
        return
    
    # Check if bot is mentioned
    if bot.user.mentioned_in(message):
        # Extract ticker from message
        # Remove the mention from the text first
        clean_text = re.sub(r'<@!?\d+>', '', message.content).strip()
        ticker = extract_ticker(clean_text)
        
        if ticker:
            await message.channel.send(f"🔍 Analyzing *${ticker}*... (checking 60 days of signals)")
            
            try:
                # Get messages about this ticker
                messages, error = get_ticker_messages(ticker)
                
                if error:
                    await message.channel.send(f"❌ Error: {error}")
                    return
                
                if not messages:
                    await message.channel.send(f"📭 No signals found for *${ticker}* in the last 60 days from Goku or Wilson.")
                    return
                
                # Generate analysis
                analysis = generate_stock_analysis(ticker, messages)
                await message.channel.send(analysis)
                
            except Exception as e:
                await message.channel.send(f"❌ Error generating analysis: {str(e)[:100]}")
        else:
            # No ticker found, show help
            await message.channel.send(
                "*Stock Analysis Bot*\n\n"
                "Tag me with a ticker to get detailed analysis:\n"
                "• `@bot $AAPL` - Analyze Apple\n"
                "• `@bot NVDA` - Analyze Nvidia\n\n"
                "I'll check 60 days of signals from Goku (Technical) and Wilson (Fundamental)."
            )
    
    await bot.process_commands(message)

@bot.command(name="analyze")
async def analyze_command(ctx, ticker: str = None):
    """!analyze TICKER - Get detailed stock analysis"""
    if not ticker:
        await ctx.send("Usage: `!analyze TICKER` (e.g., `!analyze AAPL`)")
        return
    
    ticker = ticker.upper().replace("$", "")
    await ctx.send(f"🔍 Analyzing *${ticker}*...")
    
    try:
        messages, error = get_ticker_messages(ticker)
        
        if error:
            await ctx.send(f"❌ Error: {error}")
            return
        
        if not messages:
            await ctx.send(f"📭 No signals for *${ticker}* in last 60 days.")
            return
        
        analysis = generate_stock_analysis(ticker, messages)
        await ctx.send(analysis)
        
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)[:100]}")

@bot.command(name="signals")
async def signals_command(ctx, ticker: str = None):
    """!signals TICKER - List recent signals for a stock"""
    if not ticker:
        await ctx.send("Usage: `!signals TICKER`")
        return
    
    ticker = ticker.upper().replace("$", "")
    messages, error = get_ticker_messages(ticker, days=30)
    
    if error:
        await ctx.send(f"❌ {error}")
        return
    
    if not messages:
        await ctx.send(f"📭 No signals for *${ticker}* in last 30 days.")
        return
    
    # Format recent signals
    output = f"*${ticker} Recent Signals* ({len(messages)} total)\n\n"
    for msg in messages[:10]:
        source_emoji = "📊" if msg['source'] == 'Goku' else "📈"
        output += f"{source_emoji} [{msg['date']}] {msg['content'][:100]}...\n\n"
    
    if len(output) > 1900:
        output = output[:1900] + "..."
    
    await ctx.send(output)

@bot.command(name="help_trading")
async def help_command(ctx):
    """Show help for trading commands"""
    help_text = """
*Trading Bot Commands*

📊 *Analysis*
• `@bot $TICKER` - Deep dive analysis
• `!analyze TICKER` - Same as above
• `!signals TICKER` - List recent signals

📈 *Sources*
• Goku = Technical Analysis (charts, patterns)
• Wilson = Fundamental Analysis (earnings, valuation)

🕐 *Data*
• Uses last 60 days of cached messages
• Updates daily via Discord scraper

💡 *Examples*
• `@bot NVDA` - Analyze Nvidia
• `!analyze AAPL` - Analyze Apple
• `!signals TSLA` - Recent Tesla signals
"""
    await ctx.send(help_text)

def main():
    if not DISCORD_BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set")
        print("Set it via: export DISCORD_BOT_TOKEN='your_token'")
        sys.exit(1)
    
    print("Starting Interactive Trading Bot...")
    print(f"Data file: {DATA_FILE}")
    print(f"Data exists: {os.path.exists(DATA_FILE)}")
    
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.errors.PrivilegedIntentsRequired:
        print("\n" + "="*60)
        print("ERROR: Privileged Intents Required!")
        print("="*60)
        print("\nYou need to enable Message Content Intent:")
        print("1. Go to https://discord.com/developers/applications")
        print("2. Select your bot application")
        print("3. Go to 'Bot' section")
        print("4. Scroll to 'Privileged Gateway Intents'")
        print("5. Enable 'MESSAGE CONTENT INTENT'")
        print("6. Save changes and restart the bot")
        print("="*60)
        sys.exit(1)

if __name__ == "__main__":
    main()
