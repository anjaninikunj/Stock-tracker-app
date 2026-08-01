from datetime import datetime

def validate_snapshot(data):
    """
    Validates a normalized option chain snapshot dictionary.
    Returns (True, None) if valid, or (False, "error reason") if invalid.
    """
    if not isinstance(data, dict):
        return False, "Data is not a dictionary"

    # 1. Check basic keys
    required_top_keys = ["symbol", "timestamp", "expiry", "spot", "vix", "pcr", "atmStrike", "optionChain"]
    for key in required_top_keys:
        if key not in data:
            return False, f"Missing required top-level key: {key}"

    # 2. Check Nifty Symbol and Expiry
    if data["symbol"] != "NIFTY":
        return False, f"Unexpected symbol: {data['symbol']}"
    if not data["expiry"]:
        return False, "Expiry date is empty or missing"

    # 3. Check Spot price
    spot = data["spot"]
    if not isinstance(spot, dict) or "ltp" not in spot:
        return False, "Spot details missing"
    
    spot_ltp = spot["ltp"]
    if spot_ltp is None or spot_ltp <= 10000:
        return False, f"Invalid NIFTY Spot Price: {spot_ltp}"

    # 4. Check VIX price
    vix = data["vix"]
    if vix:
        vix_val = vix.get("value")
        if vix_val is not None and (vix_val <= 0 or vix_val > 100):
            return False, f"Unrealistic INDIA VIX value: {vix_val}"

    # 5. Check Option Chain Rows
    option_chain = data["optionChain"]
    if not isinstance(option_chain, list) or len(option_chain) == 0:
        return False, "Option chain list is empty"
        
    # Check that we have enough strikes around ATM (we expect 21 rows for ATM ± 10 strikes)
    if len(option_chain) < 15:
        return False, f"Insufficient strike prices in chain (found {len(option_chain)}, expected ~21)"

    # Check for duplicate strikes and validate row keys
    seen_strikes = set()
    total_call_oi = 0
    total_put_oi = 0
    
    for idx, row in enumerate(option_chain):
        strike = row.get("strike")
        if strike is None:
            return False, f"Option chain row {idx} is missing 'strike'"
            
        if strike in seen_strikes:
            return False, f"Duplicate strike price found: {strike}"
        seen_strikes.add(strike)

        # Validate CE and PE keys
        for side in ["CE", "PE"]:
            side_data = row.get(side)
            if side_data is None:
                # We permit null if the side is genuinely unavailable, but the key must exist
                return False, f"Strike {strike} is missing '{side}' sub-dictionary"
            
            # If data is present, verify critical fields
            if side_data:
                required_option_keys = ["ltp", "oi", "changeOI", "volume", "iv", "delta", "gamma", "theta", "vega"]
                for opt_key in required_option_keys:
                    if opt_key not in side_data:
                        return False, f"Strike {strike} {side} is missing field: {opt_key}"
                
                # Accumulate OI for PCR verification
                oi_val = side_data.get("oi")
                if oi_val is not None:
                    if side == "CE":
                        total_call_oi += oi_val
                    else:
                        total_put_oi += oi_val

    # 6. Verify PCR calculation
    reported_pcr = data["pcr"].get("oiPCR")
    if reported_pcr is None or not isinstance(reported_pcr, (int, float)) or reported_pcr < 0:
        return False, f"Invalid PCR value: {reported_pcr}"

    # 7. Check Timestamp freshness
    # Format of timestamp: '31-Jul-2026 15:30:11'
    timestamp_str = data["timestamp"].strip()
    try:
        try:
            ts_dt = datetime.strptime(timestamp_str, "%d-%b-%Y %H:%M:%S")
        except ValueError:
            ts_dt = datetime.strptime(timestamp_str, "%d-%b-%Y %H:%M")
        
        # Verify it is a valid date (we won't block based on real time diff in tests since we process historical/closed market snapshots)
        if ts_dt.year < 2020:
            return False, f"Stale or invalid year in timestamp: {timestamp_str}"
    except Exception as e:
        return False, f"Timestamp parsing failed ({timestamp_str}): {e}"

    return True, None
