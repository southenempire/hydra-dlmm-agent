"""
Hydra-DLMM Strategy Controller.
Main execution controller adhering to Hummingbot V2 Controller conventions.
Orchestrates volatility estimation, DLMM bin distributions, economic churn gating,
and cross-venue delta micro-hedging.
"""

import time
import logging
from decimal import Decimal
from typing import Dict, List, Any, Optional

from config.hydra_dlmm_config import HydraDLMMConfig
from strategy.volatility_engine import VolatilityEngine
from strategy.bin_shaper import BinShaper
from strategy.churn_gate import EconomicChurnGate
from executors.delta_hedge_executor import DeltaHedgeExecutor

logger = logging.getLogger(__name__)


class HydraDLMMController:
    """Hummingbot V2 Strategy Controller for Meteora DLMM."""

    def __init__(self, config: HydraDLMMConfig):
        self.config = config
        
        # Sub-modules
        self.volatility_engine = VolatilityEngine(
            lookback_minutes=config.volatility_lookback_minutes,
            target_sigma=config.target_bin_spread_sigma
        )
        self.bin_shaper = BinShaper(
            curve_concentration=float(config.curve_concentration_factor),
            momentum_threshold=float(config.momentum_skew_threshold)
        )
        self.churn_gate = EconomicChurnGate(
            dwell_slots_threshold=config.rebalance_dwell_slots,
            min_fee_improvement_ratio=config.min_net_fee_improvement_ratio,
            estimated_priority_fee_sol=config.estimated_priority_fee_sol,
            max_slippage_bps=config.max_slippage_bps
        )
        self.delta_hedge_executor = DeltaHedgeExecutor(
            hedge_connector=config.hedge_connector_name,
            hedge_trading_pair=config.hedge_trading_pair,
            delta_threshold_pct=config.delta_hedge_threshold_pct,
            leverage=config.hedge_leverage
        )

        # Internal State
        self.state = "INITIALIZING"  # INITIALIZING, ACTIVE_LP, REBALANCING, STOPPED
        self.active_position_bin_range: Optional[Dict[str, int]] = None
        self.last_rebalance_timestamp: float = 0.0
        self.peak_portfolio_value: float = float(config.total_capital_quote)
        self.current_portfolio_value: float = float(config.total_capital_quote)
        self.last_rpc_update_time: float = time.time()

    def update_market_data(
        self,
        active_bin_id: int,
        bin_step_bps: int,
        spot_price: float,
        ohlc_candles: List[Dict[str, float]],
        pool_24h_volume_usd: float,
        pool_tvl_usd: float,
        spot_base_balance: float,
        perp_short_balance: float = 0.0
    ) -> Dict[str, Any]:
        """
        Processes tick data and generates necessary execution orders.
        """
        self.last_rpc_update_time = time.time()
        
        # 1. Volatility & Spread
        rv_gk = self.volatility_engine.calculate_garman_klass_volatility(ohlc_candles)
        half_bins, half_spread_pct = self.volatility_engine.compute_dynamic_bin_spread(
            realized_volatility=rv_gk,
            bin_step_bps=bin_step_bps,
            min_bins=self.config.min_bins_spread,
            max_bins=self.config.max_bins_spread
        )
        
        # 2. Check if active bin is inside current position
        is_in_range = True
        if self.active_position_bin_range:
            min_bin = self.active_position_bin_range["min_bin_id"]
            max_bin = self.active_position_bin_range["max_bin_id"]
            is_in_range = (min_bin <= active_bin_id <= max_bin)
        else:
            is_in_range = False  # No active position yet

        # 3. Check Stop-Loss / Drawdown Circuit Breaker
        current_drawdown = (self.peak_portfolio_value - self.current_portfolio_value) / max(self.peak_portfolio_value, 1.0)
        if current_drawdown >= float(self.config.max_drawdown_stop_pct):
            self.state = "STOPPED"
            return {
                "action": "EMERGENCY_STOP",
                "reason": f"Max drawdown exceeded: {current_drawdown*100:.2f}%",
                "orders": [{"action": "WITHDRAW_ALL"}, {"action": "CLOSE_ALL_HEDGES"}]
            }

        # 4. Determine Rebalance Requirement via Churn Gate
        should_rebalance, churn_telemetry = self.churn_gate.evaluate_rebalance(
            is_in_active_range=is_in_range,
            current_capital_usd=self.current_portfolio_value,
            sol_price_usd=spot_price,
            pool_24h_volume_usd=pool_24h_volume_usd,
            pool_tvl_usd=pool_tvl_usd
        )

        orders = []

        # 5. Position Management
        if self.active_position_bin_range is None or should_rebalance:
            # Generate optimal bin distribution
            momentum = 0.0
            if len(ohlc_candles) >= 2:
                momentum = (ohlc_candles[-1]["close"] - ohlc_candles[0]["open"]) / max(ohlc_candles[0]["open"], 1e-6)

            bin_weights = self.bin_shaper.generate_distribution(
                active_bin_id=active_bin_id,
                half_width_bins=half_bins,
                mode=self.config.distribution_mode,
                short_term_momentum=momentum
            )

            min_bin_id = active_bin_id - half_bins
            max_bin_id = active_bin_id + half_bins
            self.active_position_bin_range = {"min_bin_id": min_bin_id, "max_bin_id": max_bin_id}

            orders.append({
                "action": "DEPLOY_DLMM_POSITION",
                "connector": self.config.connector_name,
                "pool_address": self.config.pool_address,
                "active_bin_id": active_bin_id,
                "min_bin_id": min_bin_id,
                "max_bin_id": max_bin_id,
                "bin_weights": bin_weights,
                "capital_quote": float(self.config.total_capital_quote)
            })
            self.state = "ACTIVE_LP"

        # 6. Delta Hedge Verification
        if self.config.enable_delta_hedge:
            hedge_order, hedge_telemetry = self.delta_hedge_executor.compute_hedge_action(
                spot_base_amount=spot_base_balance,
                spot_price_usd=spot_price,
                current_perp_short_amount=perp_short_balance,
                total_strategy_capital_usd=self.current_portfolio_value
            )
            if hedge_order:
                orders.append({"action": "EXECUTE_PERP_HEDGE", "payload": hedge_order})
        else:
            hedge_telemetry = {"enabled": False}

        return {
            "status": self.state,
            "realized_volatility": round(rv_gk, 6),
            "target_half_bins": half_bins,
            "half_spread_pct": round(half_spread_pct * 100, 3),
            "is_in_range": is_in_range,
            "churn_telemetry": churn_telemetry,
            "hedge_telemetry": hedge_telemetry,
            "generated_orders": orders
        }
