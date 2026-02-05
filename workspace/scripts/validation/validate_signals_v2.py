import sys
import json
import yfinance as yf
from datetime import datetime, timedelta, timezone
import os

def get_current_price(ticker):
    """Get the most recent price for a ticker."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        if not hist.empty:
            close_val = hist['Close'].iloc[-1]
            return round(float(close_val.item() if hasattr(close_val, 'item') else close_val), 2)
    except:
        pass
    return None

def validate_signals(json_file):
    with open(json_file, 'r') as f:
        signals = json.load(f)

    goku_results = []
    wilson_results = []

    print(f"Validating {len(signals)} signals...")

    for signal in signals:
        ticker = signal['ticker'].replace('$', '')
        signal_date_str = signal['date']
        source = signal.get('source', 'Unknown')
        
        try:
            signal_date = datetime.fromisoformat(signal_date_str).replace(tzinfo=None)
        except ValueError:
            signal_date = datetime.strptime(signal_date_str.split('.')[0], "%Y-%m-%dT%H:%M:%S")

        print(f"Checking {ticker} from {signal_date.date()} ({source})...")

        start_date = signal_date
        end_date = signal_date + timedelta(days=90)
        
        try:
            df = yf.download(ticker, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d'), progress=False)
            
            if df.empty:
                result = {**signal, "error": "No data found"}
            else:
                # Handle both Series and scalar values from yfinance
                close_val = df['Close'].iloc[0]
                entry_price = float(close_val.item() if hasattr(close_val, 'item') else close_val)
                current_price = get_current_price(ticker)
                
                validation = {
                    "entry_price": round(entry_price, 2),
                    "current_price": current_price,
                    "returns": {}
                }

                # Check 5, 10, 15, 30, 60 days
                for days in [5, 10, 15, 30, 60]:
                    target_date = signal_date + timedelta(days=days)
                    future_slice = df[df.index >= target_date]
                    
                    if future_slice.empty:
                        validation["returns"][f"{days}d"] = None
                    else:
                        exit_val = future_slice['Close'].iloc[0]
                        exit_price = float(exit_val.item() if hasattr(exit_val, 'item') else exit_val)
                        ret = ((exit_price - entry_price) / entry_price) * 100
                        validation["returns"][f"{days}d"] = round(ret, 2)

                result = {**signal, "validation": validation}

        except Exception as e:
            result = {**signal, "error": str(e)}
        
        if source == 'Goku':
            goku_results.append(result)
        else:
            wilson_results.append(result)

    # Generate separate reports
    report_dir = os.path.dirname(json_file).replace('data', 'data/reports')
    os.makedirs(report_dir, exist_ok=True)
    
    # Goku Report
    goku_report = generate_report("GOKU", "TECHNICAL SIGNALS", goku_results)
    with open(os.path.join(report_dir, 'goku_signal_validation.txt'), 'w') as f:
        f.write(goku_report)
    
    # Wilson Report
    wilson_report = generate_report("WILSON", "FUNDAMENTAL SIGNALS", wilson_results)
    with open(os.path.join(report_dir, 'wilson_signal_validation.txt'), 'w') as f:
        f.write(wilson_report)
    
    # Combined Report
    combined = f"{'='*60}\n"
    combined += "COMBINED SIGNAL VALIDATION REPORT\n"
    combined += f"{'='*60}\n\n"
    combined += goku_report + "\n\n" + wilson_report
    
    with open(os.path.join(report_dir, 'signal_validation_report.txt'), 'w') as f:
        f.write(combined)
    
    print(f"\nReports saved:")
    print(f"  - goku_signal_validation.txt ({len(goku_results)} signals)")
    print(f"  - wilson_signal_validation.txt ({len(wilson_results)} signals)")
    print(f"  - signal_validation_report.txt (combined)")

def generate_report(server_name, title, results):
    lines = [
        f"{'='*60}",
        f"{server_name} SERVER - {title}",
        f"{'='*60}",
        ""
    ]
    
    for res in results:
        ticker = res.get('ticker', 'Unknown')
        date = res.get('date', '')[:10]
        content = res.get('content', '')[:150]
        
        lines.append(f"SIGNAL: {ticker} | Date: {date}")
        lines.append(f"Content: {content}...")
        
        if 'error' in res:
            lines.append(f"Status: Error - {res['error']}")
        else:
            val = res['validation']
            entry = val.get('entry_price', 'N/A')
            current = val.get('current_price', 'N/A')
            
            lines.append(f"Entry: ${entry} | Current: ${current}")
            
            # Performance line
            perf_parts = []
            for period in ['5d', '10d', '15d', '30d', '60d']:
                ret = val['returns'].get(period)
                if ret is not None:
                    sign = '+' if ret >= 0 else ''
                    perf_parts.append(f"{period}:{sign}{ret}%")
                else:
                    perf_parts.append(f"{period}:N/A")
            
            lines.append(f"Performance: {' | '.join(perf_parts)}")
        
        lines.append("-" * 50)
    
    return '\n'.join(lines)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_signals_v2.py <json_file>")
        sys.exit(1)
    
    validate_signals(sys.argv[1])
