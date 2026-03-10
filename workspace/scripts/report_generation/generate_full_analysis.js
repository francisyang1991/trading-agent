/**
 * Full Analysis Generator
 * - Wilson: Group by week, sorted by signal strength
 * - Goku: All signals sorted by day (DESC), with Heat Scores
 * - Uses cached messages (no Discord calls)
 */

const fs = require('fs');
const path = require('path');
const StockMessageAnalyzer = require('../core_analysis/stock_message_analyzer');

// Use cached data
const INPUT_FILE = path.join(__dirname, '../../data/real_discord_messages_goku_wilson_60d.txt');
const OUTPUT_DIR = path.join(__dirname, '../../data/reports');

const GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461"];
const WILSON_CHANNELS = ["1211549165629476924"];

const analyzer = new StockMessageAnalyzer(null);

// Signal strength keywords
const STRONG_LONG_WORDS = ['long', 'buy', 'calls', 'bullish', 'breakout', 'accumulate', 'conviction', 'confident', 'will', 'going to', 'love', 'great', 'strong', 'flag', 'tightening'];
const STRONG_SHORT_WORDS = ['short', 'sell', 'puts', 'bearish', 'breakdown', 'closing', 'exit', 'dump', 'crash', 'risk', 'hate', 'terrible', 'weak', 'heavy'];
const POSITION_WORDS = ['position', 'took', 'taking', 'added', 'adding', 'bought', 'sold', 'entered', 'entering'];

function formatDate(isoString) {
  return isoString.split('T')[0];
}

function getWeekNumber(date) {
  const d = new Date(date);
  const start = new Date(d.getFullYear(), 0, 1);
  const diff = d - start;
  const oneWeek = 604800000;
  return Math.ceil((diff + start.getDay() * 86400000) / oneWeek);
}

function getWeekLabel(date) {
  const d = new Date(date);
  const weekStart = new Date(d);
  weekStart.setDate(d.getDate() - d.getDay());
  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekStart.getDate() + 6);
  
  const format = (dt) => `${dt.getMonth()+1}/${dt.getDate()}`;
  return `Week of ${format(weekStart)} - ${format(weekEnd)}`;
}

function calculateSignalStrength(content) {
  const lower = content.toLowerCase();
  let strength = 0;
  POSITION_WORDS.forEach(w => { if (lower.includes(w)) strength += 3; });
  STRONG_LONG_WORDS.forEach(w => { if (lower.includes(w)) strength += 1; });
  STRONG_SHORT_WORDS.forEach(w => { if (lower.includes(w)) strength += 1; });
  if (lower.match(/\$\d+/)) strength += 2;
  return strength;
}

function getDirection(content) {
  const lower = content.toLowerCase();
  let bull = STRONG_LONG_WORDS.filter(w => lower.includes(w)).length;
  let bear = STRONG_SHORT_WORDS.filter(w => lower.includes(w)).length;
  
  if (bull > bear) return 'LONG';
  if (bear > bull) return 'SHORT';
  return '';
}

function extractTickers(content) {
  const result = analyzer.analyzeMessage({
    content,
    author: 'Unknown',
    timestamp: new Date().toISOString(),
    id: '0'
  });
  return result ? result.tickers : [];
}

// Calculate Heat Score for Goku tickers
function calculateTickerHeat(ticker, currentMsgDate, allMessages) {
  if (!ticker || ticker === 'CHART') return 0;
  
  const msgDate = new Date(currentMsgDate);
  const fiveDaysAgo = new Date(msgDate);
  fiveDaysAgo.setDate(msgDate.getDate() - 5);
  
  let recentMentions = 0;
  
  allMessages.forEach(m => {
    const mDate = new Date(m.timestamp);
    if (mDate >= fiveDaysAgo && mDate <= msgDate) {
      if (m.tickers.includes(ticker)) {
        recentMentions++;
      }
    }
  });
  
  let score = 1;
  if (recentMentions > 1) score += 1; // Mentioned before
  if (recentMentions > 3) score += 2; // Hot topic
  
  return score;
}

// ============ WILSON ANALYSIS (Group by Week) ============
function generateWilsonAnalysis(messages) {
  const byWeek = {};
  
  messages.forEach(msg => {
    const content = msg.content || '';
    const timestamp = msg.timestamp;
    const weekLabel = getWeekLabel(timestamp);
    const weekNum = getWeekNumber(timestamp);
    
    if (!byWeek[weekNum]) {
      byWeek[weekNum] = { label: weekLabel, messages: [] };
    }
    
    const tickers = extractTickers(content);
    const strength = calculateSignalStrength(content);
    const direction = getDirection(content);
    
    byWeek[weekNum].messages.push({
      date: formatDate(timestamp),
      time: timestamp.split('T')[1].split('.')[0].substring(0, 5),
      author: msg.author?.username || 'Unknown',
      content: content.replace(/\n/g, ' '),
      tickers,
      strength,
      direction,
      hasPosition: POSITION_WORDS.some(w => content.toLowerCase().includes(w))
    });
  });
  
  const sortedWeeks = Object.entries(byWeek)
    .sort((a, b) => parseInt(b[0]) - parseInt(a[0])); // Reverse chronological
  
  let output = `${'='.repeat(80)}\n`;
  output += `WILSON SERVER - FULL FUNDAMENTAL ANALYSIS (Newest First)\n`;
  output += `${'='.repeat(80)}\n`;
  output += `Total Messages: ${messages.length}\n\n`;
  
  for (const [weekNum, data] of sortedWeeks) {
    data.messages.sort((a, b) => b.strength - a.strength);
    
    output += `${'─'.repeat(80)}\n`;
    output += `### ${data.label} (${data.messages.length} messages)\n`;
    output += `${'─'.repeat(80)}\n`;
    
    data.messages.forEach(m => {
      const dirTag = m.direction ? `[${m.direction}]` : '';
      const posTag = m.hasPosition ? '[POSITION]' : '';
      const tickerStr = m.tickers.length > 0 ? m.tickers.join(' ') : '';
      const strengthBar = '★'.repeat(Math.min(m.strength, 5));
      
      output += `\n${m.date} ${m.time} ${strengthBar} ${dirTag}${posTag}\n`;
      output += `Tickers: ${tickerStr || 'General'}\n`;
      output += `${m.content}\n`;
    });
    output += `\n`;
  }
  
  return output;
}

// ============ GOKU ANALYSIS (All Signals by Day) ============
function generateGokuAnalysis(messages) {
  const byDay = {};
  
  // Pre-process to get tickers for heat calculation
  const processedMessages = messages.map(msg => ({
    ...msg,
    tickers: extractTickers(msg.content || '')
  }));
  
  processedMessages.forEach(msg => {
    const content = msg.content || '';
    const timestamp = msg.timestamp;
    const day = formatDate(timestamp);
    
    if (!byDay[day]) byDay[day] = [];
    
    const direction = getDirection(content);
    
    // Process each ticker in the message
    if (msg.tickers.length > 0) {
      msg.tickers.forEach(ticker => {
        const heat = calculateTickerHeat(ticker, timestamp, processedMessages);
        byDay[day].push({
          time: timestamp.split('T')[1].split('.')[0].substring(0, 5),
          author: msg.author?.username || 'Unknown',
          content: content.replace(/\n/g, ' '),
          ticker,
          direction,
          heat
        });
      });
    } else {
      // General chart/comment
      byDay[day].push({
        time: timestamp.split('T')[1].split('.')[0].substring(0, 5),
        author: msg.author?.username || 'Unknown',
        content: content.replace(/\n/g, ' '),
        ticker: 'CHART',
        direction,
        heat: 0
      });
    }
  });
  
  // Sort days REVERSE chronologically (Newest First)
  const sortedDays = Object.entries(byDay)
    .sort((a, b) => new Date(b[0]) - new Date(a[0]));
  
  let output = `${'='.repeat(80)}\n`;
  output += `GOKU SERVER - FULL TECHNICAL SIGNALS (Newest First)\n`;
  output += `${'='.repeat(80)}\n`;
  output += `Total Messages: ${messages.length} | Days Covered: ${sortedDays.length}\n\n`;
  
  for (const [day, msgs] of sortedDays) {
    // Sort by time within day (descending)
    msgs.sort((a, b) => b.time.localeCompare(a.time));
    
    const dayOfWeek = new Date(day).toLocaleDateString('en-US', { weekday: 'short' });
    
    output += `${'─'.repeat(80)}\n`;
    output += `### ${day} (${dayOfWeek}) - ${msgs.length} signals\n`;
    output += `${'─'.repeat(80)}\n`;
    
    msgs.forEach(m => {
      const dirTag = m.direction ? `[${m.direction}]` : '[NEUT]';
      const heatStr = m.heat > 1 ? `(Heat: ${m.heat} 🔥)` : '';
      
      // Format: [LONG] $TICKER (Heat: X) : Message
      output += `${m.time} ${dirTag} ${m.ticker} ${heatStr}\n`;
      output += `      ${m.content.substring(0, 200)}...\n`;
    });
    output += `\n`;
  }
  
  return output;
}

async function run() {
  console.log('Loading cached messages...');
  
  if (!fs.existsSync(INPUT_FILE)) {
    console.error('Cached data not found:', INPUT_FILE);
    return;
  }
  
  const rawData = JSON.parse(fs.readFileSync(INPUT_FILE, 'utf8'));
  
  const gokuMessages = [];
  const wilsonMessages = [];
  
  for (const [channelId, messages] of Object.entries(rawData)) {
    if (GOKU_CHANNELS.includes(channelId)) gokuMessages.push(...messages);
    if (WILSON_CHANNELS.includes(channelId)) wilsonMessages.push(...messages);
  }
  
  console.log(`Goku: ${gokuMessages.length} messages`);
  console.log(`Wilson: ${wilsonMessages.length} messages`);
  
  // Generate Wilson analysis
  const wilsonOutput = generateWilsonAnalysis(wilsonMessages);
  fs.writeFileSync(path.join(OUTPUT_DIR, 'wilson_full_by_week.txt'), wilsonOutput);
  
  // Generate Goku analysis
  const gokuOutput = generateGokuAnalysis(gokuMessages);
  fs.writeFileSync(path.join(OUTPUT_DIR, 'goku_full_by_day.txt'), gokuOutput);
  
  console.log('\nReports saved (Newest First):');
  console.log('  - reports/wilson_full_by_week.txt');
  console.log('  - reports/goku_full_by_day.txt');
}

run();
