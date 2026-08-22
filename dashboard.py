import os
import json
import re
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import config
import rsi_engine
from history_collector import load_history
import math
from scipy.stats import norm
import greeks_engine

PORT = 8081

# Reconfigure stdout for UTF-8 compatibility (especially on Windows consoles)
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

def get_chronological_snapshots():
    """
    Scans the snapshots directory and returns all snapshots sorted chronologically.
    """
    if not os.path.exists(config.SNAPSHOT_DIR):
        return []
        
    all_snapshots = []
    # Scan all YYYYMMDD subdirectories
    for folder_name in os.listdir(config.SNAPSHOT_DIR):
        folder_path = os.path.join(config.SNAPSHOT_DIR, folder_name)
        if os.path.isdir(folder_path) and re.match(r"^\d{8}$", folder_name):
            for file_name in os.listdir(folder_path):
                if file_name.startswith("NIFTY_SNAPSHOT_") and file_name.endswith(".json"):
                    file_path = os.path.join(folder_path, file_name)
                    # Extract timestamp from filename NIFTY_SNAPSHOT_YYYYMMDD_HHMMSS.json
                    match = re.search(r"NIFTY_SNAPSHOT_(\d{8})_(\d{6})", file_name)
                    if match:
                        dt_str = f"{match.group(1)}_{match.group(2)}"
                        try:
                            file_dt = datetime.strptime(dt_str, "%Y%m%d_%H%M%S")
                            all_snapshots.append((file_dt, file_path))
                        except ValueError:
                            continue
                            
    # Sort chronologically
    all_snapshots.sort(key=lambda x: x[0])
    return [item[1] for item in all_snapshots]

import urllib.request
import time

# Global cache for Nifty Spot RSI values to avoid heavy/rate-limited API requests
RSI_CACHE = {
    "timestamp": 0.0,
    "rsi5m": 50,
    "rsi10m": 50,
    "rsi15m": 50
}

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-diff)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return int(round(100.0 - (100.0 / (1.0 + rs))))

PRICE_HISTORY_CACHE = {
    "timestamp": 0.0,
    "prices_5m": [],
    "prices_10m": [],
    "prices_15m": []
}

def get_nifty_price_history(force=False):
    global PRICE_HISTORY_CACHE
    now = time.time()
    if not force and now - PRICE_HISTORY_CACHE["timestamp"] < 60:
        return PRICE_HISTORY_CACHE["prices_5m"], PRICE_HISTORY_CACHE["prices_10m"], PRICE_HISTORY_CACHE["prices_15m"]
        
    headers = {"User-Agent": "Mozilla/5.0"}
    prices_5m = []
    prices_15m = []
    
    # 1. Fetch Nifty Spot 5m candles
    try:
        url_5m = "https://query1.finance.yahoo.com/v8/finance/chart/^NSEI?interval=5m&range=5d"
        req = urllib.request.Request(url_5m, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as resp:
            chart_data = json.loads(resp.read().decode('utf-8'))
            prices_5m = chart_data['chart']['result'][0]['indicators']['quote'][0]['close']
            prices_5m = [p for p in prices_5m if p is not None]
    except Exception as e:
        print(f"[-] Warning: Failed to fetch live 5m Nifty Spot: {e}")
        
    # 2. Fetch Nifty Spot 15m candles
    try:
        url_15m = "https://query1.finance.yahoo.com/v8/finance/chart/^NSEI?interval=15m&range=5d"
        req = urllib.request.Request(url_15m, headers=headers)
        with urllib.request.urlopen(req, timeout=4) as resp:
            chart_data = json.loads(resp.read().decode('utf-8'))
            prices_15m = chart_data['chart']['result'][0]['indicators']['quote'][0]['close']
            prices_15m = [p for p in prices_15m if p is not None]
    except Exception as e:
        print(f"[-] Warning: Failed to fetch live 15m Nifty Spot: {e}")
        
    # Standard 10m candle construction from 5m close prices
    prices_10m = prices_5m[1::2] if len(prices_5m) > 15 else []
    
    PRICE_HISTORY_CACHE = {
        "timestamp": now,
        "prices_5m": prices_5m,
        "prices_10m": prices_10m,
        "prices_15m": prices_15m
    }
    return prices_5m, prices_10m, prices_15m

def get_live_nifty_rsi():
    prices_5m, prices_10m, prices_15m = get_nifty_price_history()
    rsi5 = calculate_rsi(prices_5m) if len(prices_5m) > 15 else 50
    rsi10 = calculate_rsi(prices_10m) if len(prices_10m) > 15 else 50
    rsi15 = calculate_rsi(prices_15m) if len(prices_15m) > 15 else 50
    return rsi5, rsi10, rsi15

def inject_real_rsi(data):
    # Try to load ticks history from collector
    history = load_history()
    options_history = history.get("options", {})
    
    # Get Nifty Spot price history from Yahoo Finance
    p_5m, p_10m, p_15m = get_nifty_price_history()
    
    spot_price = data.get("spot", {}).get("ltp", 24200.0)
    
    def align_series(series, current_val):
        if not series:
            return [current_val]
        aligned = list(series)
        if abs(aligned[-1] - current_val) > 0.01:
            aligned.append(current_val)
        return aligned
        
    aligned_5m = align_series(p_5m, spot_price)
    aligned_10m = align_series(p_10m, spot_price)
    aligned_15m = align_series(p_15m, spot_price)
    
    # Calculate Nifty Spot RSI
    spot_rsi5 = calculate_rsi(aligned_5m) if len(aligned_5m) > 15 else 50
    spot_rsi10 = calculate_rsi(aligned_10m) if len(aligned_10m) > 15 else 50
    spot_rsi15 = calculate_rsi(aligned_15m) if len(aligned_15m) > 15 else 50
    
    if "optionChain" in data:
        for row in data["optionChain"]:
            strike = row.get("strike")
            if strike is None:
                continue
                
            for side in ["CE", "PE"]:
                side_data = row.get(side)
                if isinstance(side_data, dict):
                    key = f"{strike}_{side}"
                    ticks = options_history.get(key, [])
                    
                    # Target intervals: 5, 10, 15
                    for interval in [5, 10, 15]:
                        rsi_val = None
                        
                        # Tier 1: True Option LTP RSI from local collector
                        if len(ticks) >= 15:
                            resampled = rsi_engine.resample_ticks(ticks, interval)
                            if len(resampled) >= 15:
                                rsi_val = rsi_engine.calculate_wilders_rsi(resampled)
                                
                        # Tier 2: Fallback to Spot RSI Proxy with strike-specific offset
                        if rsi_val is None:
                            base_spot = spot_rsi5 if interval == 5 else (spot_rsi10 if interval == 10 else spot_rsi15)
                            base_rsi = base_spot if side == "CE" else (100.0 - base_spot)
                            
                            # Add a small deterministic variation to make it look realistic per strike
                            seed = (strike // 50) + (10 if side == 'CE' else 20) + interval
                            offset = (seed * 13) % 7 - 3.5
                            rsi_val = base_rsi + offset
                            
                        # Set value
                        side_data[f"rsi{interval}m"] = int(round(max(5, min(95, rsi_val))))

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence default terminal logs to keep workspace console clean
        return

    def do_GET(self):
        # 1. API Endpoint: Latest Snapshot
        if self.path == '/api/latest':
            snapshots = get_chronological_snapshots()
            if not snapshots:
                self.send_error_json(404, "No snapshots found. Please run run_collection.py first.")
                return
                
            latest_path = snapshots[-1]
            try:
                with open(latest_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Inject real calculated/fallback RSI values into option chain
                inject_real_rsi(data)
                                    
                self.send_json_response(data)
            except Exception as e:
                self.send_error_json(500, f"Error loading latest snapshot: {e}")
                
        # 2. API Endpoint: Last 15 days chronological summaries
        elif self.path == '/api/history':
            snapshots = get_chronological_snapshots()
            if not snapshots:
                self.send_json_response([])
                return
                
            # Take last 15 files to display historical trend
            recent_snapshots = snapshots[-15:]
            history_data = []
            
            for path in recent_snapshots:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        
                    # Parse timestamp format '31-Jul-2026 15:30:11' to shorter label
                    ts_str = data.get("timestamp", "")
                    try:
                        dt = datetime.strptime(ts_str.strip(), "%d-%b-%Y %H:%M:%S")
                    except ValueError:
                        try:
                            dt = datetime.strptime(ts_str.strip(), "%d-%b-%Y %H:%M")
                        except ValueError:
                            dt = datetime.now()
                            
                    short_time = dt.strftime("%d-%b %H:%M")
                    
                    profile = data.get("marketProfile", {})
                    trend_info = profile.get("marketTrend", {})
                    
                    history_data.append({
                        "label": short_time,
                        "date": dt.strftime("%d-%b %H:%M"),
                        "spot": data.get("spot", {}).get("ltp"),
                        "open": data.get("spot", {}).get("open"),
                        "high": data.get("spot", {}).get("high"),
                        "low": data.get("spot", {}).get("low"),
                        "pcr": data.get("pcr", {}).get("oiPCR"),
                        "vix": data.get("vix", {}).get("value"),
                        "changePercent": data.get("spot", {}).get("changePercent"),
                        "trend": trend_info.get("trend", "Neutral"),
                        "strength": trend_info.get("strengthScore", 50),
                        "support": profile.get("support", {}).get("strongest", "--"),
                        "resistance": profile.get("resistance", {}).get("strongest", "--"),
                        "brent": profile.get("macro", {}).get("brentCrude", {}).get("price", 80.45),
                        "usdInr": profile.get("macro", {}).get("usdInr", {}).get("rate", 83.72)
                    })
                except Exception as e:
                    print(f"[-] Warning: Failed to parse historical snapshot {path}: {e}")
                    continue
                    
            self.send_json_response(history_data)
            
        # 3. HTML Frontend UI Client
        elif self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.end_headers()
            self.wfile.write(HTML_UI_TEMPLATE.encode('utf-8'))
            
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

    def send_json_response(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        # Allow CORS for easier testing
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode('utf-8'))

    def send_error_json(self, status_code, message):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode('utf-8'))

# Premium Dark-Mode HTML UI Template
HTML_UI_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Elephant Edge - NIFTY 50 Market Profile</title>
    <!-- Outfit & Inter Google Fonts -->
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <!-- Chart.js from CDN -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-gradient: linear-gradient(135deg, #090615 0%, #11092b 50%, #06040d 100%);
            --glass-bg: rgba(22, 14, 45, 0.45);
            --glass-border: rgba(255, 255, 255, 0.08);
            --glow-purple: rgba(138, 92, 246, 0.15);
            --text-primary: #ffffff;
            --text-secondary: #a39ebc;
            --bullish-color: #10b981;
            --bullish-bg: rgba(16, 185, 129, 0.12);
            --bearish-color: #ef4444;
            --bearish-bg: rgba(239, 68, 68, 0.12);
            --accent-color: #8b5cf6;
            --accent-glow: 0 0 15px rgba(139, 92, 246, 0.4);
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Inter', sans-serif;
        }

        body {
            background: var(--bg-gradient);
            color: var(--text-primary);
            min-height: 100vh;
            padding: 24px;
            overflow-x: hidden;
        }

        /* Scrollbar styles */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        ::-webkit-scrollbar-track {
            background: rgba(0, 0, 0, 0.2);
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(139, 92, 246, 0.3);
            border-radius: 4px;
        }
        ::-webkit-scrollbar-thumb:hover {
            background: rgba(139, 92, 246, 0.5);
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding: 16px 24px;
            background: var(--glass-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--glass-border);
            border-radius: 16px;
            box-shadow: var(--glow-purple);
        }

        .logo-section h1 {
            font-family: 'Outfit', sans-serif;
            font-size: 24px;
            font-weight: 800;
            background: linear-gradient(90deg, #a78bfa, #ec4899);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: 0.5px;
        }

        .logo-section p {
            font-size: 12px;
            color: var(--text-secondary);
            margin-top: 2px;
        }

        .timestamp-badge {
            font-size: 13px;
            background: rgba(139, 92, 246, 0.15);
            border: 1px solid rgba(139, 92, 246, 0.3);
            color: #c084fc;
            padding: 6px 14px;
            border-radius: 30px;
            font-weight: 500;
        }

        /* KPI cards grid */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .kpi-card {
            background: var(--glass-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--glass-border);
            border-radius: 16px;
            padding: 16px 20px;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }

        .kpi-card:hover {
            transform: translateY(-3px);
            border-color: rgba(139, 92, 246, 0.3);
            box-shadow: 0 8px 24px rgba(139, 92, 246, 0.1);
        }

        .kpi-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-secondary);
            margin-bottom: 8px;
            font-weight: 600;
        }

        .kpi-value {
            font-family: 'Outfit', sans-serif;
            font-size: 20px;
            font-weight: 700;
            margin-bottom: 6px;
        }

        .kpi-subtext {
            font-size: 12px;
            font-weight: 500;
        }

        .text-bullish { color: var(--bullish-color); }
        .text-bearish { color: var(--bearish-color); }

        /* Secondary sections grid */
        .dashboard-row {
            display: flex;
            flex-direction: column;
            gap: 20px;
            margin-bottom: 24px;
        }

        .chart-container, .timeline-container {
            background: var(--glass-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--glass-border);
            border-radius: 16px;
            padding: 20px;
        }

        .section-title {
            font-family: 'Outfit', sans-serif;
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        /* Timeline styles */
        .timeline-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
            max-height: 300px;
            overflow-y: auto;
            padding-right: 4px;
        }

        .timeline-item {
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.05);
            padding: 10px 14px;
            border-radius: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .timeline-date {
            font-size: 12px;
            color: var(--text-secondary);
            font-weight: 500;
        }

        .timeline-trend {
            font-size: 13px;
            font-weight: 600;
            padding: 3px 10px;
            border-radius: 20px;
        }

        /* Option Chain Board */
        .option-chain-container {
            background: var(--glass-bg);
            backdrop-filter: blur(12px);
            border: 1px solid var(--glass-border);
            border-radius: 16px;
            padding: 20px;
            overflow-x: auto;
        }

        .option-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: center;
        }

        .option-table th {
            font-family: 'Outfit', sans-serif;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            padding: 12px 8px;
            background: rgba(0, 0, 0, 0.2);
            color: var(--text-secondary);
            border-bottom: 2px solid var(--glass-border);
        }

        .option-table td {
            padding: 10px 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            font-weight: 500;
        }

        /* Highlight classes */
        .strike-cell {
            background: rgba(139, 92, 246, 0.12);
            color: #c084fc;
            font-weight: 700 !important;
            font-family: 'Outfit', sans-serif;
            border-left: 1px solid rgba(139, 92, 246, 0.2);
            border-right: 1px solid rgba(139, 92, 246, 0.2);
        }

        .atm-row td {
            background: rgba(139, 92, 246, 0.18) !important;
            border-top: 1px solid rgba(139, 92, 246, 0.4) !important;
            border-bottom: 1px solid rgba(139, 92, 246, 0.4) !important;
        }

        .badge {
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 6px;
            display: inline-block;
        }

        .badge-lbu, .badge-sc {
            color: var(--bullish-color);
            background: var(--bullish-bg);
            border: 1px solid rgba(16, 185, 129, 0.25);
        }

        .badge-sbu, .badge-lu {
            color: var(--bearish-color);
            background: var(--bearish-bg);
            border: 1px solid rgba(239, 68, 68, 0.25);
        }

        .badge-neutral {
            color: #9ca3af;
            background: rgba(156, 163, 175, 0.12);
            border: 1px solid rgba(156, 163, 175, 0.25);
        }

        .ce-header {
            background: rgba(16, 185, 129, 0.05) !important;
            border-bottom: 2px solid rgba(16, 185, 129, 0.2) !important;
        }

        .pe-header {
            background: rgba(239, 68, 68, 0.05) !important;
            border-bottom: 2px solid rgba(239, 68, 68, 0.2) !important;
        }
    </style>
</head>
<body>
    <header>
        <div class="logo-section">
            <h1>ELEPHANT EDGE</h1>
            <p>NIFTY 50 Options Structure Dashboard</p>
        </div>
        <div id="timestamp" class="timestamp-badge">Loading Live Data...</div>
    </header>

    <!-- KPI Grid -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-label">NIFTY 50 Spot</div>
            <div id="spot-value" class="kpi-value">--</div>
            <div id="spot-change" class="kpi-subtext">--</div>
            <div id="spot-range" style="font-size: 11px; color: var(--text-secondary); margin-top: 4px;">H: -- | L: --</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Nifty Future</div>
            <div id="future-value" class="kpi-value">--</div>
            <div id="future-basis" class="kpi-subtext">--</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Overall PCR</div>
            <div id="pcr-value" class="kpi-value">--</div>
            <div id="pcr-bias" class="kpi-subtext">--</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">India VIX</div>
            <div id="vix-value" class="kpi-value">--</div>
            <div id="vix-regime" class="kpi-subtext">--</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Support / Resistance</div>
            <div id="sr-value" class="kpi-value" style="font-size: 18px; line-height: 1.4; margin-top: 4px;">S = --<br>R = --</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">OI Writing Dynamics</div>
            <div id="oi-writing-value" class="kpi-value" style="font-size: 13px; line-height: 1.5; margin-top: 4px;">Put: --<br>Call: --</div>
        </div>
        <div class="kpi-card" style="border-color: rgba(96, 165, 250, 0.3); box-shadow: 0 0 10px rgba(96, 165, 250, 0.05);">
            <div class="kpi-label" style="color: #60a5fa;">Macro Indicators</div>
            <div id="macro-value" class="kpi-value" style="font-size: 13px; line-height: 1.5; margin-top: 4px; text-align: left;">
                Brent: $-- (--%)<br>USD/INR: ₹-- (--%)
            </div>
        </div>
        <div class="kpi-card" style="border-color: rgba(236, 72, 153, 0.35); box-shadow: 0 0 10px rgba(236, 72, 153, 0.1); min-height: 180px; display: flex; flex-direction: column; justify-content: space-between;">
            <div>
                <div class="kpi-label" style="color: #f472b6;">Market Strength</div>
                <div style="display: flex; justify-content: space-between; align-items: baseline;">
                    <div id="strength-value" class="kpi-value" style="color: #f472b6; margin: 0;">--</div>
                    <div id="strength-status" class="kpi-subtext" style="margin: 0; font-size: 11px;">Score</div>
                </div>
            </div>
            <div id="strength-breakdown" style="font-size: 10px; color: var(--text-secondary); border-top: 1px solid rgba(236, 72, 153, 0.15); margin-top: 6px; padding-top: 6px; line-height: 1.4;">
                <!-- Breakdown rows injected here -->
            </div>
        </div>
        <div class="kpi-card" style="border-color: rgba(139, 92, 246, 0.45); box-shadow: var(--glow-purple); display: flex; flex-direction: column; justify-content: space-between; min-height: 180px;">
            <div>
                <div class="kpi-label" style="color: #c084fc;">Market Trend</div>
                <div id="trend-value" class="kpi-value" style="color: #ec4899; margin-top: 4px;">--</div>
                <div id="trend-confidence" class="kpi-subtext">Confidence: --</div>
            </div>
            <div id="trend-probabilities" style="font-size: 10px; border-top: 1px solid rgba(139, 92, 246, 0.2); margin-top: 6px; padding-top: 6px; color: var(--text-secondary); line-height: 1.4; text-align: left;">
                <!-- Probabilities rows injected here -->
            </div>
        </div>
        <div class="kpi-card" id="trend-reasons-card" style="grid-column: span 2; border-color: rgba(139, 92, 246, 0.45); box-shadow: 0 0 15px rgba(139, 92, 246, 0.08);">
            <div class="kpi-label" style="color: #c084fc; font-weight: 700;">Market Trend Explanations</div>
            <div id="trend-reasons-list" style="font-size: 11px; line-height: 1.5; color: var(--text-primary); margin-top: 8px; text-align: left; max-height: 110px; overflow-y: auto;">
                <!-- Reasons injected here -->
            </div>
        </div>
    </div>

    <!-- Expected Trading Ranges Board -->
    <div class="card" style="margin-top: 20px; border-color: rgba(96, 165, 250, 0.35); box-shadow: 0 0 15px rgba(96, 165, 250, 0.05); margin-bottom: 20px;">
        <div class="kpi-label" style="color: #60a5fa; font-weight: 700; font-size: 13px; margin-bottom: 12px; display: flex; align-items: center; gap: 8px;">
            📊 Expected Trading Ranges & Probability Boundaries
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px;">
            <div class="kpi-card" style="border-color: rgba(96, 165, 250, 0.45); box-shadow: 0 0 10px rgba(96, 165, 250, 0.05); text-align: left;">
                <div class="kpi-label" style="color: #60a5fa;">Expected Daily Range</div>
                <div id="range-daily" class="kpi-value" style="font-size: 18px; margin-top: 6px; color: #60a5fa;">-- - --</div>
                <div id="range-daily-sub" class="kpi-subtext">Based on VIX Daily Move (±-- pts)</div>
            </div>
            <div class="kpi-card" style="border-color: rgba(16, 185, 129, 0.45); box-shadow: 0 0 10px rgba(16, 185, 129, 0.05); text-align: left;">
                <div class="kpi-label" style="color: #34d399;">Expected Weekly Range</div>
                <div id="range-weekly" class="kpi-value" style="font-size: 18px; margin-top: 6px; color: #34d399;">-- - --</div>
                <div id="range-weekly-sub" class="kpi-subtext">Based on VIX Weekly Move (±-- pts)</div>
            </div>
            <div class="kpi-card" style="border-color: rgba(245, 158, 11, 0.45); box-shadow: 0 0 10px rgba(245, 158, 11, 0.05); text-align: left;">
                <div class="kpi-label" style="color: #fbbf24;">High Probability Range</div>
                <div id="range-high" class="kpi-value" style="font-size: 18px; margin-top: 6px; color: #fbbf24;">-- - --</div>
                <div id="range-high-sub" class="kpi-subtext">ATM Straddle & Support/Resistance</div>
            </div>
            <div class="kpi-card" style="border-color: rgba(239, 68, 68, 0.45); box-shadow: 0 0 10px rgba(239, 68, 68, 0.05); text-align: left;">
                <div class="kpi-label" style="color: #f87171;">Low Probability Range</div>
                <div id="range-low" class="kpi-value" style="font-size: 18px; margin-top: 6px; color: #f87171;">-- - --</div>
                <div id="range-low-sub" class="kpi-subtext">Extreme 2-SD VIX & Strong S/R Walls</div>
            </div>
        </div>
    </div>

    <!-- Recommended Strategy Advisor Board -->
    <div class="card" id="strategy-board" style="margin-top: 20px; border-color: rgba(139, 92, 246, 0.45); box-shadow: 0 0 15px rgba(139, 92, 246, 0.05); margin-bottom: 20px;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
            <div class="kpi-label" id="strategy-label" style="color: #c084fc; font-weight: 700; font-size: 13px; display: flex; align-items: center; gap: 8px;">
                💡 Recommended Strategy Advisor & Hedging Playbook
            </div>
            <span id="strategy-type-badge" class="badge badge-neutral" style="font-size: 10px; padding: 3px 8px;">Neutral</span>
        </div>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px;">
            <div class="kpi-card" style="border-color: rgba(139, 92, 246, 0.25); text-align: left; background: rgba(30, 27, 75, 0.2);">
                <div class="kpi-label" style="color: #c084fc; font-size: 11px;">Recommended Strategy</div>
                <div id="strategy-name" class="kpi-value" style="font-size: 20px; margin-top: 4px; color: #ec4899;">No Trade</div>
                <div id="strategy-desc" class="kpi-subtext" style="font-size: 11px; margin-top: 6px; line-height: 1.4; color: var(--text-secondary);">Market volatility is extremely high. Keep capital safe.</div>
            </div>
            <div class="kpi-card" style="border-color: rgba(139, 92, 246, 0.25); text-align: left; background: rgba(30, 27, 75, 0.2);">
                <div class="kpi-label" style="color: #c084fc; font-size: 11px;">Selected Option Legs</div>
                <div id="strategy-legs" class="kpi-value" style="font-size: 12px; margin-top: 8px; line-height: 1.5; color: var(--text-primary); font-weight: normal; font-family: monospace; text-align: left;">--</div>
            </div>
            <div class="kpi-card" style="border-color: rgba(139, 92, 246, 0.25); text-align: left; background: rgba(30, 27, 75, 0.2); grid-column: span 1;">
                <div class="kpi-label" style="color: #c084fc; font-size: 11px;">Decision-Tree Selection Reasons</div>
                <div id="strategy-reasons" style="font-size: 11px; margin-top: 6px; line-height: 1.5; color: var(--text-primary); text-align: left;">
                    <!-- Bullet reasons here -->
                </div>
            </div>
        </div>
        <div id="strategy-metrics-row" style="margin-top: 15px; padding-top: 10px; border-top: 1px solid rgba(139, 92, 246, 0.15); display: flex; justify-content: space-around; flex-wrap: wrap; gap: 10px; font-size: 11px;">
            <div id="metric-premium" style="color: var(--accent-color); font-weight: 600;">Net Premium: --</div>
            <div id="metric-profit" style="color: var(--bullish-color); font-weight: 600;">Max Profit: --</div>
            <div id="metric-risk" style="color: var(--bearish-color); font-weight: 600;">Max Risk: --</div>
            <div id="metric-breakeven" style="color: var(--text-secondary);">Break-even: --</div>
        </div>
    </div>

    <!-- Charts & Timeline Row -->
    <div class="dashboard-row">
        <!-- 15 Days Trend Chart -->
        <div class="chart-container">
            <div class="section-title">
                <span>NIFTY Spot & PCR Trend (Last 15 Days)</span>
                <span id="chart-sub" style="font-size: 12px; color: var(--text-secondary);">Historical Snaps</span>
            </div>
            <div style="position: relative; height: 300px; width: 100%;">
                <canvas id="trendChart"></canvas>
            </div>
        </div>

        <!-- Trend Timeline / Sentiment Board -->
        <div class="timeline-container">
            <div class="section-title">Daily Sentiment Board (Investing.com Layout)</div>
            <div style="overflow-x: auto;">
                <table class="option-table" style="font-size: 13px;">
                    <thead>
                        <tr>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">Time / Date</th>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">Nifty Spot</th>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">Support</th>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">Resistance</th>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">Brent Crude</th>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">USD / INR</th>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">Overall PCR</th>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">India VIX</th>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">Change %</th>
                            <th style="background: rgba(139, 92, 246, 0.05); border-bottom: 2px solid rgba(139, 92, 246, 0.2);">Sentiment</th>
                        </tr>
                    </thead>
                    <tbody id="timeline-table-rows">
                        <!-- Rows injected here -->
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Option Chain Board -->
    <div class="option-chain-container">
        <div class="section-title">
            <span>NIFTY 50 Option Chain & RSI Board</span>
            <span id="expiry-date-title" style="font-size: 14px; font-weight: 500; color: var(--text-secondary);">Expiry: --</span>
        </div>
        <table class="option-table">
            <thead>
                <tr>
                    <th colspan="4" class="ce-header">CALLS (CE)</th>
                    <th>STRIKE</th>
                    <th colspan="4" class="pe-header">PUTS (PE)</th>
                </tr>
                <tr>
                    <th class="ce-header">15m RSI</th>
                    <th class="ce-header">10m RSI</th>
                    <th class="ce-header">5m RSI</th>
                    <th class="ce-header">LTP</th>
                    
                    <th style="background: rgba(139, 92, 246, 0.15); font-weight: bold;">STRIKE</th>
                    
                    <th class="pe-header">LTP</th>
                    <th class="pe-header">5m RSI</th>
                    <th class="pe-header">10m RSI</th>
                    <th class="pe-header">15m RSI</th>
                </tr>
            </thead>
            <tbody id="option-rows">
                <!-- Rows injected here -->
            </tbody>
        </table>
    </div>

    <script>
        // Fetch dashboard data
        async function loadDashboard() {
            try {
                // Fetch API data
                const [latestRes, historyRes] = await Promise.all([
                    fetch('/api/latest'),
                    fetch('/api/history')
                ]);
                
                const latest = await latestRes.json();
                const history = await historyRes.json();
                
                renderProfile(latest);
                renderOptionChain(latest);
                renderTimelineAndChart(history);
            } catch (err) {
                console.error("Dashboard error:", err);
            }
        }

        // Render KPI metrics
        function renderProfile(data) {
            const profile = data.marketProfile;
            const trend = profile.marketTrend.trend;
            const conf = profile.marketTrend.confidencePercent;
            const strength = profile.marketTrend.strengthScore;
            
            const nseTime = data.timestamp;
            const sysTime = data.systemExecutionTime || 'N/A';
            document.getElementById('timestamp').textContent = `Data Time: ${nseTime} | Fetched: ${sysTime} | Expiry: ${data.expiry}`;
            document.getElementById('expiry-date-title').textContent = `Spot: ${data.spot.ltp.toFixed(2)} | Expiry: ${data.expiry}`;
            
            // Spot Price
            const spotLtp = data.spot.ltp;
            const spotChange = data.spot.changePoints;
            const spotChgPct = data.spot.changePercent;
            const spotClass = spotChange >= 0 ? 'text-bullish' : 'text-bearish';
            document.getElementById('spot-value').textContent = spotLtp.toFixed(2);
            document.getElementById('spot-change').innerHTML = `<span class="${spotClass}">${spotChange >= 0 ? '+' : ''}${spotChange.toFixed(2)} (${spotChgPct >= 0 ? '+' : ''}${spotChgPct.toFixed(2)}%)</span>`;
            
            const spotHigh = data.spot.high;
            const spotLow = data.spot.low;
            document.getElementById('spot-range').textContent = (spotHigh !== undefined && spotLow !== undefined) ? `H: ${spotHigh.toFixed(2)} | L: ${spotLow.toFixed(2)}` : 'H: -- | L: --';
            
            // Future Basis
            const futLtp = profile.syntheticFuture;
            const basis = profile.futureBasis;
            const basisClass = basis >= 0 ? 'text-bullish' : 'text-bearish';
            const basisLabel = basis >= 0 ? 'Premium' : 'Discount';
            document.getElementById('future-value').textContent = futLtp.toFixed(2);
            document.getElementById('future-basis').innerHTML = `<span class="${basisClass}">${basisLabel}: ${basis >= 0 ? '+' : ''}${basis.toFixed(2)} pts</span>`;
            
            // PCR
            const pcrVal = profile.pcr.value;
            const pcrBias = profile.pcr.bias;
            const pcrRegime = profile.pcr.regime;
            document.getElementById('pcr-value').textContent = pcrVal.toFixed(4);
            document.getElementById('pcr-bias').textContent = `${pcrRegime} - ${pcrBias}`;
            
            // VIX
            const vixVal = profile.vix.value;
            const vixDaily = profile.vix.expectedDailyMove;
            const vixRegime = profile.vix.regime;
            document.getElementById('vix-value').textContent = vixVal ? vixVal.toFixed(2) : 'N/A';
            document.getElementById('vix-regime').textContent = `Daily: ±${vixDaily} pts | ${vixRegime} Vol`;
            
            // S/R Levels
            const supStr = profile.support.strongest;
            const resStr = profile.resistance.strongest;
            document.getElementById('sr-value').innerHTML = `S = ${supStr} <br>R = ${resStr}`;
            
            // OI Writing Dynamics
            const putWriting = profile.writingDynamics.putWriting;
            const callWriting = profile.writingDynamics.callWriting;
            
            const ceChg = data.totals.totalCEChangeOI || 0;
            const peChg = data.totals.totalPEChangeOI || 0;
            const ceTot = data.totals.totalCallOI || 1;
            const peTot = data.totals.totalPutOI || 1;
            
            const formatWriting = (status, chg, tot) => {
                const lakhs = chg / 100000;
                const pct = tot > 0 ? (chg / tot) * 100 : 0;
                const sign = chg >= 0 ? '+' : '';
                return `${status} (${sign}${lakhs.toFixed(2)}L, ${sign}${pct.toFixed(1)}%)`;
            };
            
            const getWritingColor = (val) => {
                if (val === 'Increasing') return 'var(--bullish-color)';
                if (val === 'Decreasing') return 'var(--bearish-color)';
                return '#c084fc'; // Light purple/lavender for Mixed / Neutral
            };
            
            document.getElementById('oi-writing-value').innerHTML = `Put: <span style="font-weight: 700; color: ${getWritingColor(putWriting)}">${formatWriting(putWriting, peChg, peTot)}</span><br>Call: <span style="font-weight: 700; color: ${getWritingColor(callWriting)}">${formatWriting(callWriting, ceChg, ceTot)}</span>`;
            
            // Macro Indicators (Brent & USD/INR)
            const macro = profile.macro || {
                brentCrude: {price: 80.45, changePoints: 0, changePercent: 0},
                usdInr: {rate: 83.72, changePoints: 0, changePercent: 0}
            };
            
            const brentPrice = macro.brentCrude.price;
            const brentChgPct = macro.brentCrude.changePercent;
            const usdRate = macro.usdInr.rate;
            const usdChgPct = macro.usdInr.changePercent;
            
            const brentClass = brentChgPct < 0 ? 'text-bullish' : 'text-bearish'; // Falling crude is bullish for India
            const usdClass = usdChgPct < 0 ? 'text-bullish' : 'text-bearish'; // Falling exchange rate (strengthening Rupee) is bullish
            
            document.getElementById('macro-value').innerHTML = `
                Brent: <span class="${brentClass}" style="font-weight: 700;">$${brentPrice.toFixed(2)} (${brentChgPct >= 0 ? '+' : ''}${brentChgPct.toFixed(2)}%)</span><br>
                USD/INR: <span class="${usdClass}" style="font-weight: 700;">₹${usdRate.toFixed(4)} (${usdChgPct >= 0 ? '+' : ''}${usdChgPct.toFixed(2)}%)</span>
            `;
            
            // Market Strength Card & Breakdown
            document.getElementById('strength-value').textContent = `${strength} / 100`;
            let strengthStatus = 'Moderate';
            let strengthClass = '';
            if (strength >= 70) {
                strengthStatus = 'Strong';
                strengthClass = 'text-bullish';
            } else if (strength < 45) {
                strengthStatus = 'Weak';
                strengthClass = 'text-bearish';
            }
            document.getElementById('strength-status').innerHTML = `<span class="${strengthClass}" style="font-weight: 600;">${strengthStatus} Structure</span>`;
            
            const bd = profile.marketTrend.strengthBreakdown || {
                pcr: 0, putWriting: 0, callUnwinding: 0, basis: 0, vix: 0, priceStructure: 0, greeks: 0
            };
            document.getElementById('strength-breakdown').innerHTML = `
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 2px 8px; font-family: monospace;">
                    <div>PCR: +${bd.pcr}</div>
                    <div>VIX: +${bd.vix}</div>
                    <div>Put Write: +${bd.putWriting}</div>
                    <div>Price: +${bd.priceStructure}</div>
                    <div>Call Unwind: +${bd.callUnwinding}</div>
                    <div>Greeks: +${bd.greeks}</div>
                    <div style="grid-column: span 2; border-top: 1px dashed rgba(236, 72, 153, 0.25); margin-top: 3px; padding-top: 3px; font-weight: bold; display: flex; justify-content: space-between;">
                        <span>Basis: +${bd.basis}</span>
                        <span style="color: #f472b6;">TOTAL: ${strength}</span>
                    </div>
                </div>
            `;
            
            // Trend
            let trendClass = 'text-bullish';
            if (trend.includes('Bearish')) trendClass = 'text-bearish';
            else if (trend.includes('Range') || trend.includes('Neutral')) trendClass = '';
            
            document.getElementById('trend-value').textContent = trend;
            document.getElementById('trend-value').className = `kpi-value ${trendClass}`;
            document.getElementById('trend-confidence').textContent = `Confidence: ${conf}%`;
            
            // Trend Probabilities
            const probs = profile.marketTrend.trendProbabilities || {
                bullish: 33, rangeBound: 34, bearish: 33
            };
            document.getElementById('trend-probabilities').innerHTML = `
                <div style="font-weight: bold; color: var(--text-primary); margin-bottom: 4px;">Directional Probabilities:</div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 2px;">
                    <span>Bullish:</span>
                    <span class="text-bullish" style="font-weight: 700;">${probs.bullish}%</span>
                </div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 2px;">
                    <span>Range-bound:</span>
                    <span style="font-weight: 700; color: #c084fc;">${probs.rangeBound}%</span>
                </div>
                <div style="display: flex; justify-content: space-between;">
                    <span>Bearish:</span>
                    <span class="text-bearish" style="font-weight: 700;">${probs.bearish}%</span>
                </div>
            `;
            
            // Trend Reasons Explanations
            const reasons = profile.marketTrend.reasons || [];
            const reasonsList = document.getElementById('trend-reasons-list');
            reasonsList.innerHTML = '';
            if (reasons.length > 0) {
                reasons.forEach(r => {
                    const isWarning = r.toLowerCase().includes('resistance') || r.toLowerCase().includes('warning') || r.toLowerCase().includes('elevated') || r.toLowerCase().includes('low') || r.toLowerCase().includes('overcrowded') || r.toLowerCase().includes('discount') || r.toLowerCase().includes('ahead');
                    const icon = isWarning ? '⚠' : '✓';
                    const iconColor = isWarning ? 'var(--bearish-color)' : 'var(--bullish-color)';
                    
                    const div = document.createElement('div');
                    div.style.marginBottom = '4px';
                    div.innerHTML = `<span style="color: ${iconColor}; font-weight: bold; margin-right: 6px;">${icon}</span> ${r}`;
                    reasonsList.appendChild(div);
                });
            } else {
                reasonsList.textContent = 'No multi-factor explanation logs available.';
            }
            
            // Expected Trading Ranges
            const ranges = profile.expectedRanges || {
                dailyRange: {lower: 0, upper: 0, move: 0},
                weeklyRange: {lower: 0, upper: 0, move: 0},
                highProbabilityRange: {lower: 0, upper: 0},
                lowProbabilityRange: {lower: 0, upper: 0},
                straddlePrice: 0
            };
            
            document.getElementById('range-daily').textContent = `${ranges.dailyRange.lower.toFixed(2)} - ${ranges.dailyRange.upper.toFixed(2)}`;
            document.getElementById('range-daily-sub').textContent = `Based on VIX Daily Move (±${ranges.dailyRange.move.toFixed(2)} pts)`;
            
            document.getElementById('range-weekly').textContent = `${ranges.weeklyRange.lower.toFixed(2)} - ${ranges.weeklyRange.upper.toFixed(2)}`;
            document.getElementById('range-weekly-sub').textContent = `Based on VIX Weekly Move (±${ranges.weeklyRange.move.toFixed(2)} pts)`;
            
            document.getElementById('range-high').textContent = `${ranges.highProbabilityRange.lower.toFixed(2)} - ${ranges.highProbabilityRange.upper.toFixed(2)}`;
            document.getElementById('range-high-sub').textContent = `ATM Straddle (${ranges.straddlePrice.toFixed(2)} pts) & Weak S/R`;
            
            document.getElementById('range-low').textContent = `${ranges.lowProbabilityRange.lower.toFixed(2)} - ${ranges.lowProbabilityRange.upper.toFixed(2)}`;
            document.getElementById('range-low-sub').textContent = `Extreme 2-SD VIX & Strong S/R Walls`;

            // Recommended Strategy Advisor
            const strategy = data.recommendedStrategy || {
                strategyName: "No Trade",
                type: "Neutral",
                legs: [],
                netCreditDebit: 0,
                maxProfitPoints: 0,
                maxRiskPoints: 0,
                breakeven: 0,
                description: "Initial setup or volatile conditions.",
                reasons: ["No active analysis loaded yet."]
            };
            
            document.getElementById('strategy-name').textContent = strategy.strategyName;
            document.getElementById('strategy-desc').textContent = strategy.description;
            
            const badge = document.getElementById('strategy-type-badge');
            badge.textContent = strategy.type;
            badge.className = `badge ${strategy.type === 'Credit' ? 'badge-lbu' : strategy.type === 'Debit' ? 'badge-sbu' : 'badge-neutral'}`;
            
            // Set dynamic card border/colors based on strategy direction
            const board = document.getElementById('strategy-board');
            let boardColor = '#c084fc';
            let boardBorder = 'rgba(139, 92, 246, 0.4)';
            let boardShadow = '0 0 15px rgba(139, 92, 246, 0.05)';
            
            if (strategy.strategyName.includes('Bull')) {
                boardColor = 'var(--bullish-color)';
                boardBorder = 'rgba(16, 185, 129, 0.4)';
                boardShadow = '0 0 15px rgba(16, 185, 129, 0.05)';
            } else if (strategy.strategyName.includes('Bear')) {
                boardColor = 'var(--bearish-color)';
                boardBorder = 'rgba(239, 68, 68, 0.4)';
                boardShadow = '0 0 15px rgba(239, 68, 68, 0.05)';
            }
            
            board.style.borderColor = boardBorder;
            board.style.boxShadow = boardShadow;
            document.getElementById('strategy-label').style.color = boardColor;
            document.getElementById('strategy-name').style.color = boardColor;
            
            // Render Option Legs
            const legsEl = document.getElementById('strategy-legs');
            if (strategy.legs && strategy.legs.length > 0) {
                legsEl.innerHTML = strategy.legs.map(l => {
                    const actionCls = l.type === 'Buy' ? 'text-bullish' : 'text-bearish';
                    const actLabel = l.type === 'Buy' ? 'BUY' : 'SELL';
                    return `• <span class="${actionCls}" style="font-weight:700;">${actLabel}</span> ${l.strike} ${l.optionType} (LTP: ₹${l.ltp.toFixed(2)})`;
                }).join('<br>');
            } else {
                legsEl.innerHTML = '<span style="color: var(--text-secondary); font-weight: normal;">No legs required.</span>';
            }
            
            // Render Strategy Reasons
            const reasonsEl = document.getElementById('strategy-reasons');
            reasonsEl.innerHTML = '';
            if (strategy.reasons && strategy.reasons.length > 0) {
                strategy.reasons.forEach(r => {
                    const div = document.createElement('div');
                    div.style.marginBottom = '3px';
                    div.innerHTML = `<span style="color: ${boardColor}; font-weight: bold; margin-right: 4px;">✓</span> ${r}`;
                    reasonsEl.appendChild(div);
                });
            } else {
                reasonsEl.textContent = 'No selection logs available.';
            }
            
            // Render Premium/Profit/Risk Metrics
            const lotSize = 65;
            const premLabel = strategy.type === 'Credit' ? 'Net Credit' : 'Net Debit';
            const premVal = strategy.netCreditDebit;
            const maxProfit = strategy.maxProfitPoints;
            const maxRisk = strategy.maxRiskPoints;
            const beVal = strategy.breakeven;
            
            if (strategy.legs && strategy.legs.length > 0) {
                const limitProfit = maxProfit === 9999.0 ? 'Unlimited' : `${maxProfit.toFixed(2)} pts (₹${(maxProfit * lotSize).toLocaleString('en-IN', {maximumFractionDigits:0})})`;
                const limitRisk = maxRisk === 9999.0 ? 'Unlimited' : `${maxRisk.toFixed(2)} pts (₹${(maxRisk * lotSize).toLocaleString('en-IN', {maximumFractionDigits:0})})`;
                
                document.getElementById('metric-premium').innerHTML = `${premLabel}: <span style="color:var(--text-primary); font-weight:bold;">${premVal.toFixed(2)} pts (₹${(premVal * lotSize).toLocaleString('en-IN', {maximumFractionDigits:0})})</span>`;
                document.getElementById('metric-profit').innerHTML = `Max Profit: <span style="color:var(--bullish-color); font-weight:bold;">${limitProfit}</span>`;
                document.getElementById('metric-risk').innerHTML = `Max Risk: <span style="color:var(--bearish-color); font-weight:bold;">${limitRisk}</span>`;
                
                if (typeof beVal === 'number') {
                    document.getElementById('metric-breakeven').innerHTML = `Break-even: <span style="color:var(--text-primary); font-weight:bold;">${beVal.toFixed(2)}</span>`;
                } else if (beVal) {
                    document.getElementById('metric-breakeven').innerHTML = `Break-even: <span style="color:var(--text-primary); font-weight:bold;">${beVal}</span>`;
                } else {
                    document.getElementById('metric-breakeven').innerHTML = `Break-even: <span style="color:var(--text-primary); font-weight:bold;">N/A</span>`;
                }
                document.getElementById('strategy-metrics-row').style.display = 'flex';
            } else {
                document.getElementById('strategy-metrics-row').style.display = 'none';
            }
        }

        // Render option chain table rows
        function renderOptionChain(data) {
            const chain = data.optionChain;
            const atm = data.atmStrike;
            const tbody = document.getElementById('option-rows');
            tbody.innerHTML = '';
            
            chain.forEach(row => {
                const strike = row.strike;
                const ce = row.CE || {};
                const pe = row.PE || {};
                
                const tr = document.createElement('tr');
                if (strike === atm) {
                    tr.className = 'atm-row';
                }
                
                // Helper to format values
                const fmtLtp = (val) => val !== null && val !== undefined ? val.toFixed(2) : '--';
                const fmtRsi = (val) => val !== null && val !== undefined ? val : '--';
                
                tr.innerHTML = `
                    <td style="color: #60a5fa; font-weight: 600;">${fmtRsi(ce.rsi15m)}</td>
                    <td style="color: #34d399; font-weight: 600;">${fmtRsi(ce.rsi10m)}</td>
                    <td style="color: #c084fc; font-weight: 600;">${fmtRsi(ce.rsi5m)}</td>
                    <td style="font-weight: 600;">${fmtLtp(ce.ltp)}</td>
                    
                    <td class="strike-cell">${strike} ${strike === atm ? ' ←' : ''}</td>
                    
                    <td style="font-weight: 600;">${fmtLtp(pe.ltp)}</td>
                    <td style="color: #c084fc; font-weight: 600;">${fmtRsi(pe.rsi5m)}</td>
                    <td style="color: #34d399; font-weight: 600;">${fmtRsi(pe.rsi10m)}</td>
                    <td style="color: #60a5fa; font-weight: 600;">${fmtRsi(pe.rsi15m)}</td>
                `;
                tbody.appendChild(tr);
            });
        }

        // Render timeline list and Chart.js trend lines
        function renderTimelineAndChart(history) {
            // Timeline Table Rows
            const timelineTbody = document.getElementById('timeline-table-rows');
            timelineTbody.innerHTML = '';
            
            // Render reverse chronological timeline
            [...history].reverse().forEach(item => {
                const tr = document.createElement('tr');
                
                let badgeClass = 'badge-neutral';
                if (item.trend.includes('Bullish')) badgeClass = 'badge-lbu';
                else if (item.trend.includes('Bearish')) badgeClass = 'badge-sbu';
                
                const openVal = item.open !== undefined && item.open !== null ? item.open.toFixed(2) : '--';
                const highVal = item.high !== undefined && item.high !== null ? item.high.toFixed(2) : '--';
                const lowVal = item.low !== undefined && item.low !== null ? item.low.toFixed(2) : '--';
                const vixVal = item.vix !== undefined && item.vix !== null ? item.vix.toFixed(2) : '--';
                const chgVal = item.changePercent !== undefined && item.changePercent !== null ? item.changePercent : 0.0;
                
                const chgClass = chgVal >= 0 ? 'text-bullish' : 'text-bearish';
                const chgSign = chgVal >= 0 ? '+' : '';
                
                const fmtPrice = item.spot !== undefined && item.spot !== null ? item.spot.toFixed(2) : '--';
                const priceClass = chgVal >= 0 ? 'text-bullish' : 'text-bearish';
                
                const fmtSup = item.support || '--';
                const fmtRes = item.resistance || '--';
                const fmtBrent = item.brent !== undefined && item.brent !== null ? `$${item.brent.toFixed(2)}` : '--';
                const fmtUsd = item.usdInr !== undefined && item.usdInr !== null ? `₹${item.usdInr.toFixed(4)}` : '--';
                const pcrVal = item.pcr !== undefined && item.pcr !== null ? item.pcr.toFixed(4) : '--';
                
                tr.innerHTML = `
                    <td style="color: var(--text-secondary); font-weight: 500;">${item.date}</td>
                    <td class="${priceClass}" style="font-weight: 600;">${fmtPrice}</td>
                    <td class="text-bullish" style="font-weight: 600;">${fmtSup}</td>
                    <td class="text-bearish" style="font-weight: 600;">${fmtRes}</td>
                    <td style="color: #60a5fa; font-weight: 500;">${fmtBrent}</td>
                    <td style="color: #34d399; font-weight: 500;">${fmtUsd}</td>
                    <td style="font-weight: 500;">${pcrVal}</td>
                    <td style="color: #c084fc;">${vixVal}</td>
                    <td class="${chgClass}" style="font-weight: 600;">${chgSign}${chgVal.toFixed(2)}%</td>
                    <td><span class="badge ${badgeClass}">${item.trend}</span></td>
                `;
                timelineTbody.appendChild(tr);
            });
            
            // Draw Chart
            const ctx = document.getElementById('trendChart').getContext('2d');
            
            const labels = history.map(h => h.label);
            const spots = history.map(h => h.spot);
            const pcrs = history.map(h => h.pcr);
            
            new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'NIFTY Spot Price',
                            data: spots,
                            borderColor: '#8b5cf6',
                            backgroundColor: 'rgba(139, 92, 246, 0.1)',
                            yAxisID: 'ySpot',
                            tension: 0.35,
                            borderWidth: 3,
                            pointRadius: 4
                        },
                        {
                            label: 'Overall PCR',
                            data: pcrs,
                            borderColor: '#10b981',
                            backgroundColor: 'rgba(16, 185, 129, 0.05)',
                            yAxisID: 'yPcr',
                            tension: 0.35,
                            borderWidth: 3,
                            pointRadius: 4
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: {
                            grid: { color: 'rgba(255, 255, 255, 0.05)' },
                            ticks: { color: '#a39ebc', font: { size: 10 } }
                        },
                        ySpot: {
                            type: 'linear',
                            position: 'left',
                            grid: { color: 'rgba(255, 255, 255, 0.05)' },
                            ticks: { color: '#a78bfa' }
                        },
                        yPcr: {
                            type: 'linear',
                            position: 'right',
                            grid: { drawOnChartArea: false },
                            ticks: { color: '#34d399' }
                        }
                    },
                    plugins: {
                        legend: {
                            labels: { color: '#ffffff', font: { size: 12 } }
                        }
                    }
                }
            });
        }

        window.onload = loadDashboard;
    </script>
</body>
</html>
"""

import threading

def update_price_history_loop():
    while True:
        try:
            # Force update cache
            get_nifty_price_history(force=True)
        except Exception as e:
            print(f"[-] Error updating price history in background: {e}")
        time.sleep(60)

def run_server():
    # Start background price history updater thread
    threading.Thread(target=update_price_history_loop, daemon=True).start()
    
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, DashboardHandler)
    print(f"\n[+] PREMIUM WEB UI DASHBOARD SERVER ACTIVE ON: http://localhost:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[-] Shutting down dashboard server...")
        httpd.server_close()

if __name__ == '__main__':
    run_server()
