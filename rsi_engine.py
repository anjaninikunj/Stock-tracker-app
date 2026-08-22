import math
from datetime import datetime

def calculate_wilders_rsi(prices, period=14):
    """
    Calculates Wilder's RSI (Standard RSI) for a list of close prices.
    Matches TradingView/Zerodha Wilder's smoothing.
    """
    if len(prices) < period + 1:
        return None
        
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
            
    # Initial averages (simple average for first 'period' elements)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Wilder's smoothing for the rest
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
        
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return float(round(rsi, 2))

def resample_ticks(ticks, interval_minutes):
    """
    Resamples a list of dicts: [{'timestamp': 'dd-mmm-yyyy HH:MM:SS', 'price': float}]
    into standard clock-aligned close prices.
    """
    if not ticks:
        return []
        
    # Group ticks by standard clock boundaries (e.g. 5m, 10m, 15m)
    candles = {}
    for entry in ticks:
        ts_str = entry.get("timestamp")
        price = entry.get("price")
        if ts_str is None or price is None:
            continue
            
        try:
            # Parse timestamp
            ts_str = ts_str.strip()
            try:
                dt = datetime.strptime(ts_str, "%d-%b-%Y %H:%M:%S")
            except ValueError:
                dt = datetime.strptime(ts_str, "%d-%b-%Y %H:%M")
        except Exception:
            continue
            
        # Determine the start time of the candle
        # e.g., for 5m: 11:03 goes to 11:00 bar, 11:05 goes to 11:05 bar
        minute = dt.minute
        bar_start_minute = (minute // interval_minutes) * interval_minutes
        bar_start_time = dt.replace(minute=bar_start_minute, second=0, microsecond=0)
        
        # Keep the latest price in that interval as the close price
        # Ticks are naturally in chronological order, so overwrite keeps the latest
        candles[bar_start_time] = price
        
    # Sort candles and extract the close prices
    sorted_times = sorted(candles.keys())
    return [candles[t] for t in sorted_times]

def calculate_rsi_for_ticks(ticks, interval_minutes, period=14):
    """
    Resamples ticks and calculates Wilder's RSI.
    """
    close_prices = resample_ticks(ticks, interval_minutes)
    return calculate_wilders_rsi(close_prices, period)
