#!/bin/bash

echo "=========================================="
echo "TRADING AGENT - DAILY UPDATE JOB"
echo "Date: $(date)"
echo "=========================================="

# 1. Update Discord Data
echo ">>> Step 1: Fetching new Discord messages..."
/Users/francisyang/Downloads/trading_agent/workspace/venv/bin/python /Users/francisyang/Downloads/trading_agent/workspace/scripts/data_collection/discord_scraper.py

# 2. Generate Full Reports (Goku/Wilson)
echo ">>> Step 2: Generating full analysis reports..."
node /Users/francisyang/Downloads/trading_agent/workspace/scripts/report_generation/generate_full_analysis.js

# 3. Generate Daily Recommendations
echo ">>> Step 3: Generating Daily Trading Plan..."
/Users/francisyang/Downloads/trading_agent/workspace/venv/bin/python /Users/francisyang/Downloads/trading_agent/workspace/scripts/report_generation/daily_recommendations.py

echo "=========================================="
echo "Update Complete!"
echo "Reports available in: workspace/data/reports/"
