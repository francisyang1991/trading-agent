# Internet Search Skill (Exa.ai)

You have access to Exa.ai web search for real-time market news, company research, and general information.

## Available Commands

Run from `~/trading-agent/workspace/scripts/discord_bot/`:

```bash
# General web search
python exa_search.py search "NVDA earnings Q4 2026"

# Stock-specific news (last 7 days)
python exa_search.py news "AAPL NVDA TSLA"

# Deep research with longer text extracts
python exa_search.py research "semiconductor supply chain outlook 2026"

# Today's market pulse (top financial news)
python exa_search.py pulse

# Find similar articles to a URL
python exa_search.py similar "https://example.com/article"

# Control number of results
python exa_search.py search "tariff impact tech stocks" --num 8
python exa_search.py news "META GOOGL" --num 10

# Search further back in time
python exa_search.py search "Fed interest rate decision" --days 90
```

## When to Use

- **"What's happening with TICKER?"** → `python exa_search.py news "TICKER"`
- **"Any market news today?"** → `python exa_search.py pulse`
- **"Research topic X"** → `python exa_search.py research "topic"`
- **"Find recent articles about..."** → `python exa_search.py search "query"`

## Combining with Pipeline Skill

For best stock recommendations, combine both skills:

1. **Get market context first**: `python exa_search.py pulse`
2. **Check news for specific tickers**: `python exa_search.py news "NVDA AMD AVGO"`
3. **Run pipeline scan**: `python pipeline_skill.py scan 80`
4. **Cross-reference**: Use news sentiment + pipeline scores for final recommendations

## Important Notes

- Exa.ai API key is pre-configured in the environment
- The `news` command filters for financial news category
- The `research` command returns longer text extracts (up to 1000 chars per result)
- Results include published dates — always check recency
