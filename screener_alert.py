import os
import sys
from datetime import datetime, timedelta, timezone
import chartink_utils

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8890560111:AAFgExgQVny8lspqd8hMZxWGJFRHJxSUDtg")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "811302410")
SCREENER_URL = "https://chartink.com/screener/codex-milega-hilega-scanner-weekly"

def format_alert_message(stocks):
    """Formats the alert message using the specified template."""
    # Convert UTC time to IST (UTC+5:30)
    utc_now = datetime.now(timezone.utc)
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    ist_time_str = ist_now.strftime("%H:%M IST")
    stock_time_str = ist_now.strftime("%I:%M %p")
    
    total_stocks = len(stocks)
    
    message_lines = [
        "🚀 Codex Milega Hilega Weekly",
        "",
        f"Total Stocks: {total_stocks}",
        ""
    ]
    
    if total_stocks == 0:
        message_lines.append(f"No stock found {stock_time_str}")
    else:
        for idx, stock in enumerate(stocks):
            # Prefer nsecode, fallback to name, then bsecode
            symbol = stock.get("nsecode") or stock.get("name") or stock.get("bsecode") or "UNKNOWN"
            symbol = str(symbol).strip().upper()
            
            close_price = stock.get("close")
            per_chg = stock.get("per_chg")
            
            close_str = f"{close_price:.2f}" if isinstance(close_price, (int, float)) else str(close_price)
            if isinstance(per_chg, (int, float)):
                per_chg_str = f"+{per_chg:.2f}%" if per_chg > 0 else f"{per_chg:.2f}%"
            else:
                per_chg_str = str(per_chg) if per_chg else ""
                
            message_lines.append(f"{idx + 1}. {symbol} {close_str}  {per_chg_str}")
        
    return "\n".join(message_lines)

def main():
    try:
        stocks = chartink_utils.get_screener_data(SCREENER_URL)
        print(f"Scan complete. Found {len(stocks)} stocks.")
        
        # Track performance in strategy_performance.csv (Excel)
        chartink_utils.track_performance("Codex Milega Hilega Weekly", stocks)
        
        message = format_alert_message(stocks)
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
