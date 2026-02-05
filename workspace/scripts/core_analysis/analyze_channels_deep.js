const fs = require('fs');
const path = require('path');
const StockMessageAnalyzer = require('./stock_message_analyzer');

const INPUT_FILE = path.join(__dirname, '../../data/real_discord_messages_goku_wilson_60d.txt');
const REPORT_DIR = path.join(__dirname, '../../data/reports');

// Channel Mapping
const GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461"];
const WILSON_CHANNELS = ["1211549165629476924"];

const analyzer = new StockMessageAnalyzer(null);

const TECHNICAL_KEYWORDS = ['ema', 'rsi', 'macd', 'breakout', 'flag', 'cup', 'handle', 'support', 'resistance', 'volume', 'setup', 'chart', 'technical', 'moving average', 'trend', 'gap', 'fbo', 'squat', 'base', 'tightening'];
const FUNDAMENTAL_KEYWORDS = ['earnings', 'revenue', 'pe', 'valuation', 'growth', 'margin', 'cash flow', 'guidance', 'macro', 'sector', 'thesis', 'fundamental', 'value', 'cheap', 'expensive', 'premium'];

function analyzeContent(messages) {
  const analysis = analyzer.analyzeMessages(messages.map(m => ({
    content: m.content,
    author: m.author?.username || 'Unknown',
    timestamp: m.timestamp,
    id: m.id
  })));

  // Ticker Preferences
  const tickers = Object.entries(analysis.byTicker)
    .map(([ticker, msgs]) => ({ ticker, count: msgs.length }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  // Style Analysis
  let technicalCount = 0;
  let fundamentalCount = 0;
  
  messages.forEach(msg => {
    const text = msg.content.toLowerCase();
    TECHNICAL_KEYWORDS.forEach(k => { if (text.includes(k)) technicalCount++; });
    FUNDAMENTAL_KEYWORDS.forEach(k => { if (text.includes(k)) fundamentalCount++; });
  });

  return {
    tickers,
    style: {
      technical: technicalCount,
      fundamental: fundamentalCount,
      ratio: technicalCount / (fundamentalCount || 1)
    },
    topTickers: tickers.map(t => t.ticker)
  };
}

function run() {
  const rawData = JSON.parse(fs.readFileSync(INPUT_FILE, 'utf8'));
  const gokuMessages = [];
  const wilsonMessages = [];

  for (const [channelId, messages] of Object.entries(rawData)) {
    if (GOKU_CHANNELS.includes(channelId)) gokuMessages.push(...messages);
    if (WILSON_CHANNELS.includes(channelId)) wilsonMessages.push(...messages);
  }

  const gokuAnalysis = analyzeContent(gokuMessages);
  const wilsonAnalysis = analyzeContent(wilsonMessages);

  // Generate Channel Preferences Report
  const prefReport = `
CHANNEL PREFERENCES & TRADER PROFILES
=====================================

1. TRADER PROFILES
------------------

[GOKU SERVER] (Primary Trader: tradergoku)
- Focus: ${gokuAnalysis.style.ratio > 1.5 ? 'Technical Analysis / Charts' : 'Balanced / Fundamental'}
- Key Metrics:
  - Technical Keywords Found: ${gokuAnalysis.style.technical}
  - Fundamental Keywords Found: ${gokuAnalysis.style.fundamental}
  - Ratio (Tech/Fund): ${gokuAnalysis.style.ratio.toFixed(2)}
- Trading Style: Focuses on price action, moving averages (EMA8, EMA21), and chart patterns (flags, cup & handle). Uses terms like "tightening", "flag", "breakout".

[WILSON SERVER] (Primary Trader: wilson0002)
- Focus: ${wilsonAnalysis.style.ratio < 1.0 ? 'Fundamental Analysis / Macro' : 'Balanced / Technical'}
- Key Metrics:
  - Technical Keywords Found: ${wilsonAnalysis.style.technical}
  - Fundamental Keywords Found: ${wilsonAnalysis.style.fundamental}
  - Ratio (Tech/Fund): ${wilsonAnalysis.style.ratio.toFixed(2)}
- Trading Style: Focuses on earnings, valuation (PE), macro trends, and sector rotation. Uses terms like "valuation", "earnings", "guidance".

2. STOCK PREFERENCES
--------------------

[GOKU FAVORITES]
${gokuAnalysis.tickers.map(t => `- ${t.ticker}: ${t.count} mentions`).join('\n')}

[WILSON FAVORITES]
${wilsonAnalysis.tickers.map(t => `- ${t.ticker}: ${t.count} mentions`).join('\n')}

3. OVERLAP
----------
Tickers mentioned by both: ${gokuAnalysis.topTickers.filter(t => wilsonAnalysis.topTickers.includes(t)).join(', ') || 'None in Top 10'}
`;

  fs.writeFileSync(path.join(REPORT_DIR, 'trader_profiles_and_preferences.txt'), prefReport);
  console.log(prefReport);
}

run();
