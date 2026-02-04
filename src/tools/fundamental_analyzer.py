#!/usr/bin/env python3
"""
ADVANCED FUNDAMENTAL ANALYZER (src implementation)
==================================================
Reusable CLI implementation that `tools/fundamental_analyzer.py` can delegate to.
"""

import argparse
from datetime import datetime
from typing import List, Optional
import warnings

warnings.filterwarnings("ignore")

from ..universe.fundamentals import (
    FundamentalAnalyzer,
    FundamentalMetrics,
    print_fundamental_report,
)


def print_comparison_table(metrics_list: List[FundamentalMetrics], file=None):
    """Print comparison table of multiple stocks."""
    if not metrics_list:
        print("No stocks to compare", file=file)
        return

    print(f"\n{'='*130}", file=file)
    print("  FUNDAMENTAL COMPARISON", file=file)
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", file=file)
    print(f"{'='*130}", file=file)

    print(
        f"\n{'Symbol':<8} {'Grade':>6} {'Score':>6} {'Quality':>8} {'Value':>6} {'Growth':>7} {'Health':>7} "
        f"{'F-Score':>8} {'ROIC':>8} {'FCF%':>8} {'P/E':>8} {'D/E':>6}",
        file=file,
    )
    print("-" * 130, file=file)

    metrics_list = sorted(metrics_list, key=lambda x: x.overall_fundamental_score, reverse=True)

    for m in metrics_list:
        roic = f"{m.roic:.1%}" if m.roic else "N/A"
        fcf = f"{m.fcf_yield:.1%}" if m.fcf_yield else "N/A"
        pe = f"{m.pe_ratio:.1f}" if m.pe_ratio else "N/A"
        de = f"{m.debt_to_equity:.2f}" if m.debt_to_equity is not None else "N/A"

        print(
            f"{m.symbol:<8} {m.fundamental_grade:>6} {m.overall_fundamental_score:>5.0f} "
            f"{m.quality_score:>7.0f} {m.value_score:>6.0f} {m.growth_score:>6.0f} "
            f"{m.financial_health_score:>6.0f} {m.piotroski_f_score:>7}/9 "
            f"{roic:>8} {fcf:>8} {pe:>8} {de:>6}",
            file=file,
        )

    print("\n  SCORING GUIDE:", file=file)
    print("    Grade A = 80+, B = 70-79, C = 60-69, D = 50-59, F = <50", file=file)
    print("    ROIC: >15% excellent, >10% good | FCF Yield: >5% good", file=file)
    print("    Piotroski: 7-9 strong, 4-6 average, 0-3 weak", file=file)


def print_detailed_comparison(metrics_list: List[FundamentalMetrics], file=None):
    """Print detailed comparison with strengths/weaknesses."""
    print(f"\n{'='*100}", file=file)
    print("  DETAILED ANALYSIS", file=file)
    print(f"{'='*100}", file=file)

    for m in sorted(metrics_list, key=lambda x: x.overall_fundamental_score, reverse=True):
        print(f"\n{'─'*80}", file=file)
        print(f"  {m.symbol} - {m.name}", file=file)
        print(f"  Grade: {m.fundamental_grade} | Score: {m.overall_fundamental_score:.0f}", file=file)
        print(f"{'─'*80}", file=file)

        print("\n  Scores:", file=file)
        print(
            f"    Quality: {m.quality_score:.0f} | Value: {m.value_score:.0f} | "
            f"Growth: {m.growth_score:.0f} | Health: {m.financial_health_score:.0f}",
            file=file,
        )

        print("\n  Key Metrics:", file=file)
        roic = f"{m.roic:.1%}" if m.roic else "N/A"
        roe = f"{m.roe:.1%}" if m.roe else "N/A"
        fcf_y = f"{m.fcf_yield:.1%}" if m.fcf_yield else "N/A"
        margin = f"{m.profit_margin:.1%}" if m.profit_margin else "N/A"
        print(f"    ROIC: {roic} | ROE: {roe} | FCF Yield: {fcf_y} | Margin: {margin}", file=file)

        pe = f"{m.pe_ratio:.1f}" if m.pe_ratio else "N/A"
        peg = f"{m.peg_ratio:.2f}" if m.peg_ratio else "N/A"
        ev_ebitda = f"{m.ev_to_ebitda:.1f}" if m.ev_to_ebitda else "N/A"
        print(f"    P/E: {pe} | PEG: {peg} | EV/EBITDA: {ev_ebitda}", file=file)

        rev_g = f"{m.revenue_growth_yoy:.0%}" if m.revenue_growth_yoy else "N/A"
        de = f"{m.debt_to_equity:.2f}" if m.debt_to_equity is not None else "N/A"
        z = f"{m.altman_z_score:.2f}" if m.altman_z_score else "N/A"
        print(f"    Revenue Growth: {rev_g} | D/E: {de} | Altman Z: {z}", file=file)

        if m.strengths:
            print(f"\n  ✅ Strengths: {', '.join(m.strengths)}", file=file)
        if m.weaknesses:
            print(f"  ⚠️  Watch: {', '.join(m.weaknesses)}", file=file)


def print_ranking_by_metric(metrics_list: List[FundamentalMetrics], metric: str, file=None):
    """Rank stocks by specific metric."""
    print(f"\n{'='*80}", file=file)
    print(f"  RANKING BY: {metric.upper()}", file=file)
    print(f"{'='*80}", file=file)

    rankings = []
    for m in metrics_list:
        value = getattr(m, metric, None)
        if value is not None:
            rankings.append((m.symbol, value, m))

    reverse = metric not in ["pe_ratio", "debt_to_equity", "ev_to_ebitda", "ev_to_fcf", "peg_ratio"]
    rankings.sort(key=lambda x: x[1] if x[1] is not None else float("-inf"), reverse=reverse)

    print(f"\n  {'Rank':<6} {'Symbol':<10} {metric.upper():<15} {'Grade':<8}", file=file)
    print("-" * 50, file=file)

    for i, (symbol, value, m) in enumerate(rankings, 1):
        if isinstance(value, float):
            if metric in ["roic", "roe", "fcf_yield", "profit_margin", "revenue_growth_yoy"]:
                val_str = f"{value:.1%}"
            else:
                val_str = f"{value:.2f}"
        else:
            val_str = str(value)

        print(f"  {i:<6} {symbol:<10} {val_str:<15} {m.fundamental_grade:<8}", file=file)


def main(argv: Optional[List[str]] = None):
    parser = argparse.ArgumentParser(description="Advanced Fundamental Analyzer")
    parser.add_argument("symbols", nargs="+", help="Stock symbols to analyze")
    parser.add_argument(
        "--focus",
        choices=["quality", "value", "growth", "health", "all"],
        default="all",
        help="Focus on specific aspect",
    )
    parser.add_argument("--rank-by", type=str, help="Rank by specific metric (e.g., roic, fcf_yield)")
    parser.add_argument("-o", "--output", type=str, help="Output file path")
    parser.add_argument("--brief", action="store_true", help="Brief comparison only")

    args = parser.parse_args(argv)

    output_file = None
    if args.output:
        import os

        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        output_file = open(args.output, "w", encoding="utf-8")

    print("\n🔬 ADVANCED FUNDAMENTAL ANALYZER")
    print(f"{'='*50}")
    print(f"Analyzing: {', '.join(args.symbols)}")

    analyzer = FundamentalAnalyzer()
    metrics_list: List[FundamentalMetrics] = []

    for symbol in args.symbols:
        print(f"  Analyzing {symbol}...", end=" ", flush=True)
        metrics = analyzer.analyze(symbol.upper())
        if metrics:
            metrics_list.append(metrics)
            print(f"✅ Grade {metrics.fundamental_grade}")
        else:
            print("❌ Failed")

    if not metrics_list:
        print("\n❌ No stocks analyzed successfully")
        return

    file = output_file

    print_comparison_table(metrics_list, file=file)
    if not args.brief:
        print_detailed_comparison(metrics_list, file=file)

    if args.rank_by:
        print_ranking_by_metric(metrics_list, args.rank_by, file=file)

    if len(args.symbols) == 1 or args.focus != "all":
        for m in metrics_list:
            if output_file:
                import io
                import sys as _sys

                old_stdout = _sys.stdout
                _sys.stdout = io.StringIO()
                print_fundamental_report(m)
                output_file.write(_sys.stdout.getvalue())
                _sys.stdout = old_stdout
            else:
                print_fundamental_report(m)

    print(f"\n{'='*80}", file=file)
    print("  SUMMARY", file=file)
    print(f"{'='*80}", file=file)

    a_grade = [m for m in metrics_list if m.fundamental_grade == "A"]
    b_grade = [m for m in metrics_list if m.fundamental_grade == "B"]

    if a_grade:
        symbols = ", ".join([m.symbol for m in a_grade])
        print(f"\n  🏆 Grade A (Excellent Fundamentals): {symbols}", file=file)

    if b_grade:
        symbols = ", ".join([m.symbol for m in b_grade])
        print(f"  ⭐ Grade B (Good Fundamentals): {symbols}", file=file)

    if len(metrics_list) > 1:
        best_quality = max(metrics_list, key=lambda x: x.quality_score)
        best_value = max(metrics_list, key=lambda x: x.value_score)
        best_growth = max(metrics_list, key=lambda x: x.growth_score)
        best_health = max(metrics_list, key=lambda x: x.financial_health_score)

        print(f"\n  Best Quality:  {best_quality.symbol} ({best_quality.quality_score:.0f})", file=file)
        print(f"  Best Value:    {best_value.symbol} ({best_value.value_score:.0f})", file=file)
        print(f"  Best Growth:   {best_growth.symbol} ({best_growth.growth_score:.0f})", file=file)
        print(f"  Best Health:   {best_health.symbol} ({best_health.financial_health_score:.0f})", file=file)

    print(file=file)

    if output_file:
        output_file.close()
        print(f"\n✅ Results saved to: {args.output}")


if __name__ == "__main__":
    main()

