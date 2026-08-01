import math

def calculate_support_resistance(selected_rows):
    """
    Module 1: Support / Resistance Engine
    Identifies Strong/Weak Support and Strong/Weak Resistance from selected strikes.
    """
    # Sort by Put OI descending to find Support
    pe_sorted = sorted(selected_rows, key=lambda x: x.get("PE", {}).get("oi", 0) if x.get("PE") else 0, reverse=True)
    # Sort by Call OI descending to find Resistance
    ce_sorted = sorted(selected_rows, key=lambda x: x.get("CE", {}).get("oi", 0) if x.get("CE") else 0, reverse=True)

    strong_support = None
    weak_support = None
    strong_resistance = None
    weak_resistance = None

    if pe_sorted:
        strong_support = pe_sorted[0].get("strike")
        # Find a different strike for weak support
        for row in pe_sorted[1:]:
            if row.get("strike") != strong_support:
                weak_support = row.get("strike")
                break
        if weak_support is None:
            weak_support = strong_support

    if ce_sorted:
        strong_resistance = ce_sorted[0].get("strike")
        # Find a different strike for weak resistance
        for row in ce_sorted[1:]:
            if row.get("strike") != strong_resistance:
                weak_resistance = row.get("strike")
                break
        if weak_resistance is None:
            weak_resistance = strong_resistance

    return {
        "strongSupport": strong_support,
        "weakSupport": weak_support,
        "strongResistance": strong_resistance,
        "weakResistance": weak_resistance
    }

def interpret_oi_buildup(change_price, change_oi):
    """
    Module 2: OI Interpretation (LBU/SBU/LU/SC)
    Calculates positioning build-ups based on price change and OI change.
    """
    if change_price is None or change_oi is None:
        return "Neutral"
        
    if change_oi > 0:
        if change_price > 0:
            return "Long Build-up"
        elif change_price < 0:
            return "Short Build-up"
    elif change_oi < 0:
        if change_price < 0:
            return "Long Unwinding"
        elif change_price > 0:
            return "Short Covering"
            
    return "Neutral"

def get_pcr_interpretation(pcr_value):
    """
    Module 3: PCR Engine
    Converts raw PCR value to qualitative bias levels.
    """
    if pcr_value is None:
        return "Neutral", "Neutral Bias"
        
    if pcr_value < 0.55:
        return "PCR Low", "Extremely Bearish / Oversold"
    elif pcr_value < 0.80:
        return "PCR Medium-Low", "Bearish Bias"
    elif pcr_value < 1.15:
        return "PCR Neutral", "Neutral Bias"
    elif pcr_value < 1.40:
        return "PCR High", "Bullish Bias"
    else:
        return "PCR High", "Bullish Bias | But Overcrowded"

def get_vix_analysis(spot, vix_value):
    """
    Module 4: VIX Engine
    Calculates expected daily/weekly moves and the Volatility Regime.
    """
    if vix_value is None or vix_value <= 0:
        return {
            "regime": "Low",
            "expectedDailyMove": 0.0,
            "expectedWeeklyMove": 0.0
        }
        
    # Volatility Regime
    if vix_value < 12.0:
        regime = "Low"
    elif vix_value < 18.0:
        regime = "Medium"
    else:
        regime = "High"
        
    # Expected daily move: Spot * (VIX / 100) / sqrt(365)
    daily_move = spot * (vix_value / 100.0) / math.sqrt(365)
    # Expected weekly move: Spot * (VIX / 100) / sqrt(52)
    weekly_move = spot * (vix_value / 100.0) / math.sqrt(52)
    
    return {
        "regime": regime,
        "expectedDailyMove": round(daily_move, 2),
        "expectedWeeklyMove": round(weekly_move, 2)
    }

def calculate_trend_and_confidence(spot, basis, pcr, vix, ce_change_oi, pe_change_oi, prev_comp, normalized_chain, atm_strike):
    """
    Module 5: Expert Trend & Market Strength Engine
    Combines Spot, Future Basis, PCR, VIX, absolute OI, change in OI, build-up, and ATM structure
    using a weighted scoring model to output:
    - One of 8 specific trend states
    - Trend Confidence (%)
    - Market Strength Score (0-100)
    - Detailed Reasons list (Why)
    """
    import json
    score = 0
    
    # 1. Spot Price Momentum (from prev Close)
    spot_chg_pct = 0.0
    if prev_comp and prev_comp.get("spotChangePoints") is not None:
        spot_change = prev_comp.get("spotChangePoints", 0)
        prev_spot = spot - spot_change
        spot_chg_pct = (spot_change / prev_spot * 100) if prev_spot > 0 else 0
        
        if spot_chg_pct > 0.4:
            score += 2
        elif spot_chg_pct > 0.15:
            score += 1
        elif spot_chg_pct < -0.4:
            score -= 2
        elif spot_chg_pct < -0.15:
            score -= 1

    # 2. Future Basis
    if basis > 10.0:
        score += 1
    elif basis < -20.0:
        score -= 1

    # 3. PCR Level and PCR Change
    if pcr >= 1.4:
        score += 1
    elif pcr >= 1.15:
        score += 2
    elif pcr >= 0.55:
        score -= 2
    elif pcr < 0.55:
        score -= 1
        
    if prev_comp and prev_comp.get("pcrChange") is not None:
        pcr_chg = prev_comp.get("pcrChange", 0)
        if pcr_chg > 0.04:
            score += 1
        elif pcr_chg < -0.04:
            score -= 1

    # 4. India VIX Volatility Factor
    if vix:
        if vix < 12.5:
            score += 1
        elif vix > 16.5:
            score -= 1
            
    # 5. Net Change in Call vs Put OI (Intraday Writing)
    if pe_change_oi > 0 and ce_change_oi > 0:
        if pe_change_oi > 1.5 * ce_change_oi:
            score += 2
        elif pe_change_oi > ce_change_oi:
            score += 1
        elif ce_change_oi > 1.5 * pe_change_oi:
            score -= 2
        elif ce_change_oi > pe_change_oi:
            score -= 1
            
    # 6. Build-up Classification across Strikes
    ce_lbu_sc = 0
    pe_lbu_sc = 0
    for row in normalized_chain:
        ce_b = row.get("CE", {}).get("buildup", "") if row.get("CE") else ""
        pe_b = row.get("PE", {}).get("buildup", "") if row.get("PE") else ""
        if ce_b in ["Long Build-up", "Short Covering"]:
            ce_lbu_sc += 1
        if pe_b in ["Long Build-up", "Short Covering"]:
            pe_lbu_sc += 1
            
    if pe_lbu_sc > ce_lbu_sc + 1:
        score += 1
    elif ce_lbu_sc > pe_lbu_sc + 1:
        score -= 1

    # 7. ATM Structure
    atm_row = next((r for r in normalized_chain if r.get("strike") == int(atm_strike)), None)
    if atm_row:
        ce_b = atm_row.get("CE", {}).get("buildup", "") if atm_row.get("CE") else ""
        pe_b = atm_row.get("PE", {}).get("buildup", "") if atm_row.get("PE") else ""
        if pe_b == "Short Build-up" and ce_b != "Short Build-up":
            score += 1
        elif ce_b == "Short Build-up" and pe_b != "Short Build-up":
            score -= 1

    # 8. S/R Resistance Barriers
    max_ce_oi = 0
    max_ce_strike = None
    for row in normalized_chain:
        ce_oi = row.get("CE", {}).get("oi", 0) if row.get("CE") else 0
        if ce_oi > max_ce_oi:
            max_ce_oi = ce_oi
            max_ce_strike = row["strike"]

    # ==================== REASONING LOG GENERATOR ====================
    reasons = []

    # Reason 1: PCR change
    if prev_comp and prev_comp.get("pcrChange") is not None:
        pcr_chg = prev_comp["pcrChange"]
        if pcr_chg > 0:
            reasons.append("PCR increased")
        else:
            reasons.append("PCR decreased")
    else:
        if pcr >= 1.15:
            reasons.append("PCR in bullish zone")
        else:
            reasons.append("PCR in bearish zone")

    # Reason 2: Put Writing change
    if pe_change_oi > 0:
        reasons.append("Put Writing increased")
    else:
        reasons.append("Put Writing decreased")

    # Reason 3: VIX change compared to previous session
    if prev_comp and prev_comp.get("previousSnapshot"):
        try:
            with open(prev_comp["previousSnapshot"], "r", encoding="utf-8") as f:
                prev_snap = json.load(f)
                prev_vix = prev_snap.get("vix", {}).get("value")
                if prev_vix is not None:
                    if vix < prev_vix:
                        reasons.append("VIX decreased")
                    else:
                        reasons.append("VIX increased")
                else:
                    reasons.append("VIX is stable")
        except Exception:
            reasons.append("VIX is stable")
    else:
        reasons.append("VIX is stable")

    # Reason 4: Call OI reduction / Call Unwinding
    unwound_calls = sum(abs(row["CE"]["changeOI"]) for row in normalized_chain if row.get("CE") and row["CE"].get("changeOI", 0) < 0)
    if unwound_calls > 50000 or ce_change_oi < 0:
        reasons.append("Call OI reduced")
    else:
        reasons.append("Call OI increased")

    # Reason 5: Resistance
    if max_ce_strike:
        reasons.append(f"Resistance at {int(max_ce_strike)}")

    # Reason 6: Overhead Call OI warning & Confidence Penalty
    confidence_penalty = 0
    if max_ce_strike and spot < max_ce_strike <= spot + 200:
        reasons.append("Confidence reduced due to overhead Call OI")
        confidence_penalty = 10

    # ==================== OUTPUT CLASSIFICATION ====================
    # Map total score (max positive is around +10, max negative is around -10) to 8 states:
    if score >= 7:
        trend = "Strong Bullish"
    elif score >= 4:
        trend = "Bullish"
    elif score >= 1:
        trend = "Mild Bullish"
    elif score == 0:
        # Check if PCR is neutral
        if 0.95 <= pcr <= 1.05:
            trend = "Neutral"
        else:
            trend = "Range-bound"
    elif score >= -2:
        trend = "Mild Bearish"
    elif score >= -5:
        trend = "Bearish"
    else:
        trend = "Strong Bearish"

    # Confidence calculation scaled (between 50% and 95%) and penalize if overhead resistance exists
    max_possible_score = 11.0
    confidence_ratio = min(1.0, abs(score) / max_possible_score)
    confidence = int(50 + (confidence_ratio * 45))
    confidence = max(50, confidence - confidence_penalty)
    
    # Calculate transparent F&O Market Strength Score breakdown (out of 100)
    pcr_score = min(20, max(0, int(pcr * 12.0)))
    
    put_writing_score = 0
    if pe_change_oi > 0:
        put_writing_score = min(15, max(1, int((pe_change_oi / 100000) * 1.8)))
        
    call_unwinding_score = 0
    if unwound_calls > 0:
        call_unwinding_score = min(10, max(1, int((unwound_calls / 100000) * 8.0)))
        
    basis_score = min(10, max(0, int((basis + 25) / 50 * 10)))
    vix_score = min(15, max(0, int((22 - vix) / 10 * 15))) if vix else 7
    
    price_structure_score = 5
    if prev_comp and prev_comp.get("spotChangePoints") is not None:
        spot_change = prev_comp.get("spotChangePoints", 0)
        if spot_change > 0:
            price_structure_score += 5
        if spot_change > 50:
            price_structure_score += 5
            
    greeks_score = 5
    if vix and vix < 13.0:
        greeks_score += 5
    atm_row = next((r for r in normalized_chain if r.get("strike") == int(atm_strike)), None)
    if atm_row and atm_row.get("CE") and atm_row["CE"].get("delta") is not None:
        atm_delta = abs(atm_row["CE"]["delta"])
        if 0.4 <= atm_delta <= 0.5:
            greeks_score += 5
            
    market_strength = pcr_score + put_writing_score + call_unwinding_score + basis_score + vix_score + price_structure_score + greeks_score
    market_strength = min(100, max(0, market_strength))

    breakdown = {
        "pcr": pcr_score,
        "putWriting": put_writing_score,
        "callUnwinding": call_unwinding_score,
        "basis": basis_score,
        "vix": vix_score,
        "priceStructure": price_structure_score,
        "greeks": greeks_score,
        "total": market_strength
    }

    # Calculate Directional Probabilities (softmax-like normalization)
    s_bull = 10.0
    s_bear = 10.0
    s_range = 10.0

    # 1. PCR Influence
    if pcr >= 1.15:
        s_bull += 30.0
        s_range += 10.0
    elif pcr < 0.85:
        s_bear += 30.0
        s_range += 10.0
    else:
        s_range += 30.0
        s_bull += 5.0
        s_bear += 5.0

    # 2. OI Writing Influence
    if pe_change_oi > ce_change_oi:
        ratio = pe_change_oi / max(1, ce_change_oi)
        if ratio > 1.5:
            s_bull += 25.0
        else:
            s_bull += 15.0
            s_range += 10.0
    elif ce_change_oi > pe_change_oi:
        ratio = ce_change_oi / max(1, pe_change_oi)
        if ratio > 1.5:
            s_bear += 25.0
        else:
            s_bear += 15.0
            s_range += 10.0
    else:
        s_range += 20.0

    # 3. VIX Influence
    if vix:
        if vix < 13.0:
            s_bull += 15.0
            s_range += 15.0
        elif vix > 17.0:
            s_bear += 15.0
            s_range += 5.0
        else:
            s_range += 20.0
            s_bull += 5.0

    # 4. Spot Price Influence
    if prev_comp and prev_comp.get("spotChangePoints") is not None:
        spot_change = prev_comp["spotChangePoints"]
        if spot_change > 50:
            s_bull += 20.0
        elif spot_change > 0:
            s_bull += 10.0
            s_range += 5.0
        elif spot_change < -50:
            s_bear += 20.0
        elif spot_change < 0:
            s_bear += 10.0
            s_range += 5.0
    else:
        s_range += 15.0

    # 5. Build-up / ATM structure influence
    if pe_lbu_sc > ce_lbu_sc:
        s_bull += 15.0
    elif ce_lbu_sc > pe_lbu_sc:
        s_bear += 15.0
    else:
        s_range += 15.0

    # Normalize to 100%
    total_s = s_bull + s_bear + s_range
    p_bull = int(round((s_bull / total_s) * 100))
    p_bear = int(round((s_bear / total_s) * 100))
    p_range = 100 - (p_bull + p_bear)

    trend_probs = {
        "bullish": p_bull,
        "rangeBound": p_range,
        "bearish": p_bear
    }

    return trend, confidence, market_strength, reasons, breakdown, trend_probs

def recommend_strategy(trend, spot, atm_strike, basis, pcr, vix, sup_res, normalized_chain):
    """
    Module 6: Redesigned Options Strategy Recommendation Engine
    Implements a decision-tree matching Trend, Volatility, Expected Move, and Risk
    to choose from 8 strategies, and output detailed selection reasons.
    """
    import math

    # Helper to find LTP of a strike and option type
    def get_ltp(strike, opt_type):
        for row in normalized_chain:
            if row.get("strike") == int(strike):
                opt = row.get(opt_type)
                if opt and opt.get("ltp") is not None:
                    return opt.get("ltp")
        return None

    strategy_name = "No Trade"
    strat_type = "Neutral"
    legs = []
    net_premium = 0.0
    max_profit = 0.0
    max_risk = 0.0
    breakeven = 0.0
    reasons = []
    description = "No strategy recommended due to volatile market conditions or high risk."

    strong_sup = sup_res.get("strongSupport", atm_strike - 200)
    strong_res = sup_res.get("strongResistance", atm_strike + 200)
    weak_sup = sup_res.get("weakSupport", atm_strike - 100)
    weak_res = sup_res.get("weakResistance", atm_strike + 100)

    # 1. Volatility Regime
    vol_regime = "Normal"
    if vix < 13.0:
        vol_regime = "Low"
    elif vix > 17.0:
        vol_regime = "High"

    # 2. Calculate ATM Straddle price
    atm_ce = get_ltp(atm_strike, "CE") or 0.0
    atm_pe = get_ltp(atm_strike, "PE") or 0.0
    straddle_price = atm_ce + atm_pe

    # 3. Expected Move Size
    daily_move = spot * ((vix / 100.0) / math.sqrt(365))
    expected_move_size = "Large" if daily_move > 130.0 else "Small"

    # Check if resistance/support is too close, limiting the expected move
    dist_to_res = strong_res - spot
    dist_to_sup = spot - strong_sup
    
    # Decision Tree Selector
    if vix >= 25.0:
        strategy_name = "No Trade"
        strat_type = "Neutral"
        reasons = ["Extreme VIX (>25)", "Unpredictable market swings", "Risk of high slippage and volatility expansion"]
        description = "Market volatility is extremely high. Recommend sitting in cash to protect capital."
        
    elif trend in ["Strong Bullish", "Bullish", "Mild Bullish"]:
        # If VIX is high or expected move is too small to justify paying premium, use Bull Put Spread (Credit)
        if vol_regime == "High" or expected_move_size == "Small" or dist_to_res < 150.0:
            # Bull Put Spread (Credit)
            sell_strike = atm_strike
            buy_strike = atm_strike - 100
            
            sell_ltp = get_ltp(sell_strike, "PE")
            buy_ltp = get_ltp(buy_strike, "PE")
            
            if sell_ltp is not None and buy_ltp is not None:
                strategy_name = "Bull Put Spread"
                strat_type = "Credit"
                net_premium = round(sell_ltp - buy_ltp, 2)
                max_profit = net_premium
                max_risk = round(100.0 - net_premium, 2)
                breakeven = round(sell_strike - net_premium, 2)
                legs = [
                    {"type": "Sell", "optionType": "PE", "strike": int(sell_strike), "ltp": sell_ltp},
                    {"type": "Buy", "optionType": "PE", "strike": int(buy_strike), "ltp": buy_ltp}
                ]
                reasons = [
                    "Bullish trend detected",
                    f"Low/moderate expected move ({daily_move:.1f} pts) or high VIX",
                    "Support holding firmly",
                    "Limited upside expected (Avoid paying premium)"
                ]
                description = f"Bullish credit spread. Earns premium if Nifty stays above {sell_strike} support."
        else:
            # Bull Call Spread (Debit)
            buy_strike = atm_strike
            sell_strike = strong_res if (strong_res - buy_strike >= 100) else buy_strike + 100
            
            buy_ltp = get_ltp(buy_strike, "CE")
            sell_ltp = get_ltp(sell_strike, "CE")
            
            if buy_ltp is not None and sell_ltp is not None:
                strategy_name = "Bull Call Spread"
                strat_type = "Debit"
                net_premium = round(buy_ltp - sell_ltp, 2)
                max_profit = round((sell_strike - buy_strike) - net_premium, 2)
                max_risk = net_premium
                breakeven = round(buy_strike + net_premium, 2)
                legs = [
                    {"type": "Buy", "optionType": "CE", "strike": int(buy_strike), "ltp": buy_ltp},
                    {"type": "Sell", "optionType": "CE", "strike": int(sell_strike), "ltp": sell_ltp}
                ]
                reasons = [
                    "Strong Bullish trend",
                    f"Significant expected move ({daily_move:.1f} pts)",
                    "Low/Normal VIX (Cheap premiums)",
                    "Upside resistance is far away"
                ]
                description = f"Bullish debit spread. Capitalizes on fast directional move up toward {sell_strike}."

    elif trend in ["Strong Bearish", "Bearish", "Mild Bearish"]:
        # If VIX is high or expected move is small, use Bear Call Spread (Credit)
        if vol_regime == "High" or expected_move_size == "Small" or dist_to_sup < 150.0:
            # Bear Call Spread (Credit)
            sell_strike = atm_strike
            buy_strike = atm_strike + 100
            
            sell_ltp = get_ltp(sell_strike, "CE")
            buy_ltp = get_ltp(buy_strike, "CE")
            
            if sell_ltp is not None and buy_ltp is not None:
                strategy_name = "Bear Call Spread"
                strat_type = "Credit"
                net_premium = round(sell_ltp - buy_ltp, 2)
                max_profit = net_premium
                max_risk = round(100.0 - net_premium, 2)
                breakeven = round(sell_strike + net_premium, 2)
                legs = [
                    {"type": "Sell", "optionType": "CE", "strike": int(sell_strike), "ltp": sell_ltp},
                    {"type": "Buy", "optionType": "CE", "strike": int(buy_strike), "ltp": buy_ltp}
                ]
                reasons = [
                    "Bearish trend detected",
                    f"Low/moderate expected move ({daily_move:.1f} pts) or high VIX",
                    "Resistance holding firmly",
                    "Limited downside expected (Avoid paying premium)"
                ]
                description = f"Bearish credit spread. Earns premium if Nifty stays below {sell_strike} resistance."
        else:
            # Bear Put Spread (Debit)
            buy_strike = atm_strike
            sell_strike = strong_sup if (buy_strike - strong_sup >= 100) else buy_strike - 100
            
            buy_ltp = get_ltp(buy_strike, "PE")
            sell_ltp = get_ltp(sell_strike, "PE")
            
            if buy_ltp is not None and sell_ltp is not None:
                strategy_name = "Bear Put Spread"
                strat_type = "Debit"
                net_premium = round(buy_ltp - sell_ltp, 2)
                max_profit = round((buy_strike - sell_strike) - net_premium, 2)
                max_risk = net_premium
                breakeven = round(buy_strike - net_premium, 2)
                legs = [
                    {"type": "Buy", "optionType": "PE", "strike": int(buy_strike), "ltp": buy_ltp},
                    {"type": "Sell", "optionType": "PE", "strike": int(sell_strike), "ltp": sell_ltp}
                ]
                reasons = [
                    "Strong Bearish trend",
                    f"Significant expected move ({daily_move:.1f} pts)",
                    "Low/Normal VIX (Cheap premiums)",
                    "Support is far away"
                ]
                description = f"Bearish debit spread. Capitalizes on fast directional move down toward {sell_strike}."

    elif trend in ["Range-bound", "Neutral"]:
        # If Volatility is High -> Long Straddle (breakout play)
        if vol_regime == "High":
            buy_strike = atm_strike
            ce_ltp = get_ltp(buy_strike, "CE")
            pe_ltp = get_ltp(buy_strike, "PE")
            
            if ce_ltp is not None and pe_ltp is not None:
                strategy_name = "Long Straddle"
                strat_type = "Debit"
                net_premium = round(ce_ltp + pe_ltp, 2)
                max_profit = 9999.0  # Unlimited in points
                max_risk = net_premium
                breakeven_up = round(buy_strike + net_premium, 2)
                breakeven_down = round(buy_strike - net_premium, 2)
                legs = [
                    {"type": "Buy", "optionType": "CE", "strike": int(buy_strike), "ltp": ce_ltp},
                    {"type": "Buy", "optionType": "PE", "strike": int(buy_strike), "ltp": pe_ltp}
                ]
                reasons = [
                    "Range-bound/Neutral Trend",
                    "High VIX / Volatility Expansion expected",
                    "Breakout imminent in either direction"
                ]
                description = f"Neutral debit strategy. Profit from large breakout beyond {breakeven_down} or {breakeven_up}."
        
        # If Volatility is Low -> Iron Condor (sell both sides)
        elif vol_regime == "Low":
            sell_call = strong_res
            buy_call = sell_call + 100
            sell_put = strong_sup
            buy_put = sell_put - 100
            
            sc_ltp = get_ltp(sell_call, "CE")
            bc_ltp = get_ltp(buy_call, "CE")
            sp_ltp = get_ltp(sell_put, "PE")
            bp_ltp = get_ltp(buy_put, "PE")
            
            if all(x is not None for x in [sc_ltp, bc_ltp, sp_ltp, bp_ltp]):
                strategy_name = "Iron Condor"
                strat_type = "Credit"
                net_premium = round((sc_ltp - bc_ltp) + (sp_ltp - bp_ltp), 2)
                max_profit = net_premium
                max_risk = round(100.0 - net_premium, 2)
                legs = [
                    {"type": "Sell", "optionType": "CE", "strike": int(sell_call), "ltp": sc_ltp},
                    {"type": "Buy", "optionType": "CE", "strike": int(buy_call), "ltp": bc_ltp},
                    {"type": "Sell", "optionType": "PE", "strike": int(sell_put), "ltp": sp_ltp},
                    {"type": "Buy", "optionType": "PE", "strike": int(buy_put), "ltp": bp_ltp}
                ]
                reasons = [
                    "Range-bound/Neutral Trend",
                    "Low VIX (Calm volatility environment)",
                    "S/R boundaries are stable",
                    "Time decay (Theta) favored on both sides"
                ]
                description = f"Neutral credit strategy. Maximum profit realized if Nifty remains between {sell_put} and {sell_call}."
                
        # If Volatility is Normal -> Short Straddle (sell ATM)
        else:
            sell_strike = atm_strike
            ce_ltp = get_ltp(sell_strike, "CE")
            pe_ltp = get_ltp(sell_strike, "PE")
            
            if ce_ltp is not None and pe_ltp is not None:
                strategy_name = "Short Straddle"
                strat_type = "Credit"
                net_premium = round(ce_ltp + pe_ltp, 2)
                max_profit = net_premium
                max_risk = 9999.0  # Unlimited in points
                legs = [
                    {"type": "Sell", "optionType": "CE", "strike": int(sell_strike), "ltp": ce_ltp},
                    {"type": "Sell", "optionType": "PE", "strike": int(sell_strike), "ltp": pe_ltp}
                ]
                reasons = [
                    "Range-bound/Neutral Trend",
                    "Normal Volatility regime",
                    "Index expected to stay near ATM strike",
                    "Maximize premium collection via dual ATM decay"
                ]
                description = f"Neutral credit strategy. Earns maximum profit if Nifty expires exactly at {sell_strike} ATM strike."

    return {
        "strategyName": strategy_name,
        "type": strat_type,
        "legs": legs,
        "netCreditDebit": net_premium,
        "maxProfitPoints": max_profit,
        "maxRiskPoints": max_risk,
        "breakeven": breakeven,
        "description": description,
        "reasons": reasons
    }

def calculate_expected_ranges(spot, vix, normalized_chain, atm_strike, sup_res):
    """
    Module 7: Expected Range Engine
    Combines VIX, ATM Straddle Price, S/R Levels, and OI Structure to estimate:
    - Expected Daily Range (1-day VIX move)
    - Expected Weekly Range (7-day VIX move)
    - High Probability Range (ATM Straddle + Weak S/R)
    - Low Probability Range (2 Standard Deviations VIX + Strong S/R)
    """
    import math
    
    # 1. VIX daily & weekly moves
    daily_pct = (vix / 100.0) / math.sqrt(365)
    weekly_pct = (vix / 100.0) / math.sqrt(52)
    
    daily_move = spot * daily_pct
    weekly_move = spot * weekly_pct
    
    daily_lower = round(spot - daily_move, 2)
    daily_upper = round(spot + daily_move, 2)
    
    weekly_lower = round(spot - weekly_move, 2)
    weekly_upper = round(spot + weekly_move, 2)
    
    # 2. ATM Straddle Price
    atm_row = next((r for r in normalized_chain if r.get("strike") == int(atm_strike)), None)
    straddle_price = 150.0  # default fallback
    if atm_row and atm_row.get("CE") and atm_row.get("PE"):
        ce_ltp = atm_row["CE"].get("ltp")
        pe_ltp = atm_row["PE"].get("ltp")
        if ce_ltp is not None and pe_ltp is not None:
            straddle_price = ce_ltp + pe_ltp
            
    # 3. High Probability Range (Straddle Range capped by weak S/R)
    # The market is highly likely to stay inside this range on a standard trading session
    high_lower = round(max(atm_strike - straddle_price, sup_res["weakSupport"]), 2)
    high_upper = round(min(atm_strike + straddle_price, sup_res["weakResistance"]), 2)
    
    # 4. Low Probability Range (Outer extreme boundaries / 2 Standard Deviations VIX & Strong S/R)
    # Extremely low probability of breaching these outer limits
    outer_vix_move = spot * (2.0 * daily_pct)
    low_lower = round(min(spot - outer_vix_move, sup_res["strongSupport"] - 50), 2)
    low_upper = round(max(spot + outer_vix_move, sup_res["strongResistance"] + 50), 2)
    
    return {
        "dailyRange": {"lower": daily_lower, "upper": daily_upper, "move": round(daily_move, 2)},
        "weeklyRange": {"lower": weekly_lower, "upper": weekly_upper, "move": round(weekly_move, 2)},
        "highProbabilityRange": {"lower": high_lower, "upper": high_upper},
        "lowProbabilityRange": {"lower": low_lower, "upper": low_upper},
        "straddlePrice": round(straddle_price, 2)
    }
