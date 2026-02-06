"""
Tests for workspace/scripts/core_analysis/llm_analyzer.py

Tier 2 - Integration Tests (Pure logic functions, no LLM calls)

Tests:
- _parse_llm_response: JSON extraction from LLM output
- _local_analysis: Sentiment analysis from message text
- _local_analysis_simple: Fallback analysis
"""

import pytest
import sys
import os
import importlib.util

# Load llm_analyzer directly from file path to avoid sys.modules conflicts
_LLM_ANALYZER_PATH = os.path.join(
    os.path.dirname(__file__),
    '..', 'workspace', 'scripts', 'core_analysis', 'llm_analyzer.py'
)

def _load_llm_analyzer():
    """Load llm_analyzer module directly from file path."""
    spec = importlib.util.spec_from_file_location("_llm_analyzer_real", _LLM_ANALYZER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_mod = _load_llm_analyzer()
_parse_llm_response = _mod._parse_llm_response
_local_analysis = _mod._local_analysis
_local_analysis_simple = _mod._local_analysis_simple


# =============================================================================
# _parse_llm_response
# =============================================================================

class TestParseLLMResponse:
    """Tests for JSON extraction from LLM response text."""

    @pytest.mark.integration
    def test_clean_json(self):
        """Clean JSON string -> parsed dict."""
        content = '{"sentiment": "BULLISH", "confidence": "HIGH"}'
        result = _parse_llm_response(content)
        assert result["sentiment"] == "BULLISH"

    @pytest.mark.integration
    def test_json_with_preamble(self):
        """JSON embedded in text -> extracted correctly."""
        content = 'Here is my analysis:\n{"sentiment": "BEARISH", "thesis": "downtrend"}\nEnd.'
        result = _parse_llm_response(content)
        assert result["sentiment"] == "BEARISH"

    @pytest.mark.integration
    def test_no_json(self):
        """No JSON at all -> raw_response fallback."""
        content = "This is just plain text without any JSON."
        result = _parse_llm_response(content)
        assert "raw_response" in result
        assert result["raw_response"] == content

    @pytest.mark.integration
    def test_malformed_json(self):
        """Malformed JSON -> raw_response fallback."""
        content = '{"sentiment": "BULLISH", broken'
        result = _parse_llm_response(content)
        assert "raw_response" in result

    @pytest.mark.integration
    def test_empty_string(self):
        """Empty string -> raw_response."""
        result = _parse_llm_response("")
        assert "raw_response" in result

    @pytest.mark.integration
    def test_nested_json(self):
        """Nested JSON structures are parsed."""
        content = '{"analysis": {"sentiment": "BULLISH"}, "targets": [100, 110]}'
        result = _parse_llm_response(content)
        assert "analysis" in result
        assert result["analysis"]["sentiment"] == "BULLISH"


# =============================================================================
# _local_analysis
# =============================================================================

class TestLocalAnalysis:
    """Tests for sentiment analysis from Discord messages."""

    @pytest.mark.integration
    def test_bullish_sentiment(self):
        """Messages with bullish keywords -> BULLISH sentiment."""
        messages = [
            {"content": "I'm going long on $NVDA, very bullish, expecting breakout above support with calls"},
            {"content": "Buy the dip, accumulate more shares, super bullish"},
            {"content": "Bounce play looking strong, breakout imminent, long calls"},
        ]
        result = _local_analysis("NVDA", messages, {})
        assert result["sentiment"] == "BULLISH"

    @pytest.mark.integration
    def test_bearish_sentiment(self):
        """Messages with bearish keywords -> BEARISH sentiment."""
        messages = [
            {"content": "Selling all shares, too much risk, very bearish outlook"},
            {"content": "I'm shorting this, puts are the play, breakdown ahead"},
            {"content": "Exit everything, overvalued, crash incoming, closing positions"},
        ]
        result = _local_analysis("MSTR", messages, {})
        assert result["sentiment"] == "BEARISH"

    @pytest.mark.integration
    def test_mixed_sentiment(self):
        """Balanced messages -> MIXED sentiment."""
        messages = [
            {"content": "Could go either way from here"},
            {"content": "Watching for now"},
        ]
        result = _local_analysis("AAPL", messages, {})
        assert result["sentiment"] == "MIXED"

    @pytest.mark.integration
    def test_extracts_price_targets(self):
        """Dollar amounts are extracted as price targets."""
        messages = [
            {"content": "I think $NVDA can hit $150 by next month, support at $120"},
        ]
        result = _local_analysis("NVDA", messages, {})
        targets = result["price_levels"]["targets"]
        assert 150.0 in targets or 120.0 in targets

    @pytest.mark.integration
    def test_includes_ticker_in_thesis(self):
        """Thesis text includes the ticker symbol."""
        messages = [{"content": "buying calls"}]
        result = _local_analysis("TSLA", messages, {})
        assert "TSLA" in result["thesis"]

    @pytest.mark.integration
    def test_message_count_in_thesis(self):
        """Thesis mentions number of messages."""
        messages = [{"content": "long"}, {"content": "buy"}, {"content": "calls"}]
        result = _local_analysis("AAPL", messages, {})
        assert "3" in result["thesis"]

    @pytest.mark.integration
    def test_model_field(self):
        """Result should indicate local_analysis model."""
        result = _local_analysis("NVDA", [{"content": "test"}], {})
        assert result["model"] == "local_analysis"

    @pytest.mark.integration
    def test_empty_messages(self):
        """Empty message list -> MIXED sentiment."""
        result = _local_analysis("NVDA", [], {})
        assert result["sentiment"] == "MIXED"

    @pytest.mark.integration
    def test_stock_context_passed_through(self):
        """Stock context dict is included in result."""
        ctx = {"price": 500.0, "volume": 1000000}
        result = _local_analysis("NVDA", [{"content": "test"}], ctx)
        assert result["stock_context"] == ctx


# =============================================================================
# _local_analysis_simple
# =============================================================================

class TestLocalAnalysisSimple:
    """Tests for the simple fallback analysis."""

    @pytest.mark.integration
    def test_fallback_returns_unknown(self):
        result = _local_analysis_simple("any prompt", {})
        assert result["sentiment"] == "UNKNOWN"

    @pytest.mark.integration
    def test_fallback_model_field(self):
        result = _local_analysis_simple("any prompt", {})
        assert result["model"] == "fallback"

    @pytest.mark.integration
    def test_fallback_includes_context(self):
        ctx = {"price": 100}
        result = _local_analysis_simple("any prompt", ctx)
        assert result["stock_context"] == ctx
