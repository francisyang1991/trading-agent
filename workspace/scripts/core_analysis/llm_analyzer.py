"""
LLM Analyzer Module - Enhanced Version
Uses MiniMax API for summarizing trading analysis with full context.

Features:
- Full message content (no truncation)
- Price data integration
- EMA/Technical indicator context
- Signal strength scoring
"""

import os
import json
import requests
import yfinance as yf
from typing import List, Dict, Optional
from datetime import datetime, timedelta

# MiniMax Configuration
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "sk-cp-G8bxUw5-mlh3IrH9KR3HoYv1Y7FErDgFPjT33eOkeJIxWQDTbuw08m0zkIIV4KWnP6Q9NbHycVDKa9bZN2G5wJcQ2-Lh1uy_d78J3mkfeZyBNjkC8gODvII")
MINIMAX_ENDPOINT = "https://api.minimax.io/v1/text/chatcompletion_v2"

# Anthropic-compatible endpoint
MINIMAX_ANTHROPIC_ENDPOINT = "https://api.minimax.io/anthropic/v1/messages"

def get_stock_context(ticker: str) -> Dict:
    """Get current price, EMAs, and recent performance for a ticker."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="3mo")
        
        if hist.empty:
            return {"error": "No data"}
        
        # Calculate EMAs
        hist['EMA8'] = hist['Close'].ewm(span=8, adjust=False).mean()
        hist['EMA21'] = hist['Close'].ewm(span=21, adjust=False).mean()
        hist['EMA50'] = hist['Close'].ewm(span=50, adjust=False).mean()
        
        latest = hist.iloc[-1]
        current_price = float(latest['Close'])
        
        # Get price at different periods
        prices = {}
        for days_ago in [5, 10, 30]:
            if len(hist) > days_ago:
                prices[f'{days_ago}d_ago'] = float(hist['Close'].iloc[-days_ago-1])
        
        return {
            "current_price": round(current_price, 2),
            "ema8": round(float(latest['EMA8']), 2),
            "ema21": round(float(latest['EMA21']), 2),
            "ema50": round(float(latest['EMA50']), 2),
            "above_ema8": current_price > float(latest['EMA8']),
            "above_ema21": current_price > float(latest['EMA21']),
            "historical_prices": prices,
            "52w_high": round(float(hist['Close'].max()), 2),
            "52w_low": round(float(hist['Close'].min()), 2),
            "avg_volume": int(hist['Volume'].mean())
        }
    except Exception as e:
        return {"error": str(e)}

def summarize_ticker_analysis(
    ticker: str,
    messages: List[Dict],
    model: str = "minimax",
    include_prices: bool = True,
    max_tokens: int = 1500
) -> Dict:
    """
    Summarize trader's analysis for a ticker using LLM.
    
    Args:
        ticker: Stock symbol
        messages: List of {date, content, author} dicts - FULL content, no truncation
        model: "minimax", "minimax_anthropic", or "local"
        include_prices: Whether to fetch and include price/EMA data
        max_tokens: Max response tokens
    """
    
    # Get stock context if requested
    stock_context = {}
    if include_prices:
        clean_ticker = ticker.replace('$', '')
        stock_context = get_stock_context(clean_ticker)
    
    # Build full analysis text - NO TRUNCATION
    analysis_text = f"=== TRADING ANALYSIS FOR ${ticker} ===\n\n"
    
    if stock_context and 'error' not in stock_context:
        analysis_text += f"CURRENT MARKET DATA:\n"
        analysis_text += f"- Current Price: ${stock_context.get('current_price', 'N/A')}\n"
        analysis_text += f"- EMA8: ${stock_context.get('ema8', 'N/A')} (Price {'above' if stock_context.get('above_ema8') else 'below'})\n"
        analysis_text += f"- EMA21: ${stock_context.get('ema21', 'N/A')} (Price {'above' if stock_context.get('above_ema21') else 'below'})\n"
        analysis_text += f"- EMA50: ${stock_context.get('ema50', 'N/A')}\n"
        analysis_text += f"- 52W Range: ${stock_context.get('52w_low', 'N/A')} - ${stock_context.get('52w_high', 'N/A')}\n"
        if stock_context.get('historical_prices'):
            for period, price in stock_context['historical_prices'].items():
                analysis_text += f"- Price {period}: ${price}\n"
        analysis_text += "\n"
    
    analysis_text += f"TRADER MESSAGES ({len(messages)} total):\n"
    analysis_text += "-" * 50 + "\n"
    
    # Include FULL message content
    for msg in messages:
        date = msg.get('date', 'Unknown')
        author = msg.get('author', 'Unknown')
        content = msg.get('content', '')  # Full content, no truncation
        
        analysis_text += f"\n[{date}] {author}:\n{content}\n"
        analysis_text += "-" * 30 + "\n"
    
    prompt = f"""You are an expert trading analyst. Analyze the following trading commentary and market data.

{analysis_text}

Provide a comprehensive analysis in JSON format:
{{
    "sentiment": "BULLISH/BEARISH/MIXED/NEUTRAL",
    "confidence": "HIGH/MEDIUM/LOW",
    "thesis": "2-3 sentence summary of the trader's view",
    "key_points": ["point1", "point2", "point3", "point4", "point5"],
    "price_levels": {{
        "support": [price levels mentioned],
        "resistance": [price levels mentioned],
        "targets": [price targets mentioned]
    }},
    "risk_factors": ["risk1", "risk2"],
    "trading_strategy": "Recommended approach based on analysis",
    "technical_setup": "Description of chart patterns/indicators mentioned",
    "catalyst": "Any upcoming events/earnings/news mentioned"
}}

Be thorough and reference specific quotes from the messages."""

    if model == "minimax":
        return _call_minimax_v1(prompt, max_tokens, stock_context)
    elif model == "minimax_anthropic":
        return _call_minimax_anthropic(prompt, max_tokens, stock_context, ticker, messages)
    else:
        return _local_analysis(ticker, messages, stock_context)

def _call_minimax_v1(prompt: str, max_tokens: int, stock_context: Dict) -> Dict:
    """Call MiniMax v1 API."""
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": "abab6.5s-chat",
        "messages": [
            {"sender_type": "USER", "sender_name": "user", "text": prompt}
        ],
        "tokens_to_generate": max_tokens,
        "temperature": 0.7,
        "top_p": 0.9
    }
    
    try:
        response = requests.post(MINIMAX_ENDPOINT, headers=headers, json=data, timeout=60)
        
        if response.status_code == 200:
            result = response.json()
            content = result.get("reply", "")
            parsed = _parse_llm_response(content)
            parsed["stock_context"] = stock_context
            parsed["model"] = "minimax_v1"
            return parsed
        else:
            print(f"MiniMax API error: {response.status_code} - {response.text}")
            return {"error": f"API error: {response.status_code}", "fallback": _local_analysis_simple(prompt, stock_context)}
    except Exception as e:
        print(f"MiniMax exception: {e}")
        return {"error": str(e), "fallback": _local_analysis_simple(prompt, stock_context)}

def _call_minimax_anthropic(prompt: str, max_tokens: int, stock_context: Dict, ticker: str, messages: List[Dict]) -> Dict:
    """Call MiniMax via Anthropic-compatible API using Anthropic SDK."""
    try:
        import anthropic
        
        client = anthropic.Anthropic(
            api_key=MINIMAX_API_KEY,
            base_url="https://api.minimax.io/anthropic"
        )
        
        message = client.messages.create(
            model="MiniMax-M2.1",
            max_tokens=max_tokens,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        )
        
        content = ""
        for block in message.content:
            if block.type == "text":
                content += block.text
        
        if not content:
            return _local_analysis(ticker, messages, stock_context)

        parsed = _parse_llm_response(content)
        
        # If parsing failed, use local analysis but include raw LLM output
        if "raw_response" in parsed:
             local = _local_analysis(ticker, messages, stock_context)
             local["raw_llm"] = parsed["raw_response"]
             return local

        parsed["stock_context"] = stock_context
        parsed["model"] = "minimax_anthropic_sdk"
        return parsed
        
    except ImportError:
        return _local_analysis(ticker, messages, stock_context)
    except Exception as e:
        print(f"Exception: {e}")
        local = _local_analysis(ticker, messages, stock_context)
        local["error"] = str(e)
        return local

def _parse_llm_response(content: str) -> Dict:
    """Parse JSON from LLM response."""
    try:
        # Try to extract JSON
        start = content.find('{')
        end = content.rfind('}') + 1
        if start >= 0 and end > start:
            return json.loads(content[start:end])
    except:
        pass
    return {"raw_response": content}

def _local_analysis(ticker: str, messages: List[Dict], stock_context: Dict) -> Dict:
    """Enhanced local analysis without LLM."""
    bullish_words = ['long', 'buy', 'calls', 'bullish', 'breakout', 'support', 'bounce', 'undervalued', 'accumulate']
    bearish_words = ['short', 'sell', 'puts', 'bearish', 'breakdown', 'overvalued', 'crash', 'risk', 'exit', 'closing']
    
    all_text = ' '.join([m.get('content', '').lower() for m in messages])
    
    bull_count = sum(1 for w in bullish_words if w in all_text)
    bear_count = sum(1 for w in bearish_words if w in all_text)
    
    if bull_count > bear_count + 3:
        sentiment = "BULLISH"
        confidence = "HIGH" if bull_count > bear_count + 5 else "MEDIUM"
    elif bear_count > bull_count + 3:
        sentiment = "BEARISH"
        confidence = "HIGH" if bear_count > bull_count + 5 else "MEDIUM"
    else:
        sentiment = "MIXED"
        confidence = "LOW"
    
    # Extract price targets
    import re
    prices = re.findall(r'\$(\d+(?:\.\d{2})?)', all_text)
    targets = [float(p) for p in prices if float(p) > 10]  # Filter noise
    
    # Extract key points from messages
    key_points = []
    for msg in messages[:10]:
        content = msg.get('content', '')
        if len(content) > 50:
            key_points.append(content[:150] + "...")
    
    return {
        "sentiment": sentiment,
        "confidence": confidence,
        "thesis": f"Trader has {len(messages)} mentions of ${ticker} with {sentiment.lower()} bias based on {bull_count} bullish vs {bear_count} bearish signals.",
        "key_points": key_points[:5],
        "price_levels": {
            "targets": sorted(set(targets))[:5] if targets else []
        },
        "risk_factors": ["Market volatility", "Position sizing"],
        "stock_context": stock_context,
        "model": "local_analysis"
    }

def _local_analysis_simple(prompt: str, stock_context: Dict) -> Dict:
    """Simple fallback."""
    return {
        "sentiment": "UNKNOWN",
        "thesis": "LLM analysis failed - using fallback",
        "stock_context": stock_context,
        "model": "fallback"
    }

# ============ BATCH ANALYSIS ============
def analyze_all_tickers(data_file: str, server: str = "wilson", output_file: str = None) -> List[Dict]:
    """
    Analyze all tickers from a server using cached message data.
    
    Args:
        data_file: Path to cached Discord messages JSON
        server: "wilson" or "goku"
        output_file: Optional path to save results
    """
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    
    # Load cached data
    with open(data_file, 'r') as f:
        raw_data = json.load(f)
    
    # Channel mapping
    WILSON_CHANNELS = ["1211549165629476924"]
    GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461"]
    
    channels = WILSON_CHANNELS if server == "wilson" else GOKU_CHANNELS
    
    # Collect messages
    messages = []
    for channel_id, msgs in raw_data.items():
        if channel_id in channels:
            messages.extend(msgs)
    
    print(f"Loaded {len(messages)} messages from {server}")
    
    # Group by ticker
    from stock_message_analyzer import StockMessageAnalyzer
    analyzer = StockMessageAnalyzer(None)
    
    by_ticker = {}
    for msg in messages:
        content = msg.get('content', '')
        result = analyzer.analyzeMessage({
            'content': content,
            'author': msg.get('author', {}).get('username', 'Unknown'),
            'timestamp': msg.get('timestamp', ''),
            'id': msg.get('id', '')
        })
        
        if result and result.get('tickers'):
            for ticker in result['tickers']:
                if ticker not in by_ticker:
                    by_ticker[ticker] = []
                by_ticker[ticker].append({
                    'date': msg.get('timestamp', '')[:10],
                    'author': msg.get('author', {}).get('username', 'Unknown'),
                    'content': content  # Full content
                })
    
    # Analyze top tickers
    sorted_tickers = sorted(by_ticker.items(), key=lambda x: len(x[1]), reverse=True)[:15]
    
    results = []
    for ticker, msgs in sorted_tickers:
        print(f"Analyzing {ticker} ({len(msgs)} messages)...")
        analysis = summarize_ticker_analysis(ticker, msgs, model="minimax")
        analysis['ticker'] = ticker
        analysis['message_count'] = len(msgs)
        results.append(analysis)
    
    # Save results
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"Saved analysis to {output_file}")
    
    return results

# CLI
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python llm_analyzer.py <ticker> [model]")
        print("  python llm_analyzer.py --batch <data_file> <server> [output_file]")
        sys.exit(1)
    
    if sys.argv[1] == "--batch":
        data_file = sys.argv[2]
        server = sys.argv[3] if len(sys.argv) > 3 else "wilson"
        output = sys.argv[4] if len(sys.argv) > 4 else None
        analyze_all_tickers(data_file, server, output)
    else:
        ticker = sys.argv[1]
        model = sys.argv[2] if len(sys.argv) > 2 else "local"
        
        demo_messages = [
            {"date": "2025-12-10", "content": f"Taking short position in ${ticker}...", "author": "trader"},
        ]
        
        result = summarize_ticker_analysis(ticker, demo_messages, model)
        print(json.dumps(result, indent=2))
