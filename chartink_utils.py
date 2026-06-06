import sys
import re
import json
import html
import time
import urllib.request
import urllib.parse
import http.cookiejar
import ssl
import csv
import os
from datetime import datetime, timedelta, timezone

# Reconfigure stdout/stderr to handle UTF-8 symbols (like emojis) on Windows consoles
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

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

def get_screener_data(screener_url, process_url="https://chartink.com/screener/process"):
    """Fetches the screener page, extracts the CSRF token and the scan clause, and runs the scan."""
    print(f"Step 1: Fetching main screener page: {screener_url}")
    req = urllib.request.Request(
        screener_url,
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
        process_url,
        data=post_data,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": screener_url,
            "X-CSRF-TOKEN": csrf_token,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
        }
    )
    
    response_bytes = run_request_with_retry(post_req)
    result = json.loads(response_bytes.decode("utf-8"))
    
    if "data" not in result:
        raise ValueError(f"Unexpected API response format: {result}")
        
    return result["data"]

def send_telegram_alert(bot_token, chat_id, message):
    """Sends the formatted alert message via Telegram Bot API."""
    print("Step 5: Sending Telegram alert...")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
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

def fetch_yahoo_price(symbol):
    """Fetches the latest regular market price for an NSE stock from Yahoo Finance."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        # Ignore SSL errors locally if needed
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response:
            data = json.loads(response.read().decode('utf-8'))
            return data['chart']['result'][0]['meta']['regularMarketPrice']
    except Exception as e:
        print(f"Warning: Could not fetch Yahoo price for {symbol}: {e}")
        return None

def track_performance(strategy_name, stocks):
    """Logs the stock entries, exits, and Friday prices to strategy_performance.csv."""
    print(f"EVALUATING PERFORMANCE LOGS FOR: {strategy_name}")
    
    # Calculate current IST times
    utc_now = datetime.now(timezone.utc)
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    
    # Calculate Monday date of the current week
    monday = ist_now - timedelta(days=ist_now.weekday())
    week_start = monday.strftime("%d-%B-%Y")
    date_str = ist_now.strftime("%d-%B-%Y")
    
    is_friday = (ist_now.weekday() == 4)
    # Define last run of Friday (around 15:15 IST / 3:15 PM or later)
    is_last_run_of_friday = is_friday and (ist_now.hour >= 15)
    
    # Extract current scanner stock names and prices
    current_scan_symbols = set()
    symbol_prices = {}
    for stock in stocks:
        symbol = stock.get("nsecode") or stock.get("name") or stock.get("bsecode") or "UNKNOWN"
        symbol = str(symbol).strip().upper()
        current_scan_symbols.add(symbol)
        symbol_prices[symbol] = stock.get("close")
        
    csv_file = "strategy_performance.csv"
    headers = ["Week Start", "Strategy", "Date Added", "Stock Symbol", "Entry Price", "Weekend Price (Friday)", "Status"]
    
    rows = []
    if os.path.exists(csv_file):
        try:
            with open(csv_file, mode="r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception as e:
            print(f"Error reading CSV: {e}. Reinitializing.")
            rows = []
            
    # Track which stocks already have rows for this week + strategy
    seen_symbols = set()
    updated_rows = []
    
    for row in rows:
        # Check if the row belongs to the current week and strategy
        if row["Week Start"] == week_start and row["Strategy"] == strategy_name:
            symbol = row["Stock Symbol"].strip().upper()
            seen_symbols.add(symbol)
            
            # If stock is in the current scan results, it is active
            if symbol in current_scan_symbols:
                row["Status"] = "IN (in strategy still)"
                if is_friday:
                    row["Weekend Price (Friday)"] = str(symbol_prices[symbol])
            else:
                # If stock is not in the current scan results, it exited
                row["Status"] = "OUT (Not in Strategy)"
                # If it's Friday's last run and we don't have a Friday price yet, query it from Yahoo Finance
                if is_last_run_of_friday and (not row["Weekend Price (Friday)"] or row["Weekend Price (Friday)"] == "-"):
                    yahoo_price = fetch_yahoo_price(symbol)
                    if yahoo_price:
                        row["Weekend Price (Friday)"] = str(yahoo_price)
                    else:
                        row["Weekend Price (Friday)"] = "-"
                        
        updated_rows.append(row)
        
    # Add new entries for newly detected stocks this week
    for symbol in current_scan_symbols:
        if symbol not in seen_symbols:
            new_row = {
                "Week Start": week_start,
                "Strategy": strategy_name,
                "Date Added": date_str,
                "Stock Symbol": symbol,
                "Entry Price": str(symbol_prices[symbol]),
                "Weekend Price (Friday)": str(symbol_prices[symbol]) if is_friday else "-",
                "Status": "IN (in strategy still)"
            }
            updated_rows.append(new_row)
            
    # Write back to CSV
    try:
        with open(csv_file, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(updated_rows)
        print(f"Performance report updated in {csv_file}")
    except Exception as e:
        print(f"Error writing to CSV: {e}")
