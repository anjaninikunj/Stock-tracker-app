import math
from datetime import datetime
from scipy.stats import norm
import config

def black_scholes_greeks(underlying, K, T, r, sigma, option_type):
    """
    Calculates Black-76 or standard Black-Scholes Greeks.
    underlying: Spot or Future Price
    K: Strike Price
    T: Time to Expiration in Years
    r: Interest rate (set to 0.0 for Black-76 model)
    sigma: Implied Volatility (decimal, e.g., 0.12)
    option_type: 'CE' or 'PE'
    """
    # Safeguard against zero or negative volatility
    if sigma <= 0:
        sigma = 0.0001
        
    # Safeguard against zero time to expiry
    if T <= 0:
        T = 0.0001
        
    d1 = (math.log(underlying / K) + (r + (sigma ** 2) / 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    # PDF and CDF of standard normal distribution
    n_d1 = norm.pdf(d1)
    N_d1 = norm.cdf(d1)
    N_d2 = norm.cdf(d2)
    
    # Common calculations
    sqrt_T = math.sqrt(T)
    exp_rT = math.exp(-r * T)
    
    # Gamma (same for CE and PE)
    gamma = n_d1 / (underlying * sigma * sqrt_T)
    
    # Vega (same for CE and PE)
    # Vega is usually expressed as the change in option price per 1% change in IV, so we divide by 100
    vega = (underlying * sqrt_T * n_d1) / 100.0
    
    if option_type == 'CE':
        delta = exp_rT * N_d1
        # Theta for Call
        theta = (- (underlying * n_d1 * sigma * exp_rT) / (2 * sqrt_T) - r * K * exp_rT * N_d2) / 365.0
    else:
        delta = exp_rT * (N_d1 - 1.0)
        # Theta for Put
        theta = (- (underlying * n_d1 * sigma * exp_rT) / (2 * sqrt_T) + r * K * exp_rT * norm.cdf(-d2)) / 365.0
        
    return {
        "delta": round(float(delta), 4) if not math.isnan(delta) else None,
        "gamma": round(float(gamma), 6) if not math.isnan(gamma) else None,
        "theta": round(float(theta), 4) if not math.isnan(theta) else None,
        "vega": round(float(vega), 4) if not math.isnan(vega) else None,
        "iv": round(float(sigma * 100.0), 2)
    }

def get_time_to_expiry(expiry_str, timestamp_str):
    """
    Computes remaining time T to expiry in years.
    expiry_str: e.g. '04-Aug-2026'
    timestamp_str: e.g. '31-Jul-2026 15:30:11'
    """
    try:
        # Standard formats from NSE/jugaad-data
        expiry_dt = datetime.strptime(expiry_str, "%d-%b-%Y")
        
        # Parse timestamp (can be '31-Jul-2026 15:30:11' or similar)
        # Clean potential whitespace
        timestamp_str = timestamp_str.strip()
        try:
            data_dt = datetime.strptime(timestamp_str, "%d-%b-%Y %H:%M:%S")
        except ValueError:
            # Fallback format if seconds are missing
            data_dt = datetime.strptime(timestamp_str, "%d-%b-%Y %H:%M")
            
        # Time difference in days (as float)
        delta_time = expiry_dt.replace(hour=15, minute=30) - data_dt
        diff_days = delta_time.total_seconds() / 86400.0
        
        # If expiry date has passed, or it is expiry day, enforce a minimum T of 0.01 days (about 14 mins)
        # to avoid division by zero
        T_days = max(0.01, diff_days)
        return T_days / 365.0
    except Exception as e:
        print(f"[-] Error calculating time to expiry (Expiry: {expiry_str}, Timestamp: {timestamp_str}): {e}")
        # Default fallback to 4 days
        return 4.0 / 365.0

def process_greeks_for_row(row, underlying, expiry_str, timestamp_str, vix_value, r=0.0):
    """
    Extracts data for CE and PE in a row, calculates their greeks, and returns the parsed objects.
    underlying: The Synthetic Future price (or Spot)
    r: Risk-free rate (defaults to 0.0 for Black-76 model)
    """
    strike = float(row.get("strikePrice"))
    T = get_time_to_expiry(expiry_str, timestamp_str)
    
    # Default VIX fallback in decimal format
    fallback_sigma = vix_value / 100.0 if vix_value else 0.12
    
    # Process Call (CE)
    ce_data = row.get("CE")
    ce_greeks = None
    if ce_data:
        iv_val = ce_data.get("impliedVolatility", 0)
        try:
            iv_pct = float(iv_val) if iv_val else 0.0
        except (ValueError, TypeError):
            iv_pct = 0.0
            
        # Use fallback if IV is reported as zero or missing
        sigma = iv_pct / 100.0 if iv_pct > 0 else fallback_sigma
        
        ce_greeks = black_scholes_greeks(underlying, strike, T, r, sigma, 'CE')
        
    # Process Put (PE)
    pe_data = row.get("PE")
    pe_greeks = None
    if pe_data:
        iv_val = pe_data.get("impliedVolatility", 0)
        try:
            iv_pct = float(iv_val) if iv_val else 0.0
        except (ValueError, TypeError):
            iv_pct = 0.0
            
        sigma = iv_pct / 100.0 if iv_pct > 0 else fallback_sigma
        
        pe_greeks = black_scholes_greeks(underlying, strike, T, r, sigma, 'PE')
        
    return ce_greeks, pe_greeks

if __name__ == "__main__":
    # Test Greeks with dummy data
    F = 24368.79     # Synthetic Future Price
    K = 24400.0
    T = 4.0 / 365.0  # 4 days
    r = 0.0          # 0.0 for Black-76
    sigma = 0.081    # 8.1% IV
    
    ce = black_scholes_greeks(F, K, T, r, sigma, 'CE')
    pe = black_scholes_greeks(F, K, T, r, sigma, 'PE')
    
    print("Test Black-76 Greeks Calculations (Future=24368.79, Strike=24400, 4 Days, IV=8.1%):")
    print(f"Call (CE) Greeks: {ce}")
    print(f"Put  (PE) Greeks: {pe}")
