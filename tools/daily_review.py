#!/usr/bin/env python3
"""
Daily Review & Learning System
==============================
Generates daily lessons learned from trading activity and scanner performance.
Proposes scanner improvements based on historical data.

Run after market close to:
1. Review today's signals and outcomes
2. Analyze scanner performance
3. Generate lessons learned
4. Propose scanner parameter changes
5. Track scanner versions

Usage:
    # Generate daily review
    python tools/daily_review.py
    
    # Full analysis with improvement proposals
    python tools/daily_review.py --full
    
    # Apply a scanner upgrade
    python tools/daily_review.py --upgrade "Increase R:R threshold to 2.0x"
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import argparse

from src.portfolio import get_tracker
from src.journal import get_signal_tracker

# Paths
DATA_DIR = Path(__file__).parent.parent / "data"
LESSONS_DIR = DATA_DIR / "lessons"
DOCS_DIR = Path(__file__).parent.parent / "docs"
CHANGELOG_FILE = DOCS_DIR / "SCANNER_CHANGELOG.md"
LESSONS_FILE = DOCS_DIR / "LESSONS_LEARNED.md"


def get_scanner_version() -> str:
    """Get current scanner version from changelog."""
    if not CHANGELOG_FILE.exists():
        return "v1.0.0"
    
    with open(CHANGELOG_FILE, 'r') as f:
        content = f.read()
    
    import re
    match = re.search(r'## (v\d+\.\d+\.\d+)', content)
    return match.group(1) if match else "v1.0.0"


def increment_version(version: str, level: str = "patch") -> str:
    """Increment version number."""
    parts = version.lstrip('v').split('.')
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    
    if level == "major":
        return f"v{major + 1}.0.0"
    elif level == "minor":
        return f"v{major}.{minor + 1}.0"
    else:
        return f"v{major}.{minor}.{patch + 1}"


def generate_daily_review() -> Dict:
    """Generate comprehensive daily review."""
    review = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "timestamp": datetime.now().isoformat(),
        "scanner_version": get_scanner_version()
    }
    
    # Portfolio status
    tracker = get_tracker()
    portfolio = tracker.get_portfolio_summary()
    
    review["portfolio"] = {
        "position_count": portfolio["position_count"],
        "total_value": portfolio["total_value"],
        "total_pnl": portfolio["total_pnl"],
        "total_pnl_pct": portfolio["total_pnl_pct"],
        "positions": [
            {
                "symbol": p["symbol"],
                "pnl_pct": p["pnl_pct"],
                "value": p["value"]
            }
            for p in portfolio["positions"]
        ]
    }
    
    # Signal performance
    signal_tracker = get_signal_tracker()
    
    # Auto-check pending signals
    updated = signal_tracker.auto_check_outcomes()
    review["signals_updated"] = len(updated)
    
    # Get stats
    stats_30d = signal_tracker.get_performance_stats(days_back=30)
    stats_90d = signal_tracker.get_performance_stats(days_back=90)
    
    review["performance_30d"] = stats_30d
    review["performance_90d"] = stats_90d
    
    # Generate insights
    insights = signal_tracker.generate_improvement_insights(stats_30d)
    review["insights"] = insights
    
    # Pending signals
    pending = signal_tracker.get_pending_signals()
    review["pending_signals"] = len(pending)
    
    return review


def format_review_report(review: Dict) -> str:
    """Format review as human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"  DAILY REVIEW - {review['date']}")
    lines.append(f"  Scanner Version: {review['scanner_version']}")
    lines.append("=" * 70)
    
    # Portfolio
    p = review["portfolio"]
    lines.append("")
    lines.append("PORTFOLIO STATUS")
    lines.append("-" * 40)
    lines.append(f"  Positions: {p['position_count']}")
    lines.append(f"  Total Value: ${p['total_value']:,.2f}")
    lines.append(f"  Total P&L: ${p['total_pnl']:+,.2f} ({p['total_pnl_pct']:+.1f}%)")
    
    if p["positions"]:
        lines.append("")
        for pos in p["positions"]:
            icon = "🟢" if pos["pnl_pct"] >= 0 else "🔴"
            lines.append(f"  {icon} {pos['symbol']}: {pos['pnl_pct']:+.1f}%")
    
    # Signal Performance
    stats = review.get("performance_30d", {})
    if stats.get("total_signals"):
        lines.append("")
        lines.append("SIGNAL PERFORMANCE (30 Days)")
        lines.append("-" * 40)
        lines.append(f"  Total Signals: {stats['total_signals']}")
        lines.append(f"  Win Rate: {stats['win_rate']:.1f}%")
        lines.append(f"  Avg P&L: {stats['avg_pnl']:+.2f}%")
        lines.append(f"  Expected Value: {stats['expected_value']:+.2f}%")
        
        if stats.get("by_ev_bucket"):
            lines.append("")
            lines.append("  By EV Bucket:")
            for bucket, data in stats["by_ev_bucket"].items():
                lines.append(f"    {bucket}: {data['count']} signals, {data['win_rate']:.0f}% WR")
    
    # Insights
    if review.get("insights"):
        lines.append("")
        lines.append("INSIGHTS & RECOMMENDATIONS")
        lines.append("-" * 40)
        for insight in review["insights"]:
            lines.append(f"  • {insight}")
    
    # Pending
    if review.get("signals_updated", 0) > 0:
        lines.append("")
        lines.append(f"  ✅ Auto-resolved {review['signals_updated']} signal outcomes")
    
    lines.append(f"  ⏳ {review.get('pending_signals', 0)} signals pending resolution")
    
    lines.append("")
    lines.append("=" * 70)
    
    return "\n".join(lines)


def save_daily_lesson(review: Dict):
    """Save daily lesson to lessons directory."""
    LESSONS_DIR.mkdir(parents=True, exist_ok=True)
    
    filename = f"lesson_{review['date']}.json"
    filepath = LESSONS_DIR / filename
    
    with open(filepath, 'w') as f:
        json.dump(review, f, indent=2, default=str)
    
    print(f"  Lesson saved to: {filepath}")


def update_lessons_learned(review: Dict):
    """Append insights to accumulated lessons file."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    
    if not LESSONS_FILE.exists():
        content = "# Lessons Learned\n\nAccumulated trading insights from daily reviews.\n\n"
    else:
        with open(LESSONS_FILE, 'r') as f:
            content = f.read()
    
    # Add today's insights
    if review.get("insights"):
        content += f"\n## {review['date']}\n\n"
        content += f"Scanner: {review['scanner_version']}\n\n"
        for insight in review["insights"]:
            content += f"- {insight}\n"
        content += "\n"
    
    with open(LESSONS_FILE, 'w') as f:
        f.write(content)


def propose_scanner_changes(review: Dict) -> List[Dict]:
    """
    Analyze performance and propose scanner parameter changes.
    
    Returns list of proposed changes with reasoning.
    """
    proposals = []
    stats = review.get("performance_30d", {})
    
    if not stats.get("total_signals"):
        return proposals
    
    wr = stats.get("win_rate", 50)
    ev = stats.get("expected_value", 0)
    by_ev = stats.get("by_ev_bucket", {})
    
    # Proposal 1: Adjust EV threshold
    high_ev = by_ev.get("2%+", {})
    mid_ev = by_ev.get("1-2%", {})
    low_ev = by_ev.get("0-1%", {})
    
    if high_ev.get("win_rate", 0) > wr + 15 and high_ev.get("count", 0) >= 5:
        proposals.append({
            "type": "PARAMETER",
            "change": "Increase minimum EV threshold from 0.5% to 1.5%",
            "reasoning": f"High EV signals (2%+) have {high_ev['win_rate']:.0f}% WR vs {wr:.0f}% overall",
            "impact": "Fewer signals but higher quality",
            "level": "minor"
        })
    
    # Proposal 2: Adjust R:R threshold
    if wr < 50 and stats.get("avg_loss", 0) > stats.get("avg_win", 0):
        proposals.append({
            "type": "PARAMETER",
            "change": "Increase R:R threshold from 1.5x to 2.0x",
            "reasoning": f"Avg loss ({stats['avg_loss']:.2f}%) exceeds avg win ({stats['avg_win']:.2f}%)",
            "impact": "Better risk/reward per trade",
            "level": "minor"
        })
    
    # Proposal 3: Regime filtering
    by_regime = stats.get("by_regime", {})
    if by_regime:
        worst = min(by_regime.items(), key=lambda x: x[1]["win_rate"])
        if worst[1]["win_rate"] < 40 and worst[1]["count"] >= 5:
            proposals.append({
                "type": "FILTER",
                "change": f"Add warning/filter for {worst[0]} regime signals",
                "reasoning": f"{worst[0]} regime has only {worst[1]['win_rate']:.0f}% win rate",
                "impact": "Avoid low-probability setups",
                "level": "minor"
            })
    
    return proposals


def record_scanner_upgrade(
    version: str,
    changes: List[str],
    reasoning: str,
    expected_impact: str
):
    """Record a scanner upgrade in the changelog."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    
    date = datetime.now().strftime("%Y-%m-%d")
    
    entry = f"""
## {version} ({date})

### Changes
"""
    for change in changes:
        entry += f"- {change}\n"
    
    entry += f"""
### Reasoning
{reasoning}

### Expected Impact
{expected_impact}

---
"""
    
    if CHANGELOG_FILE.exists():
        with open(CHANGELOG_FILE, 'r') as f:
            existing = f.read()
        # Insert after header
        if "# Scanner Changelog" in existing:
            parts = existing.split("\n---\n", 1)
            content = parts[0] + "\n---\n" + entry
            if len(parts) > 1:
                content += parts[1]
        else:
            content = existing + entry
    else:
        content = """# Scanner Changelog

Version history and rationale for scanner parameter changes.
Each change is data-driven based on signal performance analysis.

---
""" + entry
    
    with open(CHANGELOG_FILE, 'w') as f:
        f.write(content)
    
    print(f"  ✅ Recorded scanner upgrade to {version}")


def main():
    parser = argparse.ArgumentParser(description="Daily Review & Learning System")
    parser.add_argument("--full", action="store_true", help="Full analysis with proposals")
    parser.add_argument("--upgrade", type=str, help="Record a scanner upgrade")
    parser.add_argument("--version-level", type=str, default="patch",
                       choices=["major", "minor", "patch"],
                       help="Version increment level")
    
    args = parser.parse_args()
    
    print("\n🔍 SAIYAN Daily Review System")
    print("=" * 50)
    
    if args.upgrade:
        # Record upgrade
        current = get_scanner_version()
        new_version = increment_version(current, args.version_level)
        record_scanner_upgrade(
            version=new_version,
            changes=[args.upgrade],
            reasoning="Manual upgrade",
            expected_impact="TBD - monitor next 30 days"
        )
        return
    
    # Generate review
    print("\n📊 Generating daily review...")
    review = generate_daily_review()
    
    # Print report
    report = format_review_report(review)
    print(report)
    
    # Save lesson
    save_daily_lesson(review)
    update_lessons_learned(review)
    
    # Full analysis with proposals
    if args.full:
        print("\n💡 IMPROVEMENT PROPOSALS")
        print("-" * 50)
        
        proposals = propose_scanner_changes(review)
        
        if proposals:
            for i, p in enumerate(proposals, 1):
                print(f"\n{i}. {p['change']}")
                print(f"   Reasoning: {p['reasoning']}")
                print(f"   Impact: {p['impact']}")
                print(f"   Version level: {p['level']}")
        else:
            print("  No changes proposed - scanner performing within expectations")
    
    print("\n✅ Daily review complete!")


if __name__ == "__main__":
    main()
