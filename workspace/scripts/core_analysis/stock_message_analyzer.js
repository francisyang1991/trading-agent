/**
 * Stock Trading Message Analyzer
 * Reads messages from Discord/WhatsApp and extracts trading signals
 * 
 * Enhanced with:
 * - Signal generation with confidence scoring
 * - Trading memory integration
 * - Options recommendations
 */

const fs = require('fs');
const path = require('path');

// Import Discord service (optional - may not be installed in local test mode)
let DiscordReaderService = null;
try {
    DiscordReaderService = require('../discord_reader_service');
} catch (error) {
  // Discord.js not installed - running in local test mode
  if (error.code === 'MODULE_NOT_FOUND') {
    console.log('[Analyzer] Discord.js not installed - running in local test mode');
  } else {
    console.warn('[Analyzer] Discord service error:', error.message);
  }
}

// Import new trading system components
let SignalGenerator, getTradingMemory, OptionsCalculator;
try {
  ({ SignalGenerator } = require('../signal_generator'));
  ({ getTradingMemory } = require('../trading_memory'));
  ({ OptionsCalculator } = require('../options_calculator'));
} catch (error) {
  console.warn('[Analyzer] Trading system components not available:', error.message);
}

class StockMessageAnalyzer {
  constructor(discordToken) {
    // Initialize Discord service only if available and token provided
    this.discord = (discordToken && DiscordReaderService) 
      ? new DiscordReaderService(discordToken) 
      : null;
    this.config = this.loadConfig();
    this.summaries = [];
    this.analysisResults = [];
    
    // Initialize trading system components if available
    this.signalGenerator = SignalGenerator ? new SignalGenerator() : null;
    this.memory = getTradingMemory ? getTradingMemory() : null;
    this.optionsCalculator = OptionsCalculator ? new OptionsCalculator() : null;
  }

  loadConfig() {
    const configPaths = [
      path.join(process.env.HOME || '', '.openclaw/discord_servers.json'),
      path.join(__dirname, '../../config/discord_servers.json'),
      path.join(__dirname, '../../../config/discord_servers.json')
    ];

    for (const configPath of configPaths) {
      if (fs.existsSync(configPath)) {
        console.log(`[Analyzer] Loading config from: ${configPath}`);
        return JSON.parse(fs.readFileSync(configPath, 'utf8'));
      }
    }

    console.log('[Analyzer] No config file found, using defaults');
    return {
      servers: [],
      keywords: {
        bullish: ['buy', 'long', 'calls', 'bullish', 'moon', 'breakout', 'upgrade', 'oversold', 'dip', 'accumulate'],
        bearish: ['sell', 'short', 'puts', 'bearish', 'dump', 'breakdown', 'downgrade', 'overbought', 'crash', 'distribute'],
        tickers: ['$AAPL', '$TSLA', '$NVDA', '$SPY', '$QQQ', '$MSFT', '$AMZN', '$GOOGL', '$META', '$AMD']
      }
    };
  }

  /**
   * Extract ticker symbols from message text
   */
  extractTickers(text) {
    if (!text) return [];
    
    const tickers = new Set();
    
    // 1. Match $TICKER format (1-5 uppercase letters)
    const dollarTickers = text.match(/\$[A-Z]{1,5}\b/g) || [];
    dollarTickers.forEach(t => tickers.add(t));
    
    // 2. Match "filled on TICKER" or "on TICKER" patterns (common in trade alerts)
    const filledOnPattern = /(?:filled\s+on|sold|bought|on)\s+([A-Z]{1,5})\b/gi;
    let match;
    while ((match = filledOnPattern.exec(text)) !== null) {
      tickers.add('$' + match[1].toUpperCase());
    }
    
    // 3. Match "TICKER @" or "TICKER at" patterns
    const tickerAtPattern = /\b([A-Z]{2,5})\s+[@at]/gi;
    while ((match = tickerAtPattern.exec(text)) !== null) {
      tickers.add('$' + match[1].toUpperCase());
    }
    
    // 4. Common words to exclude
    const commonWords = new Set([
      'THE', 'AND', 'FOR', 'ARE', 'BUT', 'NOT', 'YOU', 'ALL', 'CAN', 'HAD', 
      'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'HAS', 'HIS', 'HOW', 'ITS', 'MAY', 
      'NEW', 'NOW', 'OLD', 'SEE', 'WAY', 'WHO', 'DID', 'GET', 'LET', 'PUT', 
      'SAY', 'SHE', 'TOO', 'USE', 'YES', 'BUY', 'SELL', 'HOLD', 'LONG', 'SHORT',
      'IS', 'IT', 'IN', 'ON', 'OF', 'TO', 'AT', 'BY', 'UP', 'GO', 'DO', 'BE', 'ME', 'MY', 'WE', 'US', 'AM', 'OR', 'IF', 'SO', 'AS', 'NO', 'HE', 'HI', 'LO', 'OK',
      'WITH', 'GOING', 'LOOKING', 'WATCHING', 'LIKE', 'HAVE', 'THIS', 'THAT', 'FROM', 'WILL', 'JUST', 'WHAT', 'BEEN', 'BACK', 'SOME', 'TIME', 'OVER', 'ONLY',
      'THINK', 'MORE', 'THEY', 'WANT', 'ALSO', 'THAN', 'VERY', 'THEN', 'WHEN', 'THEM', 'HERE', 'NEXT', 'WEEK', 'TODAY', 'GOOD', 'WELL', 'TAKE', 'MAKE', 'KNOW',
      'LOVE', 'HATE', 'LOOK', 'LOOKS', 'NICE', 'WAIT', 'DOWN', 'EYES', 'LATE', 'WIND', 'CARRY', 'SICK', 'CAUSE', 'THING', 'PMS', 'BEING', 'DAY', 'ATH', 'MACD', 'GAINS'
    ]);
    
    // 5. Match known tickers from config (case-insensitive)
    const knownTickers = this.config.keywords?.tickers || [];
    const potentialTickers = (text.match(/\b[A-Z]{1,5}\b/g) || [])
      .filter(t => !commonWords.has(t));
    
    potentialTickers.forEach(t => {
      if (knownTickers.includes(`$${t}`) || knownTickers.includes(t)) {
        tickers.add(`$${t}`);
      }
    });

    // Filter out common words that slipped through
    return Array.from(tickers).filter(t => !commonWords.has(t.replace('$', '')));
  }

  /**
   * Analyze sentiment of a message
   */
  analyzeSentiment(text) {
    if (!text) return { sentiment: 'neutral', confidence: 0, keywords: [] };
    
    const lower = text.toLowerCase();
    const bullish = this.config.keywords?.bullish || [];
    const bearish = this.config.keywords?.bearish || [];

    let bullScore = 0;
    let bearScore = 0;
    const matchedKeywords = [];

    bullish.forEach(word => {
      if (lower.includes(word.toLowerCase())) {
        bullScore++;
        matchedKeywords.push({ word, type: 'bullish' });
      }
    });

    bearish.forEach(word => {
      if (lower.includes(word.toLowerCase())) {
        bearScore++;
        matchedKeywords.push({ word, type: 'bearish' });
      }
    });

    if (bullScore > bearScore) {
      return { sentiment: 'bullish', confidence: bullScore, keywords: matchedKeywords };
    }
    if (bearScore > bullScore) {
      return { sentiment: 'bearish', confidence: bearScore, keywords: matchedKeywords };
    }
    return { sentiment: 'neutral', confidence: 0, keywords: matchedKeywords };
  }

  /**
   * Extract price targets from message
   */
  extractPriceTargets(text) {
    if (!text) return [];
    
    const targets = [];
    
    // Match patterns like "PT $150", "target 150", "price target: $150", "TP 150"
    const patterns = [
      /(?:PT|price target|target|TP)[:\s]*\$?(\d+(?:\.\d{1,2})?)/gi,
      /\$(\d+(?:\.\d{1,2})?)\s*(?:PT|target|TP)/gi,
      /targeting\s*\$?(\d+(?:\.\d{1,2})?)/gi
    ];

    patterns.forEach(pattern => {
      const matches = text.matchAll(pattern);
      for (const match of matches) {
        const price = parseFloat(match[1]);
        if (price > 0 && price < 100000) { // Reasonable price range
          targets.push(price);
        }
      }
    });

    return [...new Set(targets)]; // Remove duplicates
  }

  /**
   * Extract stop loss levels from message
   */
  extractStopLoss(text) {
    if (!text) return [];
    
    const stopLosses = [];
    
    // Match patterns like "SL $100", "stop loss at 100", "stop at $100"
    const patterns = [
      /(?:SL|stop loss|stop)[:\s]*(?:at\s*)?\$?(\d+(?:\.\d{1,2})?)/gi,
      /\$(\d+(?:\.\d{1,2})?)\s*(?:SL|stop)/gi
    ];

    patterns.forEach(pattern => {
      const matches = text.matchAll(pattern);
      for (const match of matches) {
        const price = parseFloat(match[1]);
        if (price > 0 && price < 100000) {
          stopLosses.push(price);
        }
      }
    });

    return [...new Set(stopLosses)];
  }

  /**
   * Analyze a single message
   */
  analyzeMessage(message) {
    const content = message.content || message.body || '';
    const tickers = this.extractTickers(content);
    const sentiment = this.analyzeSentiment(content);
    const priceTargets = this.extractPriceTargets(content);
    const stopLosses = this.extractStopLoss(content);

    // Only include if message has trading-relevant content
    if (tickers.length === 0 && sentiment.sentiment === 'neutral' && priceTargets.length === 0) {
      return null;
    }

    return {
      timestamp: message.timestamp || message.createdAt || new Date().toISOString(),
      author: message.author || message.authorName || 'unknown',
      channel: message.channelName || message.channel || 'unknown',
      guild: message.guildName || message.guild || 'unknown',
      tickers,
      sentiment: sentiment.sentiment,
      sentimentConfidence: sentiment.confidence,
      sentimentKeywords: sentiment.keywords,
      priceTargets,
      stopLosses,
      originalMessage: content.substring(0, 300),
      messageId: message.id
    };
  }

  /**
   * Analyze an array of messages (can be from any source)
   */
  analyzeMessages(messages) {
    const results = {
      timestamp: new Date().toISOString(),
      totalMessages: messages.length,
      relevantMessages: 0,
      byTicker: {},
      bySentiment: { bullish: [], bearish: [], neutral: [] },
      byAuthor: {},
      summary: ''
    };

    for (const msg of messages) {
      const analysis = this.analyzeMessage(msg);
      
      if (analysis) {
        results.relevantMessages++;
        
        // Group by ticker
        analysis.tickers.forEach(ticker => {
          if (!results.byTicker[ticker]) {
            results.byTicker[ticker] = [];
          }
          results.byTicker[ticker].push(analysis);
        });

        // Group by sentiment
        results.bySentiment[analysis.sentiment].push(analysis);
        
        // Group by author
        if (!results.byAuthor[analysis.author]) {
          results.byAuthor[analysis.author] = [];
        }
        results.byAuthor[analysis.author].push(analysis);
      }
    }

    results.summary = this.generateSummary(results);
    return results;
  }

  /**
   * Fetch and analyze all messages from configured Discord channels
   */
  async analyzeAllChannels() {
    if (!this.discord) {
      throw new Error('Discord service not initialized. Provide a token.');
    }

    await this.discord.connect();
    
    // Wait for client to be ready
    await new Promise(resolve => setTimeout(resolve, 3000));

    const results = {
      timestamp: new Date().toISOString(),
      totalMessages: 0,
      relevantMessages: 0,
      byTicker: {},
      bySentiment: { bullish: [], bearish: [], neutral: [] },
      byServer: {},
      summary: ''
    };

    for (const server of this.config.servers) {
      console.log(`\n[Analyzer] Processing server: ${server.name}`);
      results.byServer[server.name] = { messages: 0, relevant: 0 };
      
      for (const channelId of server.channels) {
        try {
          console.log(`  [Analyzer] Fetching messages from channel: ${channelId}`);
          const messages = await this.discord.fetchAllMessages(channelId, 500);
          results.totalMessages += messages.length;
          results.byServer[server.name].messages += messages.length;

          for (const msg of messages) {
            const analysis = this.analyzeMessage({
              content: msg.content,
              timestamp: msg.createdAt,
              author: msg.author?.username,
              channelName: msg.channel?.name,
              guildName: server.name,
              id: msg.id
            });

            if (analysis) {
              results.relevantMessages++;
              results.byServer[server.name].relevant++;
              
              // Group by ticker
              analysis.tickers.forEach(ticker => {
                if (!results.byTicker[ticker]) {
                  results.byTicker[ticker] = [];
                }
                results.byTicker[ticker].push(analysis);
              });

              // Group by sentiment
              results.bySentiment[analysis.sentiment].push(analysis);
            }
          }
        } catch (error) {
          console.error(`  [Analyzer] Error fetching channel ${channelId}:`, error.message);
        }
      }
    }

    results.summary = this.generateSummary(results);
    await this.discord.disconnect();
    
    return results;
  }

  /**
   * Generate human-readable summary
   */
  generateSummary(results) {
    const lines = [
      `═══════════════════════════════════════════════════════════`,
      `           STOCK TRADING INTELLIGENCE SUMMARY              `,
      `═══════════════════════════════════════════════════════════`,
      `Generated: ${results.timestamp}`,
      `Total messages analyzed: ${results.totalMessages}`,
      `Trading-relevant messages: ${results.relevantMessages}`,
      `Relevance rate: ${((results.relevantMessages / results.totalMessages) * 100 || 0).toFixed(1)}%`,
      ``,
      `═══════════════════════════════════════════════════════════`,
      `                    SENTIMENT OVERVIEW                     `,
      `═══════════════════════════════════════════════════════════`,
      `🟢 Bullish signals: ${results.bySentiment.bullish.length}`,
      `🔴 Bearish signals: ${results.bySentiment.bearish.length}`,
      `⚪ Neutral mentions: ${results.bySentiment.neutral.length}`,
      ``
    ];

    // Sentiment ratio
    const bullish = results.bySentiment.bullish.length;
    const bearish = results.bySentiment.bearish.length;
    if (bullish + bearish > 0) {
      const ratio = ((bullish / (bullish + bearish)) * 100).toFixed(1);
      const meter = ratio > 60 ? '📈 BULLISH' : ratio < 40 ? '📉 BEARISH' : '📊 MIXED';
      lines.push(`Overall sentiment: ${meter} (${ratio}% bullish)`);
      lines.push('');
    }

    // Top mentioned tickers
    const tickerCounts = Object.entries(results.byTicker)
      .map(([ticker, mentions]) => ({ 
        ticker, 
        count: mentions.length,
        bullish: mentions.filter(s => s.sentiment === 'bullish').length,
        bearish: mentions.filter(s => s.sentiment === 'bearish').length
      }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10);

    if (tickerCounts.length > 0) {
      lines.push(`═══════════════════════════════════════════════════════════`);
      lines.push(`                   TOP MENTIONED TICKERS                   `);
      lines.push(`═══════════════════════════════════════════════════════════`);
      tickerCounts.forEach((t, i) => {
        const sentimentIcon = t.bullish > t.bearish ? '🟢' : t.bearish > t.bullish ? '🔴' : '⚪';
        lines.push(`${i + 1}. ${sentimentIcon} ${t.ticker}: ${t.count} mentions (${t.bullish}↑ ${t.bearish}↓)`);
      });
      lines.push('');
    }

    // High confidence bullish signals
    const recentBullish = results.bySentiment.bullish
      .filter(s => s.sentimentConfidence >= 2)
      .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
      .slice(0, 5);
    
    if (recentBullish.length > 0) {
      lines.push(`═══════════════════════════════════════════════════════════`);
      lines.push(`              HIGH CONFIDENCE BULLISH SIGNALS              `);
      lines.push(`═══════════════════════════════════════════════════════════`);
      recentBullish.forEach(s => {
        const tickers = s.tickers.join(', ') || 'General';
        const pt = s.priceTargets.length > 0 ? ` | PT: $${s.priceTargets.join(', $')}` : '';
        lines.push(`• ${tickers} (${s.channel})${pt}`);
        lines.push(`  "${s.originalMessage.substring(0, 80)}..."`);
        lines.push(`  by ${s.author} at ${new Date(s.timestamp).toLocaleString()}`);
        lines.push('');
      });
    }

    // High confidence bearish signals
    const recentBearish = results.bySentiment.bearish
      .filter(s => s.sentimentConfidence >= 2)
      .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp))
      .slice(0, 5);
    
    if (recentBearish.length > 0) {
      lines.push(`═══════════════════════════════════════════════════════════`);
      lines.push(`              HIGH CONFIDENCE BEARISH SIGNALS              `);
      lines.push(`═══════════════════════════════════════════════════════════`);
      recentBearish.forEach(s => {
        const tickers = s.tickers.join(', ') || 'General';
        const sl = s.stopLosses.length > 0 ? ` | SL: $${s.stopLosses.join(', $')}` : '';
        lines.push(`• ${tickers} (${s.channel})${sl}`);
        lines.push(`  "${s.originalMessage.substring(0, 80)}..."`);
        lines.push(`  by ${s.author} at ${new Date(s.timestamp).toLocaleString()}`);
        lines.push('');
      });
    }

    // Price targets summary
    const allTargets = [];
    Object.entries(results.byTicker).forEach(([ticker, mentions]) => {
      mentions.forEach(m => {
        if (m.priceTargets.length > 0) {
          allTargets.push({ ticker, targets: m.priceTargets, sentiment: m.sentiment });
        }
      });
    });

    if (allTargets.length > 0) {
      lines.push(`═══════════════════════════════════════════════════════════`);
      lines.push(`                    PRICE TARGETS FOUND                    `);
      lines.push(`═══════════════════════════════════════════════════════════`);
      allTargets.slice(0, 10).forEach(t => {
        const icon = t.sentiment === 'bullish' ? '🎯' : t.sentiment === 'bearish' ? '⚠️' : '📍';
        lines.push(`${icon} ${t.ticker}: $${t.targets.join(', $')}`);
      });
      lines.push('');
    }

    lines.push(`═══════════════════════════════════════════════════════════`);
    lines.push(`                      END OF REPORT                        `);
    lines.push(`═══════════════════════════════════════════════════════════`);

    return lines.join('\n');
  }

  /**
   * Save analysis results to file
   */
  async saveResults(results, outputDir = null) {
    const dir = outputDir || path.join(__dirname, '../../data');
    
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }

    const timestamp = Date.now();
    const jsonPath = path.join(dir, `stock_analysis_${timestamp}.json`);
    const summaryPath = path.join(dir, `stock_analysis_${timestamp}_summary.txt`);

    const output = {
      ...results,
      generatedAt: new Date().toISOString()
    };

    fs.writeFileSync(jsonPath, JSON.stringify(output, null, 2));
    console.log(`\n[Analyzer] Results saved to: ${jsonPath}`);

    fs.writeFileSync(summaryPath, results.summary);
    console.log(`[Analyzer] Summary saved to: ${summaryPath}`);

    return { jsonPath, summaryPath };
  }

  /**
   * Get a quick ticker report
   */
  getTickerReport(ticker, results) {
    const mentions = results.byTicker[ticker] || results.byTicker[`$${ticker}`] || [];
    
    if (mentions.length === 0) {
      return `No mentions found for ${ticker}`;
    }

    const bullish = mentions.filter(m => m.sentiment === 'bullish').length;
    const bearish = mentions.filter(m => m.sentiment === 'bearish').length;
    const targets = mentions.flatMap(m => m.priceTargets);
    const stops = mentions.flatMap(m => m.stopLosses);

    return {
      ticker,
      totalMentions: mentions.length,
      bullish,
      bearish,
      sentimentRatio: ((bullish / (bullish + bearish)) * 100 || 0).toFixed(1),
      priceTargets: [...new Set(targets)].sort((a, b) => a - b),
      stopLosses: [...new Set(stops)].sort((a, b) => a - b),
      recentMessages: mentions.slice(0, 5).map(m => ({
        text: m.originalMessage,
        author: m.author,
        sentiment: m.sentiment,
        timestamp: m.timestamp
      }))
    };
  }

  /**
   * Generate trading signals from analysis results
   * Uses the enhanced SignalGenerator for confidence scoring and position sizing
   */
  generateTradingSignals(analysisResults, options = {}) {
    if (!this.signalGenerator) {
      console.warn('[Analyzer] SignalGenerator not available');
      return [];
    }

    const signalInputs = [];
    
    for (const [ticker, mentions] of Object.entries(analysisResults.byTicker)) {
      if (mentions.length === 0) continue;
      
      // Aggregate sentiment
      const bullishCount = mentions.filter(m => m.sentiment === 'bullish').length;
      const bearishCount = mentions.filter(m => m.sentiment === 'bearish').length;
      
      // Determine overall sentiment
      let sentiment = 'neutral';
      let sentimentScore = 0;
      
      if (bullishCount > bearishCount) {
        sentiment = 'bullish';
        sentimentScore = bullishCount - bearishCount;
      } else if (bearishCount > bullishCount) {
        sentiment = 'bearish';
        sentimentScore = bearishCount - bullishCount;
      }
      
      // Skip neutral signals
      if (sentiment === 'neutral') continue;
      
      // Collect price targets and stop losses
      const priceTargets = mentions.flatMap(m => m.priceTargets || []);
      const stopLosses = mentions.flatMap(m => m.stopLosses || []);
      
      // Get unique authors
      const authors = [...new Set(mentions.map(m => m.author))];
      const primaryAuthor = authors[0];
      
      // Create signal input
      signalInputs.push({
        ticker: ticker.replace('$', ''),
        sentiment,
        sentimentScore,
        priceTargets: [...new Set(priceTargets)].sort((a, b) => a - b),
        stopLoss: stopLosses.length > 0 ? Math.min(...stopLosses) : null,
        author: primaryAuthor,
        mentions: mentions.length,
        sourceMessages: mentions.map(m => ({
          id: m.messageId,
          content: m.originalMessage,
          author: m.author,
          timestamp: m.timestamp,
          channel: m.channel
        })),
        currentPrice: options.prices?.[ticker.replace('$', '')] || null
      });
    }

    // Generate signals using SignalGenerator
    const signals = this.signalGenerator.generateSignals(signalInputs);
    
    console.log(`[Analyzer] Generated ${signals.length} trading signal(s) from ${Object.keys(analysisResults.byTicker).length} tickers`);
    
    return signals;
  }

  /**
   * Get options recommendations for a signal
   */
  getOptionsRecommendation(signal) {
    if (!this.optionsCalculator) {
      console.warn('[Analyzer] OptionsCalculator not available');
      return null;
    }

    return this.optionsCalculator.recommend(
      signal.ticker,
      signal.currentPrice || signal.entryHigh,
      signal.action,
      signal.confidenceScore,
      {
        stopLoss: signal.stopLoss,
        target1: signal.target1,
        target2: signal.target2
      }
    );
  }

  /**
   * Record analysis to trading memory
   */
  recordToMemory(analysisResults) {
    if (!this.memory) {
      console.warn('[Analyzer] TradingMemory not available');
      return;
    }

    // Record patterns detected
    const volumeSpikes = Object.entries(analysisResults.byTicker)
      .filter(([_, mentions]) => mentions.length >= 5)
      .map(([ticker]) => ticker);
    
    if (volumeSpikes.length > 0) {
      this.memory.recordPattern('volumeSpike', {
        tickers: volumeSpikes,
        date: new Date().toISOString(),
        mentionCounts: volumeSpikes.map(t => analysisResults.byTicker[t].length)
      });
    }

    console.log(`[Analyzer] Recorded analysis to memory`);
  }

  /**
   * Get author reliability from memory
   */
  getAuthorReliability(authorId) {
    if (!this.memory) return 50; // Default
    return this.memory.getAuthorReliability(authorId);
  }

  /**
   * Get ticker historical accuracy from memory
   */
  getTickerAccuracy(ticker) {
    if (!this.memory) return null;
    return this.memory.getTickerAccuracy(ticker.replace('$', ''));
  }

  /**
   * Analyze and generate signals in one call
   */
  analyzeAndGenerateSignals(messages, options = {}) {
    // First analyze messages
    const analysisResults = this.analyzeMessages(messages);
    
    // Record to memory
    if (options.recordToMemory !== false) {
      this.recordToMemory(analysisResults);
    }
    
    // Generate signals
    const signals = this.generateTradingSignals(analysisResults, options);
    
    // Add options recommendations if requested
    if (options.includeOptions && this.optionsCalculator) {
      for (const signal of signals) {
        signal.optionsRecommendation = this.getOptionsRecommendation(signal);
      }
    }
    
    return {
      analysis: analysisResults,
      signals,
      generatedAt: new Date().toISOString()
    };
  }

  /**
   * Format signals for display
   */
  formatSignals(signals) {
    if (!signals || signals.length === 0) {
      return 'No signals generated.';
    }

    if (!this.signalGenerator) {
      return `Generated ${signals.length} signal(s) - SignalGenerator not available for formatting`;
    }

    return this.signalGenerator.formatSignalsSummary(signals);
  }
}

// CLI usage
if (require.main === module) {
  const token = process.env.DISCORD_BOT_TOKEN;
  
  if (!token) {
    console.log('[Analyzer] No DISCORD_BOT_TOKEN found.');
    console.log('[Analyzer] Running in manual mode. You can analyze messages programmatically.');
    
    // Demo with sample messages
    const analyzer = new StockMessageAnalyzer();
    const sampleMessages = [
      { content: '$AAPL looking bullish, breakout incoming! PT $200', author: 'trader1', channelName: 'alerts' },
      { content: 'Selling my $TSLA calls, this is overbought. SL at $240', author: 'trader2', channelName: 'general' },
      { content: '$NVDA to the moon! 🚀 Long calls at $500', author: 'trader3', channelName: 'alerts' },
      { content: 'Market looking bearish, puts on $SPY', author: 'trader1', channelName: 'general' }
    ];
    
    const results = analyzer.analyzeMessages(sampleMessages);
    console.log('\n' + results.summary);
    
    process.exit(0);
  }

  const analyzer = new StockMessageAnalyzer(token);
  
  analyzer.analyzeAllChannels()
    .then(async results => {
      console.log('\n' + results.summary);
      await analyzer.saveResults(results);
    })
    .catch(error => {
      console.error('[Analyzer] Analysis failed:', error);
      process.exit(1);
    });
}

module.exports = StockMessageAnalyzer;
