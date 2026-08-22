"""
Cross-Venue Delta Hedge Executor for Hydra-DLMM.
Calculates net portfolio delta across DLMM spot bins and generates
micro-hedge orders on perpetual venues (Gate/Bitget/Hyperliquid).
"""

from decimal import Decimal
from typing import Dict, Any, Optional, Tuple


class DeltaHedgeExecutor:
    """Manages delta-neutrality by offsetting DLMM spot inventory with perpetuals."""

    def __init__(
        self,
        hedge_connector: str = "gate_perpetual",
        hedge_trading_pair: str = "SOL-USDT",
        delta_threshold_pct: Decimal = Decimal("0.05"),
        leverage: int = 1
    ):
        self.hedge_connector = hedge_connector
        self.hedge_trading_pair = hedge_trading_pair
        self.delta_threshold_pct = float(delta_threshold_pct)
        self.leverage = leverage

    def compute_hedge_action(
        self,
        spot_base_amount: float,
        spot_price_usd: float,
        current_perp_short_amount: float,
        total_strategy_capital_usd: float
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        Calculates required perpetual order to neutralize delta.
        
        Args:
            spot_base_amount: Units of base token currently held in DLMM + wallet
            spot_price_usd: Current spot price
            current_perp_short_amount: Units currently shorted on perp venue (positive number)
            total_strategy_capital_usd: Total strategy portfolio value
            
        Returns:
            Tuple of (order_payload_or_None, telemetry_dict)
        """
        # Target perp short should equal the spot base holding
        target_short_amount = spot_base_amount
        delta_error_units = spot_base_amount - current_perp_short_amount
        delta_error_usd = delta_error_units * spot_price_usd
        
        # Sizing relative to total portfolio
        delta_skew_pct = abs(delta_error_usd) / max(total_strategy_capital_usd, 1.0)
        
        should_hedge = delta_skew_pct >= self.delta_threshold_pct
        order_payload = None

        if should_hedge and abs(delta_error_units) > 1e-4:
            if delta_error_units > 0:
                # We are net long spot -> SELL short on perp
                order_payload = {
                    "connector_name": self.hedge_connector,
                    "trading_pair": self.hedge_trading_pair,
                    "order_side": "SELL",
                    "order_type": "MARKET",
                    "amount": round(abs(delta_error_units), 4),
                    "price": spot_price_usd,
                    "leverage": self.leverage,
                    "reduce_only": False
                }
            else:
                # We have excess short on perp -> BUY to cover short
                order_payload = {
                    "connector_name": self.hedge_connector,
                    "trading_pair": self.hedge_trading_pair,
                    "order_side": "BUY",
                    "order_type": "MARKET",
                    "amount": round(abs(delta_error_units), 4),
                    "price": spot_price_usd,
                    "leverage": self.leverage,
                    "reduce_only": True
                }

        telemetry = {
            "spot_base_amount": round(spot_base_amount, 4),
            "current_perp_short_amount": round(current_perp_short_amount, 4),
            "delta_error_units": round(delta_error_units, 4),
            "delta_error_usd": round(delta_error_usd, 2),
            "delta_skew_pct": round(delta_skew_pct * 100, 2),
            "should_hedge": should_hedge,
            "action": order_payload["order_side"] if order_payload else "NEUTRAL"
        }

        return order_payload, telemetry
