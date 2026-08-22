"""
Volatility Engine for Hydra-DLMM.
Calculates intraday Parkinson and Garman-Klass Realized Volatility
and converts volatility metrics into dynamic DLMM bin widths.
"""

import math
from decimal import Decimal
from typing import List, Dict, Any, Tuple


class VolatilityEngine:
    """Realized volatility estimation and bin spread calculator."""

    def __init__(self, lookback_minutes: int = 30, target_sigma: Decimal = Decimal("1.5")):
        self.lookback_minutes = lookback_minutes
        self.target_sigma = float(target_sigma)

    def calculate_parkinson_volatility(self, ohlc_candles: List[Dict[str, float]]) -> float:
        """
        Calculates Parkinson volatility:
        sigma_P = sqrt( 1 / (4 * ln(2) * N) * sum( (ln(H_i / L_i))^2 ) )
        """
        if not ohlc_candles:
            return 0.005  # 0.5% default fallback
        
        sum_sq = 0.0
        n = len(ohlc_candles)
        for candle in ohlc_candles:
            high = candle["high"]
            low = max(candle["low"], 1e-8)
            if high > 0 and low > 0:
                log_hl = math.log(high / low)
                sum_sq += log_hl * log_hl

        factor = 1.0 / (4.0 * math.log(2.0) * max(n, 1))
        # Instantaneous annualized or period volatility
        sigma_parkinson = math.sqrt(factor * sum_sq)
        return sigma_parkinson

    def calculate_garman_klass_volatility(self, ohlc_candles: List[Dict[str, float]]) -> float:
        """
        Calculates Garman-Klass volatility (includes Open & Close):
        0.5 * (ln(H/L))^2 - (2*ln(2) - 1) * (ln(C/O))^2
        """
        if not ohlc_candles:
            return 0.005
        
        sum_val = 0.0
        n = len(ohlc_candles)
        c_const = 2.0 * math.log(2.0) - 1.0
        for candle in ohlc_candles:
            high = candle["high"]
            low = max(candle["low"], 1e-8)
            open_p = max(candle["open"], 1e-8)
            close_p = max(candle["close"], 1e-8)
            
            log_hl = math.log(high / low)
            log_co = math.log(close_p / open_p)
            sum_val += 0.5 * (log_hl ** 2) - c_const * (log_co ** 2)
            
        sigma_gk = math.sqrt(max(sum_val / max(n, 1), 1e-8))
        return sigma_gk

    def compute_dynamic_bin_spread(
        self,
        realized_volatility: float,
        bin_step_bps: int,
        min_bins: int = 5,
        max_bins: int = 35
    ) -> Tuple[int, float]:
        """
        Converts realized volatility (sigma) into number of DLMM bins on each side.
        
        Args:
            realized_volatility: e.g. 0.015 (1.5% volatility)
            bin_step_bps: Meteora pool bin step in basis points (e.g. 10 bps = 0.10%)
            min_bins: Minimum half-width bins
            max_bins: Maximum half-width bins
            
        Returns:
            Tuple of (num_bins_half_width, expected_half_spread_pct)
        """
        bin_step_pct = (bin_step_bps / 10000.0)
        target_spread_pct = realized_volatility * self.target_sigma
        
        # Calculate raw bins needed
        raw_bins = target_spread_pct / max(bin_step_pct, 1e-6)
        clamped_bins = int(max(min_bins, min(max_bins, round(raw_bins))))
        actual_half_spread = clamped_bins * bin_step_pct
        
        return clamped_bins, actual_half_spread

    def estimate_bin_dwell_time_seconds(self, realized_volatility: float, bin_step_bps: int) -> float:
        """
        Estimates expected first-passage time (dwell time) inside a single bin
        using Brownian motion hitting-time approximation:
        T_dwell = (bin_step / sigma)^2 * t_ref
        """
        bin_step_pct = (bin_step_bps / 10000.0)
        if realized_volatility <= 0:
            return 300.0
        # Reference 60s scale
        dwell_ratio = (bin_step_pct / realized_volatility) ** 2
        return max(5.0, min(1800.0, dwell_ratio * 3600.0))
