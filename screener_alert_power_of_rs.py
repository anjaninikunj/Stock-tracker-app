import os
import sys
from datetime import datetime, timedelta, timezone
import chartink_utils

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8890560111:AAFgExgQVny8lspqd8hMZxWGJFRHJxSUDtg")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "811302410")

# Define the 6 screeners that form the 'Power of Rs (Vivek sir)' strategy.
# Set 'enabled': True to include it in the combination (intersection).
SCREENERS = [
    {
        "id": 1,
        "name": "Close Within 52 Week High Zone",
        "url": "https://chartink.com/screener/copy-near-52-week-high-699",
        "enabled": True
    },
    {
        "id": 2,
        "name": "Stock Outperforming Benchmark Index in both 1 Week and 3 Month",
        "url": "https://chartink.com/screener/copy-stock-outperforming-benchmark-index-in-both-1-week-3-month-141",
        "enabled": True
    },
    {
        "id": 3,
        "name": "Strongly Outperforming - Benchmark Index (55 Days)",
        "url": "https://chartink.com/screener/copy-stock-outperforming-benchmark-index-in-both-1-week-3-month-141",
        "enabled": True
    },
    {
        "id": 4,
        "name": "Close Within 5 Year High Zone",
        "url": "https://chartink.com/screener/5-year-high-zone-15",
        "enabled": True
    },
    {
        "id": 5,
        "name": "Close Crossing All Time High",
        "url": "https://chartink.com/screener/copy-near-all-time-high-6",
        "enabled": True
    },
    {
        "id": 6,
        "name": "Both SRS And ARS Above Zero",
        "url": "https://chartink.com/screener/rrs-both-srs-and-ars-above-zero",
        "enabled": True
    }
]

def format_alert_message(stocks, active_screener_names):
    """Formats the alert message listing active screeners and matching stocks."""
    utc_now = datetime.now(timezone.utc)
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    ist_time_str = ist_now.strftime("%I:%M %p IST")
    date_str = ist_now.strftime("%d-%b-%Y")
    
    message_lines = [
        "🚀 *Power of Rs (Vivek sir)*",
        f"📅 Date: {date_str} | Time: {ist_time_str}",
        "",
        "*Active Scanners in Combination:*",
    ]
    for idx, name in enumerate(active_screener_names):
        message_lines.append(f"  • {name}")
    
    message_lines.extend([
        "",
        f"*Total Combined Stocks:* {len(stocks)}",
        ""
    ])
    
    if len(stocks) == 0:
        message_lines.append("No stock found matching all active scanners.")
    else:
        message_lines.append("*Matching Stocks:*")
        for idx, stock in enumerate(stocks):
            symbol = stock.get("nsecode") or stock.get("name") or stock.get("bsecode") or "UNKNOWN"
            symbol = str(symbol).strip().upper()
            close_price = stock.get("close")
            per_chg = stock.get("per_chg")
            
            close_str = f"{close_price:.2f}" if isinstance(close_price, (int, float)) else str(close_price)
            if isinstance(per_chg, (int, float)):
                per_chg_str = f"+{per_chg:.2f}%" if per_chg > 0 else f"{per_chg:.2f}%"
            else:
                per_chg_str = str(per_chg) if per_chg else ""
                
            message_lines.append(f"  {idx + 1}. {symbol} {close_str}  {per_chg_str}")
            
    return "\n".join(message_lines)

def main():
    try:
        active_screeners = [s for s in SCREENERS if s["enabled"]]
        if not active_screeners:
            print("No screeners enabled. Exiting.")
            sys.exit(0)
            
        print(f"Starting combined scan. Active screeners count: {len(active_screeners)}")
        
        # Cache results by URL to avoid redundant HTTP requests
        url_cache = {}
        screener_symbol_sets = []
        all_stock_details = {}
        
        for screener in active_screeners:
            url = screener["url"]
            name = screener["name"]
            
            if url not in url_cache:
                print(f"\nFetching data for URL: {url} ({name})...")
                try:
                    stocks = chartink_utils.get_screener_data(url)
                    url_cache[url] = stocks
                    print(f"Found {len(stocks)} stocks.")
                except Exception as ex:
                    print(f"Error fetching data for {name}: {ex}", file=sys.stderr)
                    # Exclude failed screener to avoid breaking the script, or raise exception
                    # We raise to ensure database/alert consistency
                    raise
            else:
                print(f"\nReusing cached data for {name}...")
                stocks = url_cache[url]
                
            # Process stocks and build the symbol set
            symbol_set = set()
            for stock in stocks:
                symbol = stock.get("nsecode") or stock.get("name") or stock.get("bsecode") or "UNKNOWN"
                symbol = str(symbol).strip().upper()
                symbol_set.add(symbol)
                # Save the most recent details
                all_stock_details[symbol] = stock
                
            screener_symbol_sets.append(symbol_set)
            
        # Find intersection: stocks present in ALL enabled screeners
        print("\nCombining results using intersection...")
        combined_symbols = set.intersection(*screener_symbol_sets) if screener_symbol_sets else set()
        combined_symbols = sorted(list(combined_symbols))
        
        print(f"Combined Scan complete. Found {len(combined_symbols)} stocks matching all conditions.")
        
        # Build the final stocks list of dicts for combined stocks
        combined_stocks = []
        for symbol in combined_symbols:
            combined_stocks.append(all_stock_details[symbol])
            
        # Track performance in strategy_performance.csv (Excel)
        chartink_utils.track_performance("Power of Rs (Vivek sir)", combined_stocks)
        
        # Format and send the alert
        active_screener_names = [s["name"] for s in active_screeners]
        message = format_alert_message(combined_stocks, active_screener_names)
        
        print("\n--- Formatted Alert Message ---")
        print(message)
        print("-------------------------------\n")
        
        chartink_utils.send_telegram_alert(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, message)
        print("Workflow executed successfully.")
        
    except Exception as e:
        print(f"\nFATAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
