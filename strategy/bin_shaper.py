"""
Bin Shaper for Hydra-DLMM.
Calculates optimal liquidity distribution weights across active DLMM bins.
Supports Spot (Uniform), Curve (Gaussian Concentrated), and BidAsk (Momentum Skewed).
"""

import math
from typing import Dict, List, Literal, Tuple


class BinShaper:
    """Calculates liquidity distribution weights for Meteora DLMM positions."""

    def __init__(self, curve_concentration: float = 2.0, momentum_threshold: float = 0.005):
        self.curve_concentration = curve_concentration
        self.momentum_threshold = momentum_threshold

    def generate_distribution(
        self,
        active_bin_id: int,
        half_width_bins: int,
        mode: Literal["dynamic", "curve", "bid_ask", "spot"] = "dynamic",
        short_term_momentum: float = 0.0
    ) -> Dict[int, float]:
        """
        Generates normalized liquidity weights across bins.
        
        Args:
            active_bin_id: Current active price bin ID on Meteora
            half_width_bins: Number of bins below and above active bin
            mode: Distribution mode
            short_term_momentum: Normalized price return over fast lookback (e.g. +0.01 = +1%)
            
        Returns:
            Dictionary mapping {bin_id: normalized_liquidity_weight} where sum of weights == 1.0
        """
        bin_ids = list(range(active_bin_id - half_width_bins, active_bin_id + half_width_bins + 1))
        
        # Decide effective mode if dynamic
        effective_mode = mode
        if mode == "dynamic":
            if abs(short_term_momentum) >= self.momentum_threshold:
                effective_mode = "bid_ask"
            else:
                effective_mode = "curve"

        if effective_mode == "spot":
            return self._generate_spot_weights(bin_ids)
        elif effective_mode == "curve":
            return self._generate_curve_weights(bin_ids, active_bin_id, half_width_bins)
        elif effective_mode == "bid_ask":
            return self._generate_bid_ask_weights(bin_ids, active_bin_id, short_term_momentum)
        else:
            return self._generate_spot_weights(bin_ids)

    def _generate_spot_weights(self, bin_ids: List[int]) -> Dict[int, float]:
        """Uniform distribution across all bins."""
        uniform_weight = 1.0 / len(bin_ids)
        return {bin_id: uniform_weight for bin_id in bin_ids}

    def _generate_curve_weights(
        self,
        bin_ids: List[int],
        active_bin_id: int,
        half_width_bins: int
    ) -> Dict[int, float]:
        """
        Gaussian Curve centered on active bin:
        w(i) = exp( -0.5 * ( (i - active_bin) / (half_width / concentration) )^2 )
        """
        sigma_bins = max(half_width_bins / self.curve_concentration, 1.0)
        raw_weights = {}
        total_raw = 0.0

        for bin_id in bin_ids:
            dist = bin_id - active_bin_id
            weight = math.exp(-0.5 * (dist / sigma_bins) ** 2)
            raw_weights[bin_id] = weight
            total_raw += weight

        # Normalize so sum = 1.0
        return {bin_id: w / total_raw for bin_id, w in raw_weights.items()}

    def _generate_bid_ask_weights(
        self,
        bin_ids: List[int],
        active_bin_id: int,
        momentum: float
    ) -> Dict[int, float]:
        """
        Asymmetric BidAsk profile:
        - If momentum > 0 (Uptrend): We skew liquidity onto the Ask side (laddered selling into strength)
          while keeping a safety buffer on the Bid side.
        - If momentum < 0 (Downtrend): We skew liquidity onto the Bid side (buying into dips).
        """
        raw_weights = {}
        total_raw = 0.0
        skew_factor = 1.8 if momentum > 0 else 0.55

        for bin_id in bin_ids:
            dist = bin_id - active_bin_id
            if dist == 0:
                base_w = 1.5
            elif dist > 0:  # Ask side (selling base token)
                base_w = 1.0 * (skew_factor if momentum > 0 else (1.0 / skew_factor))
                # Add laddering
                base_w *= max(0.2, 1.0 - 0.03 * dist)
            else:  # Bid side (buying base token)
                base_w = 1.0 * ((1.0 / skew_factor) if momentum > 0 else skew_factor)
                base_w *= max(0.2, 1.0 - 0.03 * abs(dist))

            raw_weights[bin_id] = base_w
            total_raw += base_w

        return {bin_id: w / total_raw for bin_id, w in raw_weights.items()}
