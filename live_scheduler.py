import time
import subprocess
from datetime import datetime, timedelta

def is_market_open():
    now = datetime.now()
    # Check weekday (0 = Monday, 4 = Friday)
    if now.weekday() > 4:
        return False
    # Check market hours (09:15 to 15:30)
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_start <= now <= market_end

print("="*60)
print(" NIFTY LIVE ANALYSIS BACKGROUND SCHEDULER")
print("="*60)
print("[*] Monitoring time to execute history collection every 1 minute.")
print("[*] Monitoring time to execute Nifty playbook analysis every 30 minutes.")
print("[*] Operational window: Mon-Fri, 09:15 AM - 03:30 PM IST.")

last_playbook_run = datetime.min
last_collector_run = datetime.min

while True:
    now = datetime.now()
    if is_market_open():
        # 1. Trigger collector every 1 minute
        if now - last_collector_run >= timedelta(minutes=1):
            print(f"\n[*] Triggering 1-minute Nifty Spot & Option tick collection at {now.strftime('%H:%M:%S')}...")
            try:
                subprocess.run(["python", "history_collector.py"], check=True)
                last_collector_run = now
            except subprocess.CalledProcessError as e:
                print(f"[-] Collector execution error: {e}")
            except Exception as e:
                print(f"[-] Collector error: {e}")
                
        # 2. Trigger Nifty playbook analysis every 30 minutes
        if now - last_playbook_run >= timedelta(minutes=30):
            print(f"\n[*] Triggering 30-minute Nifty Playbook Analysis Pipeline at {now.strftime('%H:%M:%S')}...")
            try:
                result = subprocess.run(["python", "run_collection.py"], check=True, capture_output=True, text=True)
                print("[+] Analysis and Telegram updates dispatched successfully.")
                lines = result.stdout.split("\n")
                summary_start = next((i for i, l in enumerate(lines) if "MARKET PROFILE SUMMARY" in l), 0)
                print("\n".join(lines[summary_start:summary_start+30]))
                last_playbook_run = now
            except subprocess.CalledProcessError as e:
                print(f"[-] Playbook execution error: {e.stderr}")
            except Exception as e:
                print(f"[-] Playbook error: {e}")
                
        # Sleep for 15 seconds to check again without high CPU usage
        time.sleep(15)
    else:
        # If market closed, sleep for 5 minutes and check again
        print(f"[*] Market closed. Current Time: {now.strftime('%d-%b-%Y %H:%M:%S')}. Sleeping for 5 minutes...", end="\r")
        time.sleep(300)
