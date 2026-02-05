const fs = require('fs');
const path = require('path');
const StockMessageAnalyzer = require('../core_analysis/stock_message_analyzer');

const INPUT_FILE = path.join(__dirname, '../../data/real_discord_messages_goku_wilson_60d.txt');
const IMAGES_DIR = path.join(__dirname, '../../data/images');
const RENAMED_DIR = path.join(__dirname, '../../data/images_renamed');

const GOKU_CHANNELS = ["1277321989874385029", "1411717415565393970", "1227315745352847461"];
const WILSON_CHANNELS = ["1211549165629476924"];

const analyzer = new StockMessageAnalyzer(null);

function formatTimeShort(isoString) {
  const d = new Date(isoString);
  const month = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}${month}${day}`;
}

function getDirection(content) {
  const lower = content.toLowerCase();
  const bullishWords = ['long', 'buy', 'calls', 'bullish', 'breakout', 'accumulate', 'dip', 'support', 'bounce', 'flag', 'tightening'];
  const bearishWords = ['short', 'sell', 'puts', 'bearish', 'breakdown', 'overbought', 'closing', 'exit', 'heavy', 'weak'];
  
  let bull = 0, bear = 0;
  bullishWords.forEach(w => { if (lower.includes(w)) bull++; });
  bearishWords.forEach(w => { if (lower.includes(w)) bear++; });
  
  if (bull > bear) return 'LONG';
  if (bear > bull) return 'SHORT';
  return 'NEUT';
}

function run() {
  if (!fs.existsSync(INPUT_FILE)) {
    console.error('Input file not found');
    return;
  }
  
  if (!fs.existsSync(RENAMED_DIR)) {
    fs.mkdirSync(RENAMED_DIR, { recursive: true });
  }
  
  const rawData = JSON.parse(fs.readFileSync(INPUT_FILE, 'utf8'));
  
  // Build a map: messageId -> { channel, timestamp, tickers, direction, server }
  const messageMap = {};
  
  for (const [channelId, messages] of Object.entries(rawData)) {
    const server = GOKU_CHANNELS.includes(channelId) ? 'GOKU' : 
                   WILSON_CHANNELS.includes(channelId) ? 'WILSON' : 'OTHER';
    
    for (const msg of messages) {
      if (!msg.attachments || msg.attachments.length === 0) continue;
      
      const content = msg.content || '';
      const analysisResult = analyzer.analyzeMessage({
        content,
        author: msg.author?.username || 'Unknown',
        timestamp: msg.timestamp,
        id: msg.id
      });
      
      const tickers = analysisResult ? analysisResult.tickers : [];
      const ticker = tickers.length > 0 ? tickers[0].replace('$', '') : 'CHART';
      const direction = getDirection(content);
      const dateStr = formatTimeShort(msg.timestamp);
      
      messageMap[msg.id] = {
        channelId,
        server,
        timestamp: msg.timestamp,
        dateStr,
        ticker,
        direction,
        attachments: msg.attachments
      };
    }
  }
  
  console.log(`Found ${Object.keys(messageMap).length} messages with attachments.`);
  
  // Now scan images folder and rename
  const existingImages = fs.readdirSync(IMAGES_DIR).filter(f => !f.endsWith('.json'));
  let renamed = 0;
  let notFound = 0;
  
  for (const imgFile of existingImages) {
    // Filename format: {channelId}_{msgId}_{attachmentId}.ext
    const parts = imgFile.split('_');
    if (parts.length < 3) continue;
    
    const channelId = parts[0];
    const msgId = parts[1];
    const ext = path.extname(imgFile);
    
    const meta = messageMap[msgId];
    if (!meta) {
      notFound++;
      continue;
    }
    
    // New filename: {server}_{date}_{ticker}_{direction}_{msgId}.ext
    const newFilename = `${meta.server}_${meta.dateStr}_${meta.ticker}_${meta.direction}_${msgId}${ext}`;
    
    const srcPath = path.join(IMAGES_DIR, imgFile);
    const dstPath = path.join(RENAMED_DIR, newFilename);
    
    try {
      fs.copyFileSync(srcPath, dstPath);
      renamed++;
    } catch (e) {
      console.error(`Error copying ${imgFile}: ${e.message}`);
    }
  }
  
  console.log(`Renamed ${renamed} images to ${RENAMED_DIR}`);
  console.log(`${notFound} images had no matching message (possibly from older data).`);
  
  // List sample of renamed files
  const renamedFiles = fs.readdirSync(RENAMED_DIR).slice(0, 20);
  console.log(`\nSample renamed files:`);
  renamedFiles.forEach(f => console.log(`  ${f}`));
}

run();
