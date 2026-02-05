import json
import os
import sys
from datetime import datetime, timedelta, timezone
import yfinance as yf

# Add core_analysis to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../core_analysis'))
from llm_analyzer import _call_minimax_anthropic, _parse_llm_response

DATA_FILE = os.path.join(os.path.dirname(__file__), '../../data/real_discord_messages_goku_wilson_60d.txt')
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), '../../data/reports/daily_recommendations.md')

GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461"]
WILSON_CHANNELS = ["1211549165629476924"]

def get_recent_messages(hours=48):
    if not os.path.exists(DATA_FILE):
        print("Data file not found")
        return []
        
    with open(DATA_FILE, 'r') as f:
        data = json.load(f)
        
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = []
    
    goku_count = 0
    wilson_count = 0
    
    for channel_id, msgs in data.items():
        source = "Goku" if channel_id in GOKU_CHANNELS else "Wilson"
        for msg in msgs:
            ts = msg.get('timestamp')
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                if dt > cutoff:
                    recent.append({
                        "source": source,
                        "date": ts,
                        "content": msg.get('content', ''),
                        "author": msg.get('author', {}).get('username', 'Unknown')
                    })
                    if source == "Goku": goku_count += 1
                    else: wilson_count += 1
            except:
                continue
                
    print(f"DEBUG: Found {goku_count} Goku messages and {wilson_count} Wilson messages in last {hours}h")
    return recent

def get_market_context():
    # Get SPY and QQQ context
    context = ""
    try:
        for ticker in ['SPY', 'QQQ', 'IWM', 'NVDA']:
            hist = yf.Ticker(ticker).history(period="5d")
            if not hist.empty:
                last = hist.iloc[-1]
                prev = hist.iloc[-2]
                change = ((last['Close'] - prev['Close']) / prev['Close']) * 100
                context += f"{ticker}: ${last['Close']:.2f} ({change:+.2f}%)\n"
    except:
        pass
    return context

def generate_report():
    messages = get_recent_messages(hours=48) # 48h to catch weekend/overnight
    
    if not messages:
        print("No recent messages found.")
        return

    print(f"Analyzing {len(messages)} recent messages...")
    
    # Prepare prompt
    msgs_text = "\n".join([f"[{m['source']}] {m['date']}: {m['content']}" for m in messages])
    market_text = get_market_context()
    
    prompt = f"""You are an elite trading assistant. Analyze the recent messages from two top traders (Goku - Technical, Wilson - Fundamental) and generate a Daily Trading Plan for tomorrow.

MARKET CONTEXT:
{market_text}

RECENT TRADER MESSAGES (Last 48h):
{msgs_text}

TASK:
1. Identify the Top 10 Tickers mentioned across BOTH servers.
2. IMPORTANT: You must explicitly check for and include signals from BOTH 'Goku' and 'Wilson'. Do not bias towards one source.
3. For each ticker, determine the Action (BUY/SHORT/WATCH) and Conviction (HIGH/MEDIUM/LOW).
4. Sort the final list by **CONVICTION (Highest to Lowest)**.
5. If a ticker is mentioned by both, synthesize their views (Technical + Fundamental).

OUTPUT FORMAT (Markdown):
# Daily Trading Plan - [DATE]

## 🚨 Top Actionable Ideas (Sorted by Confidence)

1. **$TICKER** [ACTION] - [CONVICTION] (Source: [Goku/Wilson/Both])
   - **Thesis**: ...
   - **Technical (Goku)**: [Details or 'N/A']
   - **Fundamental (Wilson)**: [Details or 'N/A']
   - **Levels**: Support: $X, Resistance: $Y

2. ...

## 📋 Watchlist
- **$TICKER**: ...

## ⚠️ Market Context & Risks
...
"""

    # Call LLM
    print("Calling LLM...")
    response = _call_minimax_anthropic(prompt, 4000, {}, "", [])
    
    content = ""
    if "raw_response" in response:
        content = response["raw_response"]
    elif "raw_llm" in response:
        content = response["raw_llm"]
        
    if content:
        with open(OUTPUT_FILE, 'w') as f:
            f.write(content)
        print(f"Report saved to {OUTPUT_FILE}")
    else:
        print("Failed to generate report")

if __name__ == "__main__":
    generate_report()
