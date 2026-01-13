#!/usr/bin/env python3
"""
Stock Technical Analyzer
========================
Analyzes any stock symbol with:
- Technical indicators (VPES, RSI, ATR)
- EMA position analysis (9, 21, 50, 120, 200)
- Recent news and earnings from the last 3 months

Usage:
    python tools/stock_analyzer.py AAPL
    python tools/stock_analyzer.py AAPL MSFT NVDA
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import warnings
warnings.filterwarnings('ignore')


class TrendPosition(Enum):
    STRONG_BULLISH = "🟢🟢 STRONG BULLISH"
    BULLISH = "🟢 BULLISH"
    NEUTRAL = "⚪ NEUTRAL"
    BEARISH = "🔴 BEARISH"
    STRONG_BEARISH = "🔴🔴 STRONG BEARISH"


@dataclass
class EMAAnalysis:
    """EMA analysis result"""
    ema_period: int
    ema_value: float
    price: float
    position: str  # "ABOVE" or "BELOW"
    distance_pct: float


@dataclass
class TechnicalSummary:
    """Complete technical analysis summary"""
    symbol: str
    price: float
    change_1d: float
    change_1w: float
    change_1m: float
    ema_analysis: List[EMAAnalysis]
    trend_position: TrendPosition
    rsi: float
    atr: float
    atr_pct: float
    volume_ratio: float
    vpes: float
    support_level: float
    resistance_level: float


def calculate_ema(data: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return data.ewm(span=period, adjust=False).mean()


def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index."""
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def calculate_vpes(data: pd.DataFrame, volume_period: int = 20) -> pd.Series:
    """
    Calculate Volume-Price Expansion Score.
    VPES = (Close - Open) / Open * (Volume / MA(Volume, N))
    """
    price_change = (data['Close'] - data['Open']) / data['Open']
    volume_ma = data['Volume'].rolling(window=volume_period).mean()
    volume_ratio = data['Volume'] / volume_ma
    return price_change * volume_ratio


def find_support_resistance(data: pd.DataFrame, lookback: int = 20) -> Tuple[float, float]:
    """Find recent support and resistance levels."""
    recent_data = data.tail(lookback)
    support = recent_data['Low'].min()
    resistance = recent_data['High'].max()
    return support, resistance


def analyze_ema_position(price: float, emas: Dict[int, float]) -> Tuple[List[EMAAnalysis], TrendPosition]:
    """
    Analyze price position relative to EMAs.
    Returns EMA analysis list and overall trend position.
    """
    ema_analysis = []
    above_count = 0
    
    for period, ema_value in sorted(emas.items()):
        position = "ABOVE" if price > ema_value else "BELOW"
        distance_pct = ((price - ema_value) / ema_value) * 100
        
        if price > ema_value:
            above_count += 1
        
        ema_analysis.append(EMAAnalysis(
            ema_period=period,
            ema_value=ema_value,
            price=price,
            position=position,
            distance_pct=distance_pct
        ))
    
    # Determine trend position
    total_emas = len(emas)
    if above_count == total_emas:
        trend = TrendPosition.STRONG_BULLISH
    elif above_count >= total_emas * 0.8:
        trend = TrendPosition.BULLISH
    elif above_count <= total_emas * 0.2:
        trend = TrendPosition.STRONG_BEARISH
    elif above_count < total_emas * 0.5:
        trend = TrendPosition.BEARISH
    else:
        trend = TrendPosition.NEUTRAL
    
    return ema_analysis, trend


def get_stock_data(symbol: str, period: str = "1y") -> Optional[pd.DataFrame]:
    """Fetch stock data from yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        data = ticker.history(period=period)
        if data.empty:
            print(f"❌ No data found for {symbol}")
            return None
        return data
    except Exception as e:
        print(f"❌ Error fetching data for {symbol}: {e}")
        return None


def get_stock_info(symbol: str) -> Dict:
    """Get stock basic info."""
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return {
            'name': info.get('shortName', info.get('longName', symbol)),
            'sector': info.get('sector', 'N/A'),
            'industry': info.get('industry', 'N/A'),
            'market_cap': info.get('marketCap', 0),
            'pe_ratio': info.get('trailingPE', 0),
            'forward_pe': info.get('forwardPE', 0),
            'dividend_yield': info.get('dividendYield', 0),
            '52w_high': info.get('fiftyTwoWeekHigh', 0),
            '52w_low': info.get('fiftyTwoWeekLow', 0),
        }
    except Exception as e:
        return {'name': symbol, 'sector': 'N/A', 'industry': 'N/A'}


def get_news(symbol: str, max_news: int = 10) -> List[Dict]:
    """Get recent news for a stock."""
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        
        result = []
        if news is None:
            return result
            
        for item in news[:max_news]:
            if not isinstance(item, dict):
                continue
                
            # Handle new yfinance news format (nested 'content' structure)
            content = item.get('content', item)
            
            # Get title
            title = content.get('title', content.get('headline', 'No title'))
            
            # Get publisher
            provider = content.get('provider', {})
            if isinstance(provider, dict):
                publisher = provider.get('displayName', 'Unknown')
            else:
                publisher = content.get('publisher', 'Unknown')
            
            # Get publish date
            pub_date_str = content.get('pubDate', content.get('displayTime', ''))
            try:
                if pub_date_str:
                    # Parse ISO format date string
                    pub_date = datetime.fromisoformat(pub_date_str.replace('Z', '+00:00'))
                    published = pub_date.strftime('%Y-%m-%d %H:%M')
                else:
                    # Try timestamp format
                    publish_time = item.get('providerPublishTime', 0)
                    if publish_time > 0:
                        published = datetime.fromtimestamp(publish_time).strftime('%Y-%m-%d %H:%M')
                    else:
                        published = 'Unknown'
            except:
                published = 'Unknown'
            
            # Get link
            click_through = content.get('clickThroughUrl', {})
            if isinstance(click_through, dict):
                link = click_through.get('url', '')
            else:
                link = content.get('link', content.get('url', ''))
            
            result.append({
                'title': title,
                'publisher': publisher,
                'link': link,
                'published': published,
                'type': content.get('contentType', 'STORY')
            })
        return result
    except Exception as e:
        print(f"⚠️  Error fetching news: {e}")
        return []


def get_earnings(symbol: str) -> Dict:
    """Get earnings information."""
    try:
        ticker = yf.Ticker(symbol)
        
        # Get earnings dates
        earnings_dates = ticker.earnings_dates
        
        # Get quarterly earnings
        quarterly_earnings = ticker.quarterly_earnings
        
        result = {
            'upcoming': None,
            'recent': []
        }
        
        # Parse earnings dates
        if earnings_dates is not None and not earnings_dates.empty:
            now = datetime.now()
            for date_idx in earnings_dates.index:
                try:
                    if hasattr(date_idx, 'to_pydatetime'):
                        date = date_idx.to_pydatetime()
                    else:
                        date = pd.to_datetime(date_idx).to_pydatetime()
                    
                    if date.tzinfo:
                        date = date.replace(tzinfo=None)
                    
                    eps_estimate = earnings_dates.loc[date_idx].get('EPS Estimate', None)
                    reported_eps = earnings_dates.loc[date_idx].get('Reported EPS', None)
                    surprise_pct = earnings_dates.loc[date_idx].get('Surprise(%)', None)
                    
                    entry = {
                        'date': date.strftime('%Y-%m-%d'),
                        'eps_estimate': eps_estimate if pd.notna(eps_estimate) else None,
                        'reported_eps': reported_eps if pd.notna(reported_eps) else None,
                        'surprise_pct': surprise_pct if pd.notna(surprise_pct) else None
                    }
                    
                    if date > now and result['upcoming'] is None:
                        result['upcoming'] = entry
                    elif date <= now:
                        result['recent'].append(entry)
                except Exception:
                    continue
        
        # Limit recent earnings to last 4 quarters
        result['recent'] = result['recent'][:4]
        
        return result
    except Exception as e:
        print(f"⚠️  Error fetching earnings: {e}")
        return {'upcoming': None, 'recent': []}


def analyze_stock(symbol: str) -> Optional[TechnicalSummary]:
    """
    Perform complete technical analysis on a stock.
    """
    # Get historical data
    data = get_stock_data(symbol, period="1y")
    if data is None:
        return None
    
    # Current price
    current_price = data['Close'].iloc[-1]
    
    # Calculate changes
    change_1d = ((data['Close'].iloc[-1] / data['Close'].iloc[-2]) - 1) * 100 if len(data) > 1 else 0
    change_1w = ((data['Close'].iloc[-1] / data['Close'].iloc[-5]) - 1) * 100 if len(data) > 5 else 0
    change_1m = ((data['Close'].iloc[-1] / data['Close'].iloc[-21]) - 1) * 100 if len(data) > 21 else 0
    
    # Calculate EMAs
    ema_periods = [9, 21, 50, 120, 200]
    emas = {}
    for period in ema_periods:
        if len(data) >= period:
            ema = calculate_ema(data['Close'], period)
            emas[period] = ema.iloc[-1]
    
    # Analyze EMA position
    ema_analysis, trend_position = analyze_ema_position(current_price, emas)
    
    # Calculate RSI
    rsi = calculate_rsi(data['Close']).iloc[-1]
    
    # Calculate ATR
    atr = calculate_atr(data['High'], data['Low'], data['Close']).iloc[-1]
    atr_pct = (atr / current_price) * 100
    
    # Calculate volume ratio
    volume_ma = data['Volume'].rolling(window=20).mean().iloc[-1]
    volume_ratio = data['Volume'].iloc[-1] / volume_ma if volume_ma > 0 else 1
    
    # Calculate VPES
    vpes = calculate_vpes(data).iloc[-1]
    
    # Find support/resistance
    support, resistance = find_support_resistance(data)
    
    return TechnicalSummary(
        symbol=symbol,
        price=current_price,
        change_1d=change_1d,
        change_1w=change_1w,
        change_1m=change_1m,
        ema_analysis=ema_analysis,
        trend_position=trend_position,
        rsi=rsi,
        atr=atr,
        atr_pct=atr_pct,
        volume_ratio=volume_ratio,
        vpes=vpes,
        support_level=support,
        resistance_level=resistance
    )


def format_number(num: float, decimals: int = 2) -> str:
    """Format number with appropriate suffix."""
    if abs(num) >= 1e12:
        return f"${num/1e12:.{decimals}f}T"
    elif abs(num) >= 1e9:
        return f"${num/1e9:.{decimals}f}B"
    elif abs(num) >= 1e6:
        return f"${num/1e6:.{decimals}f}M"
    elif abs(num) >= 1e3:
        return f"${num/1e3:.{decimals}f}K"
    else:
        return f"${num:.{decimals}f}"


def print_stock_analysis(symbol: str):
    """
    Print comprehensive stock analysis.
    """
    print("\n" + "=" * 70)
    print(f"📊 STOCK ANALYSIS: {symbol.upper()}")
    print("=" * 70)
    
    # Get stock info
    info = get_stock_info(symbol)
    print(f"\n🏢 {info['name']}")
    print(f"   Sector: {info['sector']} | Industry: {info['industry']}")
    
    if info.get('market_cap'):
        print(f"   Market Cap: {format_number(info['market_cap'])}")
    if info.get('pe_ratio'):
        print(f"   P/E Ratio: {info['pe_ratio']:.2f} (Forward: {info.get('forward_pe', 0):.2f})")
    if info.get('52w_high') and info.get('52w_low'):
        print(f"   52-Week Range: ${info['52w_low']:.2f} - ${info['52w_high']:.2f}")
    
    # Technical analysis
    analysis = analyze_stock(symbol)
    if analysis is None:
        print("❌ Could not analyze stock")
        return
    
    # Price section
    print(f"\n💰 CURRENT PRICE: ${analysis.price:.2f}")
    
    change_color_1d = "🟢" if analysis.change_1d >= 0 else "🔴"
    change_color_1w = "🟢" if analysis.change_1w >= 0 else "🔴"
    change_color_1m = "🟢" if analysis.change_1m >= 0 else "🔴"
    
    print(f"   {change_color_1d} 1-Day:  {analysis.change_1d:+.2f}%")
    print(f"   {change_color_1w} 1-Week: {analysis.change_1w:+.2f}%")
    print(f"   {change_color_1m} 1-Month: {analysis.change_1m:+.2f}%")
    
    # EMA Analysis
    print(f"\n📈 EMA ANALYSIS")
    print("-" * 50)
    print(f"{'EMA':<10} {'Value':<12} {'Position':<10} {'Distance':<12}")
    print("-" * 50)
    
    for ema in analysis.ema_analysis:
        position_icon = "⬆️ " if ema.position == "ABOVE" else "⬇️ "
        distance_color = "🟢" if ema.distance_pct > 0 else "🔴"
        print(f"EMA {ema.ema_period:<5} ${ema.ema_value:<10.2f} {position_icon}{ema.position:<8} {distance_color} {ema.distance_pct:+.2f}%")
    
    print("-" * 50)
    print(f"📊 TREND POSITION: {analysis.trend_position.value}")
    
    # Technical Indicators
    print(f"\n📉 TECHNICAL INDICATORS")
    print("-" * 50)
    
    # RSI interpretation
    if analysis.rsi > 70:
        rsi_status = "⚠️  OVERBOUGHT"
    elif analysis.rsi < 30:
        rsi_status = "⚠️  OVERSOLD"
    else:
        rsi_status = "✅ NEUTRAL"
    print(f"   RSI (14):        {analysis.rsi:.2f} {rsi_status}")
    
    # ATR
    print(f"   ATR (14):        ${analysis.atr:.2f} ({analysis.atr_pct:.2f}% of price)")
    
    # Volume
    if analysis.volume_ratio > 1.5:
        vol_status = "📈 HIGH VOLUME"
    elif analysis.volume_ratio < 0.5:
        vol_status = "📉 LOW VOLUME"
    else:
        vol_status = "📊 NORMAL"
    print(f"   Volume Ratio:    {analysis.volume_ratio:.2f}x {vol_status}")
    
    # VPES
    if analysis.vpes > 0.02:
        vpes_status = "🚀 STRONG BULLISH EXPANSION"
    elif analysis.vpes > 0:
        vpes_status = "📈 BULLISH"
    elif analysis.vpes < -0.02:
        vpes_status = "💥 STRONG BEARISH EXPANSION"
    else:
        vpes_status = "📉 BEARISH"
    print(f"   VPES:            {analysis.vpes:.4f} {vpes_status}")
    
    # Support/Resistance
    print(f"\n🎯 KEY LEVELS")
    print("-" * 50)
    distance_to_support = ((analysis.price - analysis.support_level) / analysis.price) * 100
    distance_to_resistance = ((analysis.resistance_level - analysis.price) / analysis.price) * 100
    print(f"   Support:         ${analysis.support_level:.2f} ({distance_to_support:.2f}% below)")
    print(f"   Resistance:      ${analysis.resistance_level:.2f} ({distance_to_resistance:.2f}% above)")
    
    # Earnings
    print(f"\n💼 EARNINGS")
    print("-" * 50)
    earnings = get_earnings(symbol)
    
    if earnings['upcoming']:
        print(f"   📅 Next Earnings: {earnings['upcoming']['date']}")
        if earnings['upcoming']['eps_estimate']:
            print(f"      EPS Estimate: ${earnings['upcoming']['eps_estimate']:.2f}")
    else:
        print("   📅 Next Earnings: Not scheduled")
    
    if earnings['recent']:
        print(f"\n   📊 Recent Earnings:")
        for i, e in enumerate(earnings['recent'][:4]):
            surprise_str = ""
            if e['surprise_pct'] is not None:
                surprise_icon = "✅" if e['surprise_pct'] >= 0 else "❌"
                surprise_str = f" {surprise_icon} Surprise: {e['surprise_pct']:+.2f}%"
            
            eps_str = f"EPS: ${e['reported_eps']:.2f}" if e['reported_eps'] else "EPS: N/A"
            print(f"      {e['date']}: {eps_str}{surprise_str}")
    
    # News
    print(f"\n📰 RECENT NEWS (Last 3 Months)")
    print("-" * 50)
    news = get_news(symbol, max_news=10)
    
    if news:
        for i, article in enumerate(news[:10], 1):
            # Highlight earnings-related news
            title = article['title']
            if any(keyword in title.lower() for keyword in ['earnings', 'quarterly', 'q1', 'q2', 'q3', 'q4', 'profit', 'revenue', 'beat', 'miss']):
                print(f"   💰 [{article['published']}] {title[:60]}...")
            else:
                print(f"   📰 [{article['published']}] {title[:60]}...")
    else:
        print("   No recent news found")
    
    print("\n" + "=" * 70)
    print()


def print_quick_summary(symbols: List[str]):
    """
    Print a quick multi-stock comparison table.
    """
    print("\n" + "=" * 100)
    print("📊 QUICK SUMMARY COMPARISON")
    print("=" * 100)
    
    # Header
    print(f"{'Symbol':<8} {'Price':>10} {'1D%':>8} {'1W%':>8} {'1M%':>8} {'RSI':>6} {'Trend':>20} {'VPES':>10}")
    print("-" * 100)
    
    for symbol in symbols:
        analysis = analyze_stock(symbol)
        if analysis:
            trend_short = analysis.trend_position.value.split()[-1]  # Just get BULLISH/BEARISH/NEUTRAL
            print(f"{symbol:<8} ${analysis.price:>8.2f} {analysis.change_1d:>+7.2f}% {analysis.change_1w:>+7.2f}% {analysis.change_1m:>+7.2f}% {analysis.rsi:>5.1f} {trend_short:>20} {analysis.vpes:>+10.4f}")
        else:
            print(f"{symbol:<8} {'N/A':>10} {'N/A':>8} {'N/A':>8} {'N/A':>8} {'N/A':>6} {'N/A':>20} {'N/A':>10}")
    
    print("=" * 100)
    print()


def main():
    """Main function."""
    print("\n🔍 SAIYAN Stock Technical Analyzer")
    print("=" * 70)
    
    if len(sys.argv) < 2:
        # Default stocks to analyze
        symbols = ["AAPL", "MSFT", "NVDA"]
        print(f"No symbols provided. Analyzing default: {', '.join(symbols)}")
    else:
        symbols = [s.upper() for s in sys.argv[1:]]
        print(f"Analyzing: {', '.join(symbols)}")
    
    # Individual analysis for each stock
    for symbol in symbols:
        print_stock_analysis(symbol)
    
    # Quick summary comparison if multiple stocks
    if len(symbols) > 1:
        print_quick_summary(symbols)


if __name__ == "__main__":
    main()
