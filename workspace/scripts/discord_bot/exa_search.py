#!/usr/bin/env python3
"""
Exa Search Skill — Internet Search for Claude Agent
=====================================================
Provides web search capabilities using Exa.ai API.
Claude can use this to find market news, research companies,
and get real-time information.

Usage:
    python exa_search.py search "NVDA earnings report 2026"
    python exa_search.py search "market crash tariff news" --num 5
    python exa_search.py news "AAPL NVDA TSLA"              # Stock-specific news
    python exa_search.py research "semiconductor supply chain"  # Deep research
    python exa_search.py similar "https://some-article-url"   # Find similar content

Environment:
    EXA_API_KEY: Exa.ai API key (required)
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timedelta
from typing import List, Optional

logging.basicConfig(level=logging.WARNING, format="%(message)s")
log = logging.getLogger("exa-search")

# API Key
EXA_API_KEY = os.environ.get("EXA_API_KEY", "01f941f0-b5bf-46aa-80a3-f3d53288c90e")


def get_exa_client():
    """Get Exa client, installing SDK if needed."""
    try:
        from exa_py import Exa
        return Exa(api_key=EXA_API_KEY)
    except ImportError:
        print("ERROR: exa-py not installed. Run: pip install exa-py")
        sys.exit(1)


# =========================================================================
# Search Commands
# =========================================================================

def cmd_search(query: str, num_results: int = 5, days_back: int = 30) -> str:
    """General web search using Exa.ai.

    Returns search results with titles, URLs, and text snippets.
    """
    exa = get_exa_client()

    start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')

    results = exa.search_and_contents(
        query,
        type="auto",
        num_results=num_results,
        start_published_date=start_date,
        text={"max_characters": 500},
    )

    lines = [f"=== Exa Search: \"{query}\" ({len(results.results)} results) ===\n"]

    for i, r in enumerate(results.results, 1):
        lines.append(f"{i}. {r.title}")
        lines.append(f"   URL: {r.url}")
        if r.published_date:
            lines.append(f"   Date: {r.published_date}")
        if r.text:
            # Clean and truncate text
            text = r.text.strip().replace('\n', ' ')[:400]
            lines.append(f"   {text}")
        lines.append("")

    return "\n".join(lines)


def cmd_news(tickers: str, num_results: int = 5) -> str:
    """Search for stock-specific news.

    Args:
        tickers: Space-separated ticker symbols (e.g., "AAPL NVDA TSLA")
    """
    exa = get_exa_client()

    ticker_list = [t.upper().replace("$", "") for t in tickers.split()]
    query = f"stock market news for {', '.join(ticker_list)} latest analysis"

    start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

    results = exa.search_and_contents(
        query,
        type="neural",
        num_results=num_results,
        start_published_date=start_date,
        text={"max_characters": 600},
        category="news",
    )

    lines = [f"=== Market News: {', '.join(ticker_list)} ({len(results.results)} results) ===\n"]

    for i, r in enumerate(results.results, 1):
        lines.append(f"{i}. {r.title}")
        lines.append(f"   URL: {r.url}")
        if r.published_date:
            lines.append(f"   Published: {r.published_date}")
        if r.text:
            text = r.text.strip().replace('\n', ' ')[:500]
            lines.append(f"   {text}")
        lines.append("")

    if not results.results:
        lines.append("   No recent news found for these tickers.")

    return "\n".join(lines)


def cmd_research(topic: str, num_results: int = 8) -> str:
    """Deep research on a topic — longer text extracts for analysis.

    Args:
        topic: Research topic (e.g., "semiconductor supply chain 2026")
    """
    exa = get_exa_client()

    start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    results = exa.search_and_contents(
        topic,
        type="neural",
        num_results=num_results,
        start_published_date=start_date,
        text={"max_characters": 1000},
    )

    lines = [f"=== Research: \"{topic}\" ({len(results.results)} results) ===\n"]

    for i, r in enumerate(results.results, 1):
        lines.append(f"--- [{i}] {r.title} ---")
        lines.append(f"URL: {r.url}")
        if r.published_date:
            lines.append(f"Date: {r.published_date}")
        if r.text:
            text = r.text.strip()[:900]
            lines.append(f"\n{text}\n")

    return "\n".join(lines)


def cmd_similar(url: str, num_results: int = 5) -> str:
    """Find content similar to a given URL.

    Args:
        url: URL to find similar content for
    """
    exa = get_exa_client()

    results = exa.find_similar_and_contents(
        url,
        num_results=num_results,
        text={"max_characters": 400},
    )

    lines = [f"=== Similar to: {url} ({len(results.results)} results) ===\n"]

    for i, r in enumerate(results.results, 1):
        lines.append(f"{i}. {r.title}")
        lines.append(f"   URL: {r.url}")
        if r.text:
            text = r.text.strip().replace('\n', ' ')[:350]
            lines.append(f"   {text}")
        lines.append("")

    return "\n".join(lines)


def cmd_market_pulse(num_results: int = 8) -> str:
    """Get today's market pulse — top financial news and analysis."""
    exa = get_exa_client()

    queries = [
        "stock market today analysis outlook",
        "breaking financial news market moving",
    ]

    all_results = []
    start_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')

    for q in queries:
        try:
            results = exa.search_and_contents(
                q,
                type="neural",
                num_results=num_results // 2,
                start_published_date=start_date,
                text={"max_characters": 400},
                category="news",
            )
            all_results.extend(results.results)
        except Exception as e:
            log.warning(f"Search error for '{q}': {e}")

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for r in all_results:
        if r.url not in seen_urls:
            seen_urls.add(r.url)
            unique.append(r)

    lines = [f"=== Market Pulse — {datetime.now().strftime('%Y-%m-%d %H:%M')} ({len(unique)} articles) ===\n"]

    for i, r in enumerate(unique[:num_results], 1):
        lines.append(f"{i}. {r.title}")
        lines.append(f"   URL: {r.url}")
        if r.published_date:
            lines.append(f"   Date: {r.published_date}")
        if r.text:
            text = r.text.strip().replace('\n', ' ')[:350]
            lines.append(f"   {text}")
        lines.append("")

    return "\n".join(lines)


# =========================================================================
# Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Exa Search Skill — Internet search for Claude agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  search QUERY          General web search
  news TICKERS          Stock-specific news (e.g., "AAPL NVDA")
  research TOPIC        Deep research with longer extracts
  similar URL           Find similar content
  pulse                 Today's market pulse

Examples:
  python exa_search.py search "NVDA earnings Q4 2026"
  python exa_search.py news "AAPL NVDA TSLA" --num 8
  python exa_search.py research "AI chip demand 2026 outlook"
  python exa_search.py pulse
""",
    )
    parser.add_argument('command', choices=['search', 'news', 'research', 'similar', 'pulse'],
                        help='Search command')
    parser.add_argument('query', nargs='*', help='Search query or URL')
    parser.add_argument('--num', type=int, default=5, help='Number of results (default: 5)')
    parser.add_argument('--days', type=int, default=30, help='Days back to search (default: 30)')

    args = parser.parse_args()

    query = ' '.join(args.query) if args.query else ''

    if args.command == 'search':
        if not query:
            print("Error: search requires a query")
            return
        result = cmd_search(query, num_results=args.num, days_back=args.days)

    elif args.command == 'news':
        if not query:
            print("Error: news requires ticker symbols")
            return
        result = cmd_news(query, num_results=args.num)

    elif args.command == 'research':
        if not query:
            print("Error: research requires a topic")
            return
        result = cmd_research(query, num_results=args.num)

    elif args.command == 'similar':
        if not query:
            print("Error: similar requires a URL")
            return
        result = cmd_similar(query, num_results=args.num)

    elif args.command == 'pulse':
        result = cmd_market_pulse(num_results=args.num)

    else:
        result = f"Unknown command: {args.command}"

    print(result)


if __name__ == "__main__":
    main()
