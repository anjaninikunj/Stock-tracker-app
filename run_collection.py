import json
import sys
import config
from nse_client import NSEClient
from greeks_engine import process_greeks_for_row
from validator import validate_snapshot
from snapshot_storage import save_snapshot
from analytics_engine import (
    calculate_support_resistance,
    interpret_oi_buildup,
    get_pcr_interpretation,
    get_vix_analysis,
    calculate_trend_and_confidence,
    recommend_strategy,
    calculate_expected_ranges
)

# Reconfigure stdout for UTF-8 compatibility (especially on Windows consoles)
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

def run_collection_pipeline():
    """
    Orchestrates the entire Phase 2 pipeline:
    1. Fetches spot/VIX & option chain
    2. Filters strikes & processes greeks
    3. Runs analytics (S/R, build-ups, PCR/VIX expected moves, Trend Confidence)
    4. Normalizes data into the target schema containing Market Profile
    5. Validates the data snapshot
    6. Saves the snapshot to disk
    7. Outputs the premium Market Profile terminal dashboard
    """
    print("=" * 60)
    print(" NIFTY OI ANALYSIS AUTOMATION - COLLECTION PIPELINE")
    print("=" * 60)

    client = NSEClient()
    
    # 1. Fetch live quotes (Spot and VIX)
    try:
        market_data = client.get_market_data()
        spot_info = market_data["spot"]
        vix_info = market_data["vix"]
    except Exception as e:
        print(f"[-] Critical Error: Failed to fetch index quotes: {e}")
        return False

    # Fetch macro indicators (Brent Crude and USD/INR)
    try:
        macro_data = client.get_macro_indicators()
    except Exception as e:
        print(f"[-] Warning: Failed to fetch macro indicators: {e}")
        macro_data = {
            "brentCrude": {"price": 80.45, "changePoints": -0.32, "changePercent": -0.40},
            "usdInr": {"rate": 83.7200, "changePoints": 0.0400, "changePercent": 0.05}
        }

    # 2. Fetch option chain
    try:
        opt_data = client.get_option_chain()
    except Exception as e:
        print(f"[-] Critical Error: Failed to fetch option chain: {e}")
        return False

    # 3. Extract target expiry and filter strikes
    try:
        spot_price = spot_info["ltp"]
        expiry, atm_strike, selected_rows, timestamp = client.extract_expiry_and_strikes(opt_data, spot_price)
    except Exception as e:
        print(f"[-] Critical Error: Failed to extract strikes: {e}")
        return False

    # 4. Calculate total Call and Put OI, and Change in OI across the target expiry
    all_contracts = opt_data.get("records", {}).get("data", [])
    total_call_oi = 0
    total_put_oi = 0
    total_ce_change_oi = 0
    total_pe_change_oi = 0
    
    for contract in all_contracts:
        if contract.get("expiryDates") == expiry:
            ce = contract.get("CE")
            pe = contract.get("PE")
            if ce:
                total_call_oi += ce.get("openInterest", 0)
                total_ce_change_oi += ce.get("changeinOpenInterest", 0)
            if pe:
                total_put_oi += pe.get("openInterest", 0)
                total_pe_change_oi += pe.get("changeinOpenInterest", 0)
                
    calculated_pcr = round(total_put_oi / total_call_oi, 4) if total_call_oi > 0 else 0.0

    # 5. Process option chain rows and calculate Greeks
    vix_val = vix_info["value"] if vix_info else 12.0
    
    # Calculate Synthetic Future Price using ATM strike options
    atm_row = next((r for r in selected_rows if r.get("strikePrice") == atm_strike), None)
    synthetic_future = spot_price  # default fallback
    if atm_row:
        ce_raw = atm_row.get("CE", {})
        pe_raw = atm_row.get("PE", {})
        if ce_raw and pe_raw:
            ce_ltp = ce_raw.get("lastPrice")
            pe_ltp = pe_raw.get("lastPrice")
            if ce_ltp is not None and pe_ltp is not None:
                synthetic_future = float(atm_strike) + float(ce_ltp) - float(pe_ltp)
                print(f"[+] Calculated Synthetic Future Price: {synthetic_future:.2f} (Spot: {spot_price})")
                
    normalized_chain = []
    
    # We will track Support and Resistance from the selected strikes around ATM
    max_call_oi = -1
    resistance_strike = None
    max_put_oi = -1
    support_strike = None

    for row in selected_rows:
        strike = float(row.get("strikePrice"))
        ce_greeks, pe_greeks = process_greeks_for_row(row, synthetic_future, expiry, timestamp, vix_val)
        
        ce_raw = row.get("CE", {})
        pe_raw = row.get("PE", {})
        
        # Build Call details
        ce_normalized = None
        if ce_raw:
            ce_oi = ce_raw.get("openInterest", 0)
            if ce_oi > max_call_oi:
                max_call_oi = ce_oi
                resistance_strike = strike
                
            ce_change_pts = float(ce_raw.get("change", 0)) if ce_raw.get("change") else 0.0
            ce_change_oi_val = int(ce_raw.get("changeinOpenInterest", 0)) if ce_raw.get("changeinOpenInterest") is not None else 0
            ce_buildup = interpret_oi_buildup(ce_change_pts, ce_change_oi_val)
            
            ce_normalized = {
                "ltp": float(ce_raw.get("lastPrice", 0)) if ce_raw.get("lastPrice") else None,
                "oi": int(ce_oi) if ce_oi is not None else None,
                "changeOI": int(ce_raw.get("changeinOpenInterest", 0)) if ce_raw.get("changeinOpenInterest") is not None else None,
                "volume": int(ce_raw.get("totalTradedVolume", 0)) if ce_raw.get("totalTradedVolume") is not None else None,
                "iv": float(ce_raw.get("impliedVolatility", 0)) if ce_raw.get("impliedVolatility") else None,
                "delta": ce_greeks["delta"] if ce_greeks else None,
                "gamma": ce_greeks["gamma"] if ce_greeks else None,
                "theta": ce_greeks["theta"] if ce_greeks else None,
                "vega": ce_greeks["vega"] if ce_greeks else None,
                "buildup": ce_buildup
            }
            
        # Build Put details
        pe_normalized = None
        if pe_raw:
            pe_oi = pe_raw.get("openInterest", 0)
            if pe_oi > max_put_oi:
                max_put_oi = pe_oi
                support_strike = strike
                
            pe_change_pts = float(pe_raw.get("change", 0)) if pe_raw.get("change") else 0.0
            pe_change_oi_val = int(pe_raw.get("changeinOpenInterest", 0)) if pe_raw.get("changeinOpenInterest") is not None else 0
            pe_buildup = interpret_oi_buildup(pe_change_pts, pe_change_oi_val)
            
            pe_normalized = {
                "ltp": float(pe_raw.get("lastPrice", 0)) if pe_raw.get("lastPrice") else None,
                "oi": int(pe_oi) if pe_oi is not None else None,
                "changeOI": int(pe_raw.get("changeinOpenInterest", 0)) if pe_raw.get("changeinOpenInterest") is not None else None,
                "volume": int(pe_raw.get("totalTradedVolume", 0)) if pe_raw.get("totalTradedVolume") is not None else None,
                "iv": float(pe_raw.get("impliedVolatility", 0)) if pe_raw.get("impliedVolatility") else None,
                "delta": pe_greeks["delta"] if pe_greeks else None,
                "gamma": pe_greeks["gamma"] if pe_greeks else None,
                "theta": pe_greeks["theta"] if pe_greeks else None,
                "vega": pe_greeks["vega"] if pe_greeks else None,
                "buildup": pe_buildup
            }
            
        normalized_chain.append({
            "strike": int(strike),
            "CE": ce_normalized,
            "PE": pe_normalized
        })

    # 5b. Fetch previous snapshot for comparison
    from snapshot_storage import get_latest_snapshot_before
    prev_snapshot_path = get_latest_snapshot_before(timestamp)
    comparison = None
    if prev_snapshot_path:
        try:
            with open(prev_snapshot_path, "r", encoding="utf-8") as f:
                prev_snapshot = json.load(f)
                
            prev_spot = prev_snapshot["spot"]["ltp"]
            prev_pcr = prev_snapshot["pcr"]["oiPCR"]
            
            spot_change = spot_price - prev_spot
            pcr_change = calculated_pcr - prev_pcr
            
            # Sentiment analysis based on OI changes
            if total_pe_change_oi > total_ce_change_oi:
                if spot_change > 0:
                    sentiment = "STRONG BULLISH (Heavy Put Writing & Call Unwinding)"
                else:
                    sentiment = "BULLISH (Put Writing supporting from below)"
            elif total_ce_change_oi > total_pe_change_oi:
                if spot_change < 0:
                    sentiment = "STRONG BEARISH (Heavy Call Writing & Put Unwinding)"
                else:
                    sentiment = "BEARISH (Call Writing capping upside)"
            else:
                sentiment = "NEUTRAL / RANGEBOUND"
                
            comparison = {
                "previousSnapshot": prev_snapshot_path,
                "spotChangePoints": round(spot_change, 2),
                "pcrChange": round(pcr_change, 4),
                "sentiment": sentiment
            }
        except Exception as e:
            print(f"[-] Error parsing previous snapshot for comparison: {e}")
            comparison = {
                "previousSnapshot": None,
                "spotChangePoints": 0.0,
                "pcrChange": 0.0,
                "sentiment": "NEUTRAL / RANGEBOUND"
            }
    else:
        # Default sentiment if no previous snapshot exists
        if total_pe_change_oi > total_ce_change_oi:
            sentiment = "BULLISH (Put Writing bias)"
        elif total_ce_change_oi > total_pe_change_oi:
            sentiment = "BEARISH (Call Writing bias)"
        else:
            sentiment = "NEUTRAL / RANGEBOUND"
            
        comparison = {
            "previousSnapshot": None,
            "spotChangePoints": 0.0,
            "pcrChange": 0.0,
            "sentiment": sentiment
        }

    # 5c. Run Advanced Analytics Engines (Modules 1-5)
    basis = synthetic_future - spot_price
    sup_res = calculate_support_resistance(normalized_chain)
    pcr_desc, pcr_bias = get_pcr_interpretation(calculated_pcr)
    vix_analysis = get_vix_analysis(spot_price, vix_val)
    trend, confidence, market_strength, reasons, breakdown, trend_probs = calculate_trend_and_confidence(
        spot_price, basis, calculated_pcr, vix_val, total_ce_change_oi, total_pe_change_oi, comparison, normalized_chain, atm_strike
    )
    expected_ranges = calculate_expected_ranges(
        spot_price, vix_val, normalized_chain, atm_strike, sup_res
    )
    recommended_strat = recommend_strategy(
        trend, spot_price, atm_strike, basis, calculated_pcr, vix_val, sup_res, normalized_chain
    )

    # Build Consolidated Market Profile summary
    market_profile = {
        "spot": spot_price,
        "syntheticFuture": round(synthetic_future, 2),
        "futureBasis": round(basis, 2),
        "atmStrike": atm_strike,
        "expectedRanges": expected_ranges,
        "pcr": {
            "value": calculated_pcr,
            "regime": pcr_desc,
            "bias": pcr_bias
        },
        "vix": {
            "value": vix_val,
            "regime": vix_analysis["regime"],
            "expectedDailyMove": vix_analysis["expectedDailyMove"],
            "expectedWeeklyMove": vix_analysis["expectedWeeklyMove"]
        },
        "support": {
            "strongest": sup_res["strongSupport"],
            "weak": sup_res["weakSupport"]
        },
        "resistance": {
            "strongest": sup_res["strongResistance"],
            "weak": sup_res["weakResistance"]
        },
        "writingDynamics": {
            "putWriting": "Increasing" if total_pe_change_oi > total_ce_change_oi else "Mixed" if total_pe_change_oi > 0 else "Decreasing",
            "callWriting": "Increasing" if total_ce_change_oi > total_pe_change_oi else "Mixed" if total_ce_change_oi > 0 else "Decreasing"
        },
        "marketTrend": {
            "trend": trend,
            "confidencePercent": confidence,
            "strengthScore": market_strength,
            "reasons": reasons,
            "strengthBreakdown": breakdown,
            "trendProbabilities": trend_probs
        },
        "macro": macro_data
    }

    # Assemble normalized snapshot dictionary
    from datetime import datetime
    system_execution_time = datetime.now().strftime("%d-%b-%Y %H:%M:%S")

    snapshot = {
        "symbol": "NIFTY",
        "timestamp": timestamp,
        "systemExecutionTime": system_execution_time,
        "expiry": expiry,
        "spot": {
            "ltp": spot_price,
            "previousClose": spot_info["previousClose"],
            "open": spot_info["open"],
            "high": spot_info["high"],
            "low": spot_info["low"],
            "changePoints": spot_info["changePoints"],
            "changePercent": spot_info["changePercent"],
            "syntheticFuture": round(synthetic_future, 2)
        },
        "vix": {
            "value": vix_info["value"] if vix_info else None,
            "changePoints": vix_info["changePoints"] if vix_info else None,
            "changePercent": vix_info["changePercent"] if vix_info else None
        },
        "pcr": {
            "oiPCR": calculated_pcr,
            "calculated": True
        },
        "totals": {
            "totalCallOI": total_call_oi,
            "totalPutOI": total_put_oi,
            "totalCEChangeOI": total_ce_change_oi,
            "totalPEChangeOI": total_pe_change_oi
        },
        "comparison": comparison,
        "marketProfile": market_profile,
        "macro": macro_data,
        "recommendedStrategy": recommended_strat,
        "atmStrike": atm_strike,
        "optionChain": normalized_chain
    }

    # 6. Validate snapshot
    is_valid, error_reason = validate_snapshot(snapshot)
    if not is_valid:
        print(f"[-] Validation Failed: {error_reason}")
        return False
    print("[+] Validation Passed.")

    # 7. Save to disk
    save_path = save_snapshot(snapshot)

    # 8. Render Terminal Dashboard
    vix_display = "N/A"
    if vix_info:
        vix_display = f"{vix_info['value']} ({vix_info['changePoints']:+g} pts, {vix_info['changePercent']:+g}%)"
        
    basis_label = "Premium" if basis >= 0 else "Discount"

    print("\n" + "=" * 60)
    print(" MARKET PROFILE SUMMARY")
    print("=" * 60)
    print(f"Spot Price   : {spot_price:<9} ({spot_info['changePoints']:+g} points, {spot_info['changePercent']:+g}%)")
    print(f"ATM Strike   : {atm_strike:<9}")
    print(f"Future Basis : {synthetic_future:.2f} ({basis_label}: {basis:+.2f} points)")
    print(f"India VIX    : {vix_display:<15} (Regime: {vix_analysis['regime']})")
    print(f"Macro Data   : Brent Crude: ${macro_data['brentCrude']['price']:.2f} ({macro_data['brentCrude']['changePercent']:+g}%) | USD/INR: {macro_data['usdInr']['rate']:.4f} ({macro_data['usdInr']['changePercent']:+g}%)")
    print(f"Expected Move: Daily: ±{vix_analysis['expectedDailyMove']} pts | Weekly: ±{vix_analysis['expectedWeeklyMove']} pts")
    print(f"Overall PCR  : {calculated_pcr:<9} ({pcr_bias})")
    print(f"S/R Levels   : Support: {sup_res['strongSupport']} (Strong), {sup_res['weakSupport']} (Weak)")
    print(f"               Resistance: {sup_res['strongResistance']} (Strong), {sup_res['weakResistance']} (Weak)")
    print(f"OI Writing   : Put Writing: {market_profile['writingDynamics']['putWriting']} | Call Writing: {market_profile['writingDynamics']['callWriting']}")
    print(f"Market Trend : {trend:<14} (Confidence: {confidence}% | Strength Score: {market_strength}/100)")
    print(f"               Probabilities: Bullish: {trend_probs['bullish']}% | Range-bound: {trend_probs['rangeBound']}% | Bearish: {trend_probs['bearish']}%")
    print("               Probability Calculation Explanation:")
    print("                 * Bullish %     : Determined by rising spot price momentum, expanding PCR level, Put writing dominance, and bullish option chain build-ups.")
    print("                 * Bearish %     : Determined by overhead Call OI concentration, falling spot price, and Call writing dominance.")
    print("                 * Range-bound % : Driven by India VIX cooling (calm volatility regime) and neutral PCR levels.")
    print(f"               Breakdown: PCR +{breakdown['pcr']} | Put Writing +{breakdown['putWriting']} | Call Unwinding +{breakdown['callUnwinding']} | Basis +{breakdown['basis']} | VIX +{breakdown['vix']} | Price Structure +{breakdown['priceStructure']} | Greeks +{breakdown['greeks']} (TOTAL = {breakdown['total']})")
    print(f"Daily Range  : {expected_ranges['dailyRange']['lower']} - {expected_ranges['dailyRange']['upper']} (±{expected_ranges['dailyRange']['move']} pts)")
    print(f"Weekly Range : {expected_ranges['weeklyRange']['lower']} - {expected_ranges['weeklyRange']['upper']} (±{expected_ranges['weeklyRange']['move']} pts)")
    print(f"High Prob Rng: {expected_ranges['highProbabilityRange']['lower']} - {expected_ranges['highProbabilityRange']['upper']} [ATM Straddle: {expected_ranges['straddlePrice']} pts]")
    print(f"Low Prob Rng : {expected_ranges['lowProbabilityRange']['lower']} - {expected_ranges['lowProbabilityRange']['upper']} [Extreme S/R Boundaries]")
    print(f"Strategy Rec : {recommended_strat['strategyName']} ({recommended_strat['type']})")
    if recommended_strat["legs"]:
        legs_str = ", ".join([f"{l['type']} {l['strike']} {l['optionType']} (LTP: {l['ltp']})" for l in recommended_strat["legs"]])
        print(f"               Legs: {legs_str}")
        print(f"               Net Prem: {recommended_strat['netCreditDebit']} pts | Max Profit: {recommended_strat['maxProfitPoints']} pts | Max Risk: {recommended_strat['maxRiskPoints']} pts | BE: {recommended_strat['breakeven']}")
        print(f"               Reasons: {', '.join(recommended_strat['reasons'])}")
    else:
        print(f"               Details: {recommended_strat['description']}")
    print("Reasons:")
    for r in reasons:
        is_warning = any(w in r.lower() for w in ["resistance", "warning", "elevated", "low", "overcrowded", "discount", "ahead"])
        icon = "⚠" if is_warning else "✓"
        print(f"  {icon} {r}")
    if comparison['previousSnapshot']:
        print(f"Session Chg  : Spot = {comparison['spotChangePoints']:+.2f} pts, PCR = {comparison['pcrChange']:+.4f}")
    else:
        print("Session Chg  : Initial run (no comparison data yet)")
    print(f"Timestamp    : {timestamp} | Expiry: {expiry}")
    print("-" * 145)
    print(f"{'STRIKE':<6} | {'CE LTP':<8} | {'CE CHG OI':<10} | {'CE VOL':<10} | {'CE IV':<6} | {'CE DELTA':<8} | {'CE BUILD':<14} | {'PE LTP':<8} | {'PE CHG OI':<10} | {'PE VOL':<10} | {'PE IV':<6} | {'PE DELTA':<8} | {'PE BUILD':<14}")
    print("-" * 145)
    
    # Render preview of ATM +/- 3 strikes
    for row in normalized_chain:
        strike = row["strike"]
        if abs(strike - atm_strike) <= 150:
            ce = row["CE"]
            pe = row["PE"]
            ce_ltp = ce["ltp"] if ce else "N/A"
            ce_chg = ce["changeOI"] if ce else "N/A"
            ce_vol = ce["volume"] if ce else "N/A"
            ce_iv = ce["iv"] if ce else "N/A"
            ce_delta = ce["delta"] if ce else "N/A"
            ce_buildup = ce["buildup"] if ce else "N/A"
            
            pe_ltp = pe["ltp"] if pe else "N/A"
            pe_chg = pe["changeOI"] if pe else "N/A"
            pe_vol = pe["volume"] if pe else "N/A"
            pe_iv = pe["iv"] if pe else "N/A"
            pe_delta = pe["delta"] if pe else "N/A"
            pe_buildup = pe["buildup"] if pe else "N/A"
            
            marker = "<- ATM" if strike == atm_strike else ""
            
            ce_ltp_s = f"{ce_ltp:.2f}" if isinstance(ce_ltp, (int, float)) else str(ce_ltp)
            ce_chg_s = f"{ce_chg:+d}" if isinstance(ce_chg, int) else str(ce_chg)
            ce_vol_s = f"{ce_vol:,}" if isinstance(ce_vol, int) else str(ce_vol)
            ce_iv_s = f"{ce_iv:.1f}%" if isinstance(ce_iv, (int, float)) else str(ce_iv)
            ce_delta_s = f"{ce_delta:.2f}" if isinstance(ce_delta, (int, float)) else str(ce_delta)
            
            pe_ltp_s = f"{pe_ltp:.2f}" if isinstance(pe_ltp, (int, float)) else str(pe_ltp)
            pe_chg_s = f"{pe_chg:+d}" if isinstance(pe_chg, int) else str(pe_chg)
            pe_vol_s = f"{pe_vol:,}" if isinstance(pe_vol, int) else str(pe_vol)
            pe_iv_s = f"{pe_iv:.1f}%" if isinstance(pe_iv, (int, float)) else str(pe_iv)
            pe_delta_s = f"{pe_delta:.2f}" if isinstance(pe_delta, (int, float)) else str(pe_delta)
            
            print(f"{strike:<6} | {ce_ltp_s:<8} | {ce_chg_s:<10} | {ce_vol_s:<10} | {ce_iv_s:<6} | {ce_delta_s:<8} | {ce_buildup:<14} | {pe_ltp_s:<8} | {pe_chg_s:<10} | {pe_vol_s:<10} | {pe_iv_s:<6} | {pe_delta_s:<8} | {pe_buildup:<14} {marker}")
    print("=" * 145)
    
    # Dispatch Telegram Alert Summary
    import chartink_utils
    import os
    telegram_token = os.environ.get("NIFTY_TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "8890560111:AAFgExgQVny8lspqd8hMZxWGJFRHJxSUDtg")
    telegram_chat_id = os.environ.get("NIFTY_TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "811302410")

    tg_lines = [
        "📊 *NIFTY F&O Market Profile Alert*",
        f"Time: {timestamp} (Expiry: {expiry})",
        "",
        f"Spot Price   : {spot_price:.2f} ({spot_info['changePoints']:+g} pts, {spot_info['changePercent']:+g}%)",
        f"Future Basis : {synthetic_future:.2f} ({basis_label}: {basis:+.2f} pts)",
        f"India VIX    : {vix_display} [Regime: {vix_analysis['regime']}]",
        f"Overall PCR  : {calculated_pcr} ({pcr_bias})",
        f"Macro Drivers:",
        f"  • Brent Crude: ${macro_data['brentCrude']['price']:.2f} ({macro_data['brentCrude']['changePercent']:+g}%)",
        f"  • USD/INR    : {macro_data['usdInr']['rate']:.4f} ({macro_data['usdInr']['changePercent']:+g}%)",
        "",
        "S/R Levels:",
        f"  • Support: {sup_res['strongSupport']} (Strong), {sup_res['weakSupport']} (Weak)",
        f"  • Resistance: {sup_res['strongResistance']} (Strong), {sup_res['weakResistance']} (Weak)",
        "",
        "Expected Ranges:",
        f"  • Daily: {expected_ranges['dailyRange']['lower']} - {expected_ranges['dailyRange']['upper']} (±{expected_ranges['dailyRange']['move']} pts)",
        f"  • Weekly: {expected_ranges['weeklyRange']['lower']} - {expected_ranges['weeklyRange']['upper']} (±{expected_ranges['weeklyRange']['move']} pts)",
        f"  • High Prob: {expected_ranges['highProbabilityRange']['lower']} - {expected_ranges['highProbabilityRange']['upper']} [ATM Straddle: {expected_ranges['straddlePrice']} pts]",
        "",
        f"Trend State: *{trend}* (Confidence: {confidence}% | Strength: {market_strength}/100)",
        f"Probabilities: Bullish: {trend_probs['bullish']}% | Range-bound: {trend_probs['rangeBound']}% | Bearish: {trend_probs['bearish']}%",
        "",
        "Playbook Strategy:"
    ]
    if recommended_strat["strategyName"] != "No Trade":
        lot = 65
        tg_lines.append(f"  👉 *{recommended_strat['strategyName']}* ({recommended_strat['type']})")
        for l in recommended_strat["legs"]:
            tg_lines.append(f"    • {l['type']} {l['strike']} {l['optionType']} (LTP: {l['ltp']})")
        tg_lines.append(f"  Net Prem: {recommended_strat['netCreditDebit']} pts")
        tg_lines.append(f"  Max Profit: {recommended_strat['maxProfitPoints']} pts (₹{int(recommended_strat['maxProfitPoints']*lot)})")
        tg_lines.append(f"  Max Risk: {recommended_strat['maxRiskPoints']} pts (₹{int(recommended_strat['maxRiskPoints']*lot)})")
        tg_lines.append(f"  Break-even: {recommended_strat['breakeven']}")
    else:
        tg_lines.append(f"  👉 *No Trade* ({recommended_strat['description']})")

    tg_lines.append("")
    tg_lines.append("Trend Indicators:")
    for r in reasons:
        is_warning = any(w in r.lower() for w in ["resistance", "warning", "elevated", "low", "overcrowded", "discount", "ahead"])
        icon = "⚠" if is_warning else "✓"
        tg_lines.append(f"  {icon} {r}")

    tg_msg = "\n".join(tg_lines)
    print("[*] Dispatching options playbook alert to Telegram...")
    try:
        chartink_utils.send_telegram_alert(telegram_token, telegram_chat_id, tg_msg)
        print("[+] Telegram alert summary sent successfully.")
    except Exception as e:
        print(f"[-] Failed to send Telegram alert: {e}")

    return True

if __name__ == "__main__":
    run_collection_pipeline()

