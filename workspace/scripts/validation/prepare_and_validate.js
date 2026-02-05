const fs = require('fs');
const path = require('path');
const StockMessageAnalyzer = require('../core_analysis/stock_message_analyzer');
const { spawn } = require('child_process');

const INPUT_FILE = path.join(__dirname, '../../data/real_discord_messages_goku_wilson_60d.txt');
const DATA_DIR = path.join(__dirname, '../../data');

// Channel Mapping
const GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461"];
const WILSON_CHANNELS = ["1211549165629476924"];

const analyzer = new StockMessageAnalyzer(null);

function getTopSignals(messages, topN = 5) {
  // 1. Analyze all messages
  const analysis = analyzer.analyzeMessages(messages.map(m => ({
    content: m.content,
    author: m.author?.username || 'Unknown',
    timestamp: m.timestamp,
    id: m.id
  })));

  // 2. Count tickers
  const tickerCounts = Object.entries(analysis.byTicker)
    .map(([ticker, msgs]) => ({ ticker, count: msgs.length, msgs }))
    .sort((a, b) => b.count - a.count)
    .slice(0, topN);

  // 3. Get the *first* mention of each top ticker as the "signal"
  return tickerCounts.map(t => {
    // Sort messages by time to find the first one
    const sortedMsgs = t.msgs.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
    const firstMsg = sortedMsgs[0];
    return {
      ticker: t.ticker,
      date: firstMsg.timestamp,
      content: firstMsg.originalMessage,
      author: firstMsg.author
    };
  });
}

async function run() {
  const rawData = JSON.parse(fs.readFileSync(INPUT_FILE, 'utf8'));
  const gokuMessages = [];
  const wilsonMessages = [];

  for (const [channelId, messages] of Object.entries(rawData)) {
    if (GOKU_CHANNELS.includes(channelId)) gokuMessages.push(...messages);
    if (WILSON_CHANNELS.includes(channelId)) wilsonMessages.push(...messages);
  }

  const gokuSignals = getTopSignals(gokuMessages, 15);
  const wilsonSignals = getTopSignals(wilsonMessages, 15);

  const allSignals = [...gokuSignals.map(s => ({...s, source: 'Goku'})), ...wilsonSignals.map(s => ({...s, source: 'Wilson'}))];
  
  // Save signals to JSON for Python script to consume
  const signalsFile = path.join(DATA_DIR, 'signals_to_validate.json');
  fs.writeFileSync(signalsFile, JSON.stringify(allSignals, null, 2));

  console.log('Prepared signals for validation. Running Python validator...');

  // Call Python script
  const pythonScript = path.join(__dirname, 'validate_signals_v2.py');
  const pythonProcess = spawn(path.join(__dirname, '../../venv/bin/python'), [pythonScript, signalsFile], {
    cwd: __dirname
  });

  let output = '';
  pythonProcess.stdout.on('data', (data) => { output += data.toString(); process.stdout.write(data); });
  pythonProcess.stderr.on('data', (data) => { console.error(data.toString()); });

  pythonProcess.on('close', (code) => {
    if (code !== 0) {
      console.error(`Python script exited with code ${code}`);
      return;
    }
    console.log('Validation complete.');
  });
}

run();
