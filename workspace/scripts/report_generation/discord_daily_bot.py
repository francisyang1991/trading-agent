#!/usr/bin/env python3
"""
Daily Trading Analysis Bot
Runs daily at 11:50 AM PST (before market close)
Sends analysis to Discord "Rich or Die" channel
"""

import json
import os
import sys
import requests
from datetime import datetime, timedelta, timezone

# Add paths
sys.path.append(os.path.join(os.path.dirname(__file__), '../core_analysis'))
from llm_analyzer import _call_minimax_anthropic

# Configuration
DISCORD_CHANNEL_ID = "1345123472019423284"  # Rich or Die channel
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DATA_FILE = os.path.join(os.path.dirname(__file__), '../../data/real_discord_messages_goku_wilson_60d.txt')

GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461"]
WILSON_CHANNELS = ["1211549165629476924"]

def get_messages_last_n_days(days=7):
    """Get messages from the last N days"""
    if not os.path.exists(DATA_FILE):
        print("Data file not found")
        return [], []
        
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)
        
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    
    messages = []
    today_messages = []
    
    goku_count = 0
    wilson_count = 0
    goku_today = 0
    wilson_today = 0
    
    for channel_id, msgs in data.items():
        source = "Goku" if channel_id in GOKU_CHANNELS else "Wilson"
        for msg in msgs:
            ts = msg.get('timestamp')
            content = msg.get('content', '').strip()
            
            # Skip empty messages
            if not content:
                continue
                
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if dt > cutoff:
                    entry = {
                        "source": source,
                        "date": ts,
                        "content": content,
                        "author": msg.get('author', {}).get('username', 'Unknown')
                    }
                    messages.append(entry)
                    
                    if source == "Goku": goku_count += 1
                    else: wilson_count += 1
                    
                    if dt > today_start:
                        today_messages.append(entry)
                        if source == "Goku": goku_today += 1
                        else: wilson_today += 1
            except:
                continue
                
    print(f"Last {days} days: {goku_count} Goku, {wilson_count} Wilson messages")
    print(f"Today: {goku_today} Goku, {wilson_today} Wilson messages ({len(today_messages)} total)")
    return messages, today_messages

def generate_analysis():
    """Generate trading analysis for Discord"""
    messages, today_messages = get_messages_last_n_days(days=7)
    
    if not messages:
        return "No recent trading signals found."
    
    # Format ALL today's signals (no truncation)
    today_msgs_text = "\n".join([
        f"[{m['source']}] {m['author']}: {m['content']}" 
        for m in today_messages
    ]) if today_messages else "No signals yet today."
    
    # For historical: use last 300 messages for context
    historical_msgs_text = "\n".join([
        f"[{m['source']}] {m['date'][:10]}: {m['content'][:200]}" 
        for m in messages[-300:]
    ])
    
    today_date = datetime.now().strftime('%Y-%m-%d')
    
    prompt = f"""You are an elite trading analyst. Create a Discord briefing from two traders (Goku=Technical, Wilson=Fundamental).

TODAY'S DATE: {today_date}

=== TODAY'S SIGNALS (MUST INCLUDE ALL) ===
{today_msgs_text}

=== LAST 7 DAYS (SUMMARY CONTEXT) ===
{historical_msgs_text}

TASK:
Create a Discord report. IMPORTANT FORMATTING RULES:
- Do NOT use triple backticks (```)
- Use single asterisks for bold: *BOLD* not **BOLD**
- Keep under 1900 characters total

STRUCTURE:
🎯 *PRE-CLOSE BRIEFING* | {today_date}

🔥 *TODAY'S SIGNALS* (List ALL today's signals - do not skip any)
• $TICKER [LONG/SHORT] - reason (Goku/Wilson)
[Include every signal from today]

📊 *7-DAY SUMMARY*
*LONG BIAS:* $TICK1, $TICK2...
*SHORT BIAS:* $TICK3, $TICK4...

⚡ *ACTIONABLE NOW*
• Best setup with entry/stop/target

⚠️ *AVOID*
• Tickers to skip and why

*MACRO:* One-line market context
"""

    print("Generating analysis...")
    response = _call_minimax_anthropic(prompt, 2500, {}, "", [])
    
    content = ""
    if "raw_response" in response:
        content = response["raw_response"]
    elif "raw_llm" in response:
        content = response["raw_llm"]
    
    # Clean up any triple backticks that might have been generated
    content = content.replace("```", "")
    
    # Trim to Discord limit
    if len(content) > 1950:
        content = content[:1950] + "..."
        
    return content

def send_to_discord(message):
    """Send message to Discord channel"""
    if not DISCORD_BOT_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not set")
        return False
        
    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "content": message
    }
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code == 200:
            print(f"Message sent to Discord channel {DISCORD_CHANNEL_ID}")
            return True
        else:
            print(f"Discord API error: {r.status_code} - {r.text}")
            return False
    except Exception as e:
        print(f"Error sending to Discord: {e}")
        return False

def main():
    print(f"=== Daily Trading Analysis Bot ===")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Target: Rich or Die (#{DISCORD_CHANNEL_ID})")
    print("=" * 40)
    
    # Generate analysis
    analysis = generate_analysis()
    
    if not analysis:
        print("Failed to generate analysis")
        return
    
    print("\n--- Generated Report ---")
    print(analysis)
    print("--- End Report ---\n")
    
    # Send to Discord
    if DISCORD_BOT_TOKEN:
        success = send_to_discord(analysis)
        if success:
            print("✓ Report sent to Discord")
        else:
            print("✗ Failed to send to Discord")
    else:
        print("⚠ DISCORD_BOT_TOKEN not set - skipping Discord send")
        print("  Set environment variable to enable Discord posting")
    
    # Also save locally
    output_file = os.path.join(os.path.dirname(__file__), '../../data/reports/daily_discord_report.txt')
    with open(output_file, 'w') as f:
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Channel: {DISCORD_CHANNEL_ID}\n\n")
        f.write(analysis)
    print(f"Local copy saved to: {output_file}")

if __name__ == "__main__":
    main()
