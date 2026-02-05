# Discord Signal Trading System - File Structure & Usage

## Directory Structure (`workspace/scripts/`)

| Folder | Contents | Purpose |
|--------|----------|---------|
| `data_collection/` | `discord_scraper.py` | Fetches messages (60 days history) |
| | `download_images.py` | Downloads attached chart images |
| `core_analysis/` | `stock_message_analyzer.js` | Core logic for parsing tickers/sentiment |
| | `llm_analyzer.py` | MiniMax LLM integration for deep analysis |
| | `analyze_channels_deep.js` | Generates Trader Profiles |
| `report_generation/`| `generate_full_analysis.js` | Generates main weekly/daily text reports |
| `validation/` | `prepare_and_validate.js` | Main entry point for signal validation |
| | `validate_signals_v2.py` | Python script for calculating returns |
| `utils/` | `rename_images.js` | Renames images with ticker/signal metadata |

## Data Flow

1. **Scrape**: `data_collection/discord_scraper.py` -> `data/real_discord_messages_goku_wilson_60d.txt`
2. **Analyze**: `report_generation/generate_full_analysis.js` reads JSON -> Uses `core_analysis/stock_message_analyzer.js` -> Outputs to `data/reports/`
3. **Validate**: `validation/prepare_and_validate.js` reads JSON -> Calls `validation/validate_signals_v2.py` -> Outputs performance report

## Usage Commands

### 1. Data Collection
```bash
# Fetch new messages (optional if cached)
python scripts/data_collection/discord_scraper.py
```

### 2. Generate Reports
```bash
# Generate Wilson (Weekly) and Goku (Daily) analysis
node scripts/report_generation/generate_full_analysis.js
```

### 3. Validate Signals
```bash
# Check performance of recent signals
node scripts/validation/prepare_and_validate.js
```

### 4. Image Processing (Optional)
```bash
# Download images
python scripts/data_collection/download_images.py

# Rename images with metadata
node scripts/utils/rename_images.js
```

### 5. LLM Deep Dive (Optional)
```bash
# Run batch LLM analysis on specific server
python scripts/core_analysis/llm_analyzer.py --batch data/real_discord_messages_goku_wilson_60d.txt wilson
```
