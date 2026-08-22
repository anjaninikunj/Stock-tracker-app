import os
import json
import sys
from datetime import datetime, timedelta
from nse_client import NSEClient
import config

# Reconfigure stdout for UTF-8 compatibility (especially on Windows consoles)
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

HISTORY_FILE = os.path.join(config.SNAPSHOT_DIR, "ohlc_history.json")

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[-] Warning: Failed to load history file: {e}. Reinitializing.")
            
    return {"spot": [], "options": {}}

def save_history(history):
    os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[-] Error writing history file: {e}")

def clean_stale_data(history, current_time_str):
    """
    Cleans option keys that haven't been updated for more than 4 hours.
    """
    try:
        current_dt = datetime.strptime(current_time_str, "%d-%b-%Y %H:%M:%S")
    except ValueError:
        try:
            current_dt = datetime.strptime(current_time_str, "%d-%b-%Y %H:%M")
        except Exception:
            return
            
    stale_keys = []
    for key, ticks in history.get("options", {}).items():
        if not ticks:
            stale_keys.append(key)
            continue
            
        # Check last tick timestamp
        last_ts = ticks[-1].get("timestamp")
        try:
            last_dt = datetime.strptime(last_ts, "%d-%b-%Y %H:%M:%S")
        except (ValueError, TypeError):
            try:
                last_dt = datetime.strptime(last_ts, "%d-%b-%Y %H:%M")
            except Exception:
                stale_keys.append(key)
                continue
                
        # If last update was more than 96 hours ago (4 days), mark for removal
        # This ensures Friday data survives weekends and is carried over to Monday morning
        if current_dt - last_dt > timedelta(hours=96):
            stale_keys.append(key)
            
    for key in stale_keys:
        history["options"].pop(key, None)
        print(f"[*] Cleaned stale option history key: {key}")

def collect_tick():
    print("[*] Collecting live 1-minute tick...")
    client = NSEClient()
    
    # 1. Fetch market data (Spot)
    try:
        market_data = client.get_market_data()
        spot_price = market_data["spot"]["ltp"]
    except Exception as e:
        print(f"[-] Error fetching spot price: {e}")
        return False
        
    # 2. Fetch option chain
    try:
        opt_data = client.get_option_chain()
    except Exception as e:
        print(f"[-] Error fetching option chain: {e}")
        return False
        
    # 3. Extract expiry and strikes
    try:
        expiry, atm_strike, selected_rows, timestamp = client.extract_expiry_and_strikes(opt_data, spot_price)
    except Exception as e:
        print(f"[-] Error extracting option chain strikes: {e}")
        return False
        
    if not timestamp:
        # Fallback to local time if NSE timestamp is empty
        timestamp = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        
    # Load existing history
    history = load_history()
    
    # Check if Spot price tick already exists with this exact timestamp
    spot_ticks = history.setdefault("spot", [])
    if spot_ticks and spot_ticks[-1].get("timestamp") == timestamp:
        print(f"[*] Tick for timestamp {timestamp} already collected. Skipping.")
        return True
        
    # Append Spot tick
    spot_ticks.append({"timestamp": timestamp, "price": spot_price})
    history["spot"] = spot_ticks[-500:] # Limit to 500 minutes (8.33 hours)
    
    # Append Option ticks for active strikes
    options_hist = history.setdefault("options", {})
    for row in selected_rows:
        strike = int(row.get("strikePrice"))
        for side in ["CE", "PE"]:
            side_data = row.get(side)
            if side_data:
                ltp = side_data.get("lastPrice")
                if ltp is not None:
                    key = f"{strike}_{side}"
                    ticks = options_hist.setdefault(key, [])
                    ticks.append({"timestamp": timestamp, "price": float(ltp)})
                    options_hist[key] = ticks[-500:] # Limit to 500 entries
                    
    # Clean stale keys
    clean_stale_data(history, timestamp)
    
    # Save back to disk
    save_history(history)
    print(f"[+] Successfully saved 1-minute tick for {timestamp}.")
    return True

if __name__ == "__main__":
    collect_tick()
