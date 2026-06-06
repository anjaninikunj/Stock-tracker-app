import os
import sys
from datetime import datetime, timedelta, timezone
import chartink_utils

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8890560111:AAFgExgQVny8lspqd8hMZxWGJFRHJxSUDtg")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "811302410")
SCREENER_URL = "https://chartink.com/screener/15-minutes-jyoti-budhiya-52-week-high-breakout-myb-open-low-near-by"

def format_alert_message(stocks):
    """Formats the alert message using the specified 15-minute Jyoti Budhiya template."""
    # Convert UTC time to IST (UTC+5:30)
    utc_now = datetime.now(timezone.utc)
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    
    header_time_str = ist_now.strftime("%H:%M") # e.g. 10:15
    stock_time_str = ist_now.strftime("%I:%M %p") # e.g. 01:13 PM
    
    total_stocks = len(stocks)
    
    message_lines = [
        f"🚀 {header_time_str} | BTST Open≈Low 15 Mins",
        "",
        "Scanner: JYOTI BUDHIYA 52W High BO + Open≈Low",
        "",
        f"Total Stocks: {total_stocks}",
        ""
    ]
    
    if total_stocks == 0:
        message_lines.append(f"No stock found {stock_time_str}")
    else:
        message_lines.append("Stocks:")
        for stock in stocks:
            # Prefer nsecode, fallback to name, then bsecode
            symbol = stock.get("nsecode") or stock.get("name") or stock.get("bsecode") or "UNKNOWN"
            symbol = str(symbol).strip().upper()
            message_lines.append(f"• {symbol}")
        
    return "\n".join(message_lines)

def main():
    try:
        stocks = chartink_utils.get_screener_data(SCREENER_URL)
        print(f"Scan complete. Found {len(stocks)} stocks.")
        
        # Track performance in strategy_performance.csv (Excel)
        chartink_utils.track_performance("Jyoti Budhiya 15M Breakout", stocks)
        
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
