import os

# NSE API Configurations
NSE_MAIN_URL = "https://www.nseindia.com"
NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
NSE_INDICES_URL = "https://www.nseindia.com/api/allIndices"

# Browser Simulation Headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
}

# Local Storage Configurations
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = os.path.join(BASE_DIR, "snapshots")

# Expiry Filter: set to None to automatically grab the nearest/first available expiry date
TARGET_EXPIRY = None 
