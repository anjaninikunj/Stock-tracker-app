import os
import sys
import re
import json
import html
import time
import urllib.request
import urllib.parse
import http.cookiejar
import ssl
from datetime import datetime, timedelta, timezone

# Reconfigure stdout/stderr to handle UTF-8 symbols (like emojis) on Windows consoles
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8890560111:AAFgExgQVny8lspqd8hMZxWGJFRHJxSUDtg")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "811302410")
SCREENER_URL = "https://chartink.com/screener/15-minutes-jyoti-budhiya-52-week-high-breakout-myb-open-low-near-by"
PROCESS_URL = "https://chartink.com/screener/process"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Create a cookie jar to retain session cookies across requests
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def run_request_with_retry(request, max_retries=3, delay=3):
    """Executes an HTTP request with automatic retries and SSL fallback for robust operation."""
    for attempt in range(1, max_retries + 1):
        try:
            # Try with default verified SSL context
            with opener.open(request) as response:
                return response.read()
        except Exception as e:
            # Handle SSL certificate verification failures (common on local corporate/antivirus environments)
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                print(f"Warning: SSL certificate verification failed ({e}). Retrying with unverified SSL context...")
                try:
                    unverified_context = ssl._create_unverified_context()
                    unverified_opener = urllib.request.build_opener(
                        urllib.request.HTTPCookieProcessor(cj),
                        urllib.request.HTTPSHandler(context=unverified_context)
                    )
                    with unverified_opener.open(request) as response:
                        return response.read()
                except Exception as inner_e:
                    if attempt == max_retries:
                        raise
                    print(f"Attempt {attempt} failed (SSL fallback): {inner_e}. Retrying in {delay} seconds...")
            else:
                if attempt == max_retries:
                    raise
                print(f"Attempt {attempt} failed: {e}. Retrying in {delay} seconds...")
            
            time.sleep(delay)

def get_screener_data():
    """Fetches the screener page, extracts the CSRF token and the scan clause, and runs the scan."""
    print(f"Step 1: Fetching main screener page: {SCREENER_URL}")
    req = urllib.request.Request(
        SCREENER_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://chartink.com/"
        }
    )
    html_content = run_request_with_retry(req).decode("utf-8")

    # Extract CSRF Token
    print("Step 2: Extracting CSRF token from page meta tags...")
    csrf_match = re.search(r'meta name="csrf-token" content="([^"]+)"', html_content)
    if not csrf_match:
        raise ValueError("Could not find CSRF token in screener page HTML.")
    csrf_token = csrf_match.group(1)
    print("CSRF Token extracted successfully.")

    # Extract Scan Clause from :scan-json Vue attribute
    print("Step 3: Extracting scan clause (atlas_query) from scanner config...")
    scan_json_match = re.search(r':scan-json="([^"]+)"', html_content)
    if not scan_json_match:
        raise ValueError("Could not find :scan-json attribute in screener page HTML.")
    
    unescaped_json = html.unescape(scan_json_match.group(1))
    scan_config = json.loads(unescaped_json)
    scan_clause = scan_config.get("atlas_query")
    if not scan_clause:
        raise ValueError("Could not find 'atlas_query' within scan-json data.")
    print("Scan clause extracted successfully.")

    # Run the scan via POST
    print("Step 4: Posting query to processing endpoint...")
    post_data = urllib.parse.urlencode({"scan_clause": scan_clause}).encode("utf-8")
    post_req = urllib.request.Request(
        PROCESS_URL,
        data=post_data,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": SCREENER_URL,
            "X-CSRF-TOKEN": csrf_token,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }
    )
    
    response_bytes = run_request_with_retry(post_req)
    result = json.loads(response_bytes.decode("utf-8"))
    
    if "data" not in result:
        raise ValueError(f"Unexpected API response format: {result}")
        
    return result["data"]

def format_alert_message(stocks):
    """Formats the alert message using the specified 15-minute Jyoti Budhiya template."""
    # Convert UTC time to IST (UTC+5:30)
    utc_now = datetime.now(timezone.utc)
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    
    header_time_str = ist_now.strftime("%H:%M") # e.g. 10:15
    ist_time_str = ist_now.strftime("%H:%M IST") # e.g. 11:00 IST
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

def send_telegram_alert(message):
    """Sends the formatted alert message via Telegram Bot API."""
    print("Step 5: Sending Telegram alert...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json"
        }
    )
    
    def process_response(resp):
        response_data = resp.read().decode("utf-8")
        res_json = json.loads(response_data)
        if not res_json.get("ok"):
            raise ValueError(f"Telegram API returned an error: {response_data}")
    
    try:
        with urllib.request.urlopen(req) as response:
            process_response(response)
    except Exception as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            print(f"Warning: SSL certificate verification failed ({e}). Retrying with unverified SSL context...")
            try:
                unverified_context = ssl._create_unverified_context()
                with urllib.request.urlopen(req, context=unverified_context) as response:
                    process_response(response)
            except Exception as inner_e:
                raise ValueError(f"Failed to send Telegram alert even with unverified SSL: {inner_e}")
        else:
            raise
            
    print("Telegram alert sent successfully!")

def main():
    try:
        stocks = get_screener_data()
        print(f"Scan complete. Found {len(stocks)} stocks.")
        
        message = format_alert_message(stocks)
        print("\n--- Formatted Alert Message ---")
        print(message)
        print("-------------------------------\n")
        
        send_telegram_alert(message)
        print("Workflow executed successfully.")
        
    except Exception as e:
        print(f"\nFATAL ERROR: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
