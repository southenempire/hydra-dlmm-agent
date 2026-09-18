"""
Hydra-DLMM: Volatility-Adaptive Liquidity Agent with Cross-Venue Delta Hedge.

Hummingbot V2 Controller for Meteora DLMM (Solana).

This controller implements:
  - Garman-Klass / Parkinson intraday Realized Volatility estimation
  - Dynamic DLMM bin range sizing calibrated to σ(t)
  - Gaussian Curve vs. momentum-skewed BidAsk liquidity shaping
  - Economic churn gating with slot dwell-time confirmation
  - Cross-venue perpetual delta hedging (Gate/Bitget/Hyperliquid)

Compatible with Hummingbot V2 framework: inherits ControllerConfigBase
and ControllerBase, implements determine_executor_actions().

Author: Southen_ (Team Meteora — Agent Builders Cup)
"""

import logging
import time
from decimal import Decimal
from typing import Dict, List, Any, Literal, Optional, Set

from pydantic import Field

# ---------------------------------------------------------------------------
# Hummingbot V2 Framework Imports
# ---------------------------------------------------------------------------
# When running INSIDE the Hummingbot runtime these resolve to the real classes.
# When running standalone (tests, backtests) we fall back to lightweight stubs
# so the module remains importable and testable without the full Hummingbot
# installation.
# ---------------------------------------------------------------------------

try:
    from hummingbot.strategy_v2.controllers.controller_base import (
        ControllerBase,
        ControllerConfigBase,
    )
    from hummingbot.strategy_v2.models.executor_actions import (
        CreateExecutorAction,
        ExecutorAction,
        StopExecutorAction,
    )
    from hummingbot.strategy_v2.executors.position_executor.data_types import (
        PositionExecutorConfig,
        TripleBarrierConfig,
    )
    from hummingbot.strategy_v2.executors.data_types import ConnectorPair
    from hummingbot.core.data_type.common import OrderType, TradeType

    _HB_AVAILABLE = True
except ImportError:
    # ── Lightweight stubs for standalone testing / backtesting ──────────
    from pydantic import BaseModel as ControllerConfigBase  # type: ignore[assignment]

    class ControllerBase:  # type: ignore[no-redef]
        """Minimal stub so the controller can be instantiated outside Hummingbot."""
        def __init__(self, config, *args, **kwargs):
            self.config = config
            self.status = "ACTIVE"

    class ExecutorAction:  # type: ignore[no-redef]
        pass

    class CreateExecutorAction(ExecutorAction):  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class StopExecutorAction(ExecutorAction):  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    _HB_AVAILABLE = False

# ---------------------------------------------------------------------------
# Internal strategy modules (unchanged from standalone version)
# ---------------------------------------------------------------------------
import sys, os
# Ensure the project root is on sys.path for both Hummingbot and standalone use
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from strategy.volatility_engine import VolatilityEngine
from strategy.bin_shaper import BinShaper
from strategy.churn_gate import EconomicChurnGate
from executors.delta_hedge_executor import DeltaHedgeExecutor

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG — inherits ControllerConfigBase for Hummingbot V2 discovery
# ═══════════════════════════════════════════════════════════════════════════════


class HydraDLMMConfig(ControllerConfigBase):
    """
    Hummingbot V2 Controller configuration for Hydra-DLMM.

    Usage (Hummingbot CLI):
        create --controller-config market_making.hydra_dlmm
        start --v2 v2_with_controllers
    """

    # ── Controller Identity ────────────────────────────────────────────────
    id: str = Field(default="hydra_dlmm_001",
                    description="Unique controller instance ID")
    controller_name: str = "hydra_dlmm"
    controller_type: str = "market_making"

    # ── Primary DEX & Pool ─────────────────────────────────────────────────
    connector_name: str = Field(
        default="meteora",
        json_schema_extra={
            "prompt": "Enter the DEX connector name (e.g., meteora): ",
            "prompt_on_new": True,
        },
    )
    trading_pair: str = Field(
        default="SOL-USDC",
        json_schema_extra={
            "prompt": "Enter the trading pair (e.g., SOL-USDC): ",
            "prompt_on_new": True,
        },
    )
    pool_address: str = Field(
        default="ARwi1S4DaiTG5DX7S4M4ZsrXqpMD1MrTMsonBK52BuJn",
        description="Solana address for the Meteora DLMM pool",
    )
    total_amount_quote: Decimal = Field(
        default=Decimal("1000"),
        json_schema_extra={
            "prompt": "Enter total capital in quote asset (USDC): ",
            "prompt_on_new": True,
            "is_updatable": True,
        },
    )

    # ── Volatility Engine ──────────────────────────────────────────────────
    volatility_lookback_minutes: int = Field(
        default=30,
        description="Lookback window for Garman-Klass / Parkinson RV",
    )
    target_bin_spread_sigma: Decimal = Field(
        default=Decimal("1.5"),
        description="Target half-spread in realized volatility units (σ)",
    )
    min_bins_spread: int = Field(default=5, description="Minimum half-width bins")
    max_bins_spread: int = Field(default=35, description="Maximum half-width bins")

    # ── Bin Shaping ────────────────────────────────────────────────────────
    distribution_mode: Literal["dynamic", "curve", "bid_ask", "spot"] = Field(
        default="dynamic",
        description="Liquidity distribution shape across bins",
    )
    curve_concentration_factor: Decimal = Field(
        default=Decimal("2.0"),
        description="Gaussian steepness for Curve distribution",
    )
    momentum_skew_threshold: Decimal = Field(
        default=Decimal("0.005"),
        description="Min price momentum to trigger BidAsk skew",
    )

    # ── Economic Churn Gate ────────────────────────────────────────────────
    rebalance_dwell_slots: int = Field(
        default=5,
        description="Min consecutive slots OOR before rebalance trigger",
    )
    estimated_priority_fee_sol: Decimal = Field(
        default=Decimal("0.0005"),
        description="Estimated Solana priority fee per tx (SOL)",
    )
    max_slippage_bps: int = Field(
        default=25, description="Max slippage tolerance (bps)"
    )
    min_net_fee_improvement_ratio: Decimal = Field(
        default=Decimal("1.25"),
        description="Min expected-fee / rebalance-cost ratio",
    )

    # ── Cross-Venue Delta Hedging ──────────────────────────────────────────
    enable_delta_hedge: bool = Field(
        default=True,
        description="Enable perpetual hedging for delta neutrality",
    )
    hedge_connector_name: str = Field(
        default="gate_perpetual",
        description="Perp connector (gate_perpetual / bitget_perpetual / hyperliquid_perpetual)",
    )
    hedge_trading_pair: str = Field(
        default="SOL-USDT",
        description="Trading pair on perp exchange for hedging",
    )
    delta_hedge_threshold_pct: Decimal = Field(
        default=Decimal("0.05"),
        description="Portfolio net-delta skew before firing a micro-hedge (5%)",
    )
    hedge_leverage: int = Field(
        default=1, description="Target leverage for hedging venue"
    )

    # ── Risk / Circuit Breakers ────────────────────────────────────────────
    max_drawdown_stop_pct: Decimal = Field(
        default=Decimal("0.03"),
        description="Hard stop-loss circuit breaker (3% total drawdown)",
    )
    executor_refresh_time: int = Field(
        default=60 * 5,
        description="Refresh time in seconds for executors",
        json_schema_extra={"is_updatable": True},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CONTROLLER — inherits ControllerBase for Hummingbot V2 orchestration
# ═══════════════════════════════════════════════════════════════════════════════


class HydraDLMMController(ControllerBase):
    """
    Hummingbot V2 Strategy Controller for Meteora DLMM.

    Orchestrates:
      1. Garman-Klass realized volatility → dynamic bin spread
      2. Gaussian / BidAsk liquidity shaping
      3. Economic churn gate (dwell + fee hurdle)
      4. Cross-venue perpetual delta hedge
      5. Drawdown circuit breaker

    The controller emits CreateExecutorAction / StopExecutorAction objects
    consumed by the Hummingbot engine on each tick.
    """

    def __init__(self, config: HydraDLMMConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config = config

        # ── Sub-modules (unchanged strategy logic) ─────────────────────────
        self.volatility_engine = VolatilityEngine(
            lookback_minutes=config.volatility_lookback_minutes,
            target_sigma=config.target_bin_spread_sigma,
        )
        self.bin_shaper = BinShaper(
            curve_concentration=float(config.curve_concentration_factor),
            momentum_threshold=float(config.momentum_skew_threshold),
        )
        self.churn_gate = EconomicChurnGate(
            dwell_slots_threshold=config.rebalance_dwell_slots,
            min_fee_improvement_ratio=config.min_net_fee_improvement_ratio,
            estimated_priority_fee_sol=config.estimated_priority_fee_sol,
            max_slippage_bps=config.max_slippage_bps,
        )
        self.delta_hedge_executor = DeltaHedgeExecutor(
            hedge_connector=config.hedge_connector_name,
            hedge_trading_pair=config.hedge_trading_pair,
            delta_threshold_pct=config.delta_hedge_threshold_pct,
            leverage=config.hedge_leverage,
        )

        # ── Internal State ─────────────────────────────────────────────────
        self.active_position_bin_range: Optional[Dict[str, int]] = None
        self.last_rebalance_timestamp: float = 0.0
        self.peak_portfolio_value: float = float(config.total_amount_quote)
        self.current_portfolio_value: float = float(config.total_amount_quote)
        self._active_executor_ids: Set[str] = set()

    # ───────────────────────────────────────────────────────────────────────
    # Core V2 interface: determine_executor_actions()
    # ───────────────────────────────────────────────────────────────────────

    def determine_executor_actions(self) -> List[ExecutorAction]:
        """
        Called by Hummingbot on each tick.

        Evaluates market state and returns a list of ExecutorAction objects
        (CreateExecutorAction to open positions, StopExecutorAction to close).
        """
        actions: List[ExecutorAction] = []

        # If running inside Hummingbot, pull live candle data from the provider
        ohlc_candles = self._get_candle_data()
        spot_price = self._get_mid_price()
        active_bin_id = self._estimate_active_bin(spot_price)
        bin_step_bps = 10  # Standard Meteora DLMM bin step

        # ── 1. Realized Volatility & Dynamic Spread ────────────────────────
        rv_gk = self.volatility_engine.calculate_garman_klass_volatility(ohlc_candles)
        half_bins, half_spread_pct = self.volatility_engine.compute_dynamic_bin_spread(
            realized_volatility=rv_gk,
            bin_step_bps=bin_step_bps,
            min_bins=self.config.min_bins_spread,
            max_bins=self.config.max_bins_spread,
        )

        # ── 2. Range Check ─────────────────────────────────────────────────
        is_in_range = True
        if self.active_position_bin_range:
            min_bin = self.active_position_bin_range["min_bin_id"]
            max_bin = self.active_position_bin_range["max_bin_id"]
            is_in_range = min_bin <= active_bin_id <= max_bin
        else:
            is_in_range = False  # No active position yet

        # ── 3. Drawdown Circuit Breaker ────────────────────────────────────
        current_dd = (
            (self.peak_portfolio_value - self.current_portfolio_value)
            / max(self.peak_portfolio_value, 1.0)
        )
        if current_dd >= float(self.config.max_drawdown_stop_pct):
            logger.warning(
                "EMERGENCY STOP: drawdown %.2f%% exceeds threshold",
                current_dd * 100,
            )
            # Stop all active executors
            for eid in list(self._active_executor_ids):
                actions.append(StopExecutorAction(
                    controller_id=self.config.id,
                    executor_id=eid,
                ))
            self._active_executor_ids.clear()
            return actions

        # ── 4. Churn Gate Evaluation ───────────────────────────────────────
        pool_24h_volume = self._estimate_pool_volume()
        pool_tvl = self._estimate_pool_tvl()

        should_rebalance, churn_telemetry = self.churn_gate.evaluate_rebalance(
            is_in_active_range=is_in_range,
            current_capital_usd=self.current_portfolio_value,
            sol_price_usd=spot_price,
            pool_24h_volume_usd=pool_24h_volume,
            pool_tvl_usd=pool_tvl,
        )

        # ── 5. Position Management ─────────────────────────────────────────
        if self.active_position_bin_range is None or should_rebalance:
            # Stop existing executors before redeploying
            for eid in list(self._active_executor_ids):
                actions.append(StopExecutorAction(
                    controller_id=self.config.id,
                    executor_id=eid,
                ))
            self._active_executor_ids.clear()

            # Calculate momentum for bin shaping
            momentum = 0.0
            if len(ohlc_candles) >= 2:
                momentum = (
                    (ohlc_candles[-1]["close"] - ohlc_candles[0]["open"])
                    / max(ohlc_candles[0]["open"], 1e-6)
                )

            # Generate optimal bin distribution
            bin_weights = self.bin_shaper.generate_distribution(
                active_bin_id=active_bin_id,
                half_width_bins=half_bins,
                mode=self.config.distribution_mode,
                short_term_momentum=momentum,
            )

            # Update internal state
            self.active_position_bin_range = {
                "min_bin_id": active_bin_id - half_bins,
                "max_bin_id": active_bin_id + half_bins,
            }
            self.last_rebalance_timestamp = time.time()

            # Emit DLMM position deployment action
            executor_id = f"hydra_dlmm_{int(time.time())}"
            actions.append(CreateExecutorAction(
                controller_id=self.config.id,
                executor_id=executor_id,
                connector_name=self.config.connector_name,
                trading_pair=self.config.trading_pair,
                pool_address=self.config.pool_address,
                active_bin_id=active_bin_id,
                min_bin_id=active_bin_id - half_bins,
                max_bin_id=active_bin_id + half_bins,
                bin_weights=bin_weights,
                capital_quote=float(self.config.total_amount_quote),
                realized_volatility=round(rv_gk, 6),
                distribution_mode=self.config.distribution_mode,
            ))
            self._active_executor_ids.add(executor_id)

            logger.info(
                "DEPLOY position: bins=[%d, %d], rv=%.4f, mode=%s",
                active_bin_id - half_bins,
                active_bin_id + half_bins,
                rv_gk,
                self.config.distribution_mode,
            )

        # ── 6. Delta Hedge ─────────────────────────────────────────────────
        if self.config.enable_delta_hedge:
            spot_base_balance = self._get_spot_base_balance()
            perp_short_balance = self._get_perp_short_balance()

            hedge_order, hedge_telemetry = self.delta_hedge_executor.compute_hedge_action(
                spot_base_amount=spot_base_balance,
                spot_price_usd=spot_price,
                current_perp_short_amount=perp_short_balance,
                total_strategy_capital_usd=self.current_portfolio_value,
            )

            if hedge_order:
                hedge_exec_id = f"hydra_hedge_{int(time.time())}"
                actions.append(CreateExecutorAction(
                    controller_id=self.config.id,
                    executor_id=hedge_exec_id,
                    connector_name=self.config.hedge_connector_name,
                    trading_pair=self.config.hedge_trading_pair,
                    order_side=hedge_order["order_side"],
                    order_type=hedge_order["order_type"],
                    amount=hedge_order["amount"],
                    price=hedge_order["price"],
                    leverage=hedge_order["leverage"],
                ))
                self._active_executor_ids.add(hedge_exec_id)

        return actions

    # ───────────────────────────────────────────────────────────────────────
    # Data accessors — use Hummingbot MarketDataProvider when available,
    # otherwise return sensible defaults for standalone testing.
    # ───────────────────────────────────────────────────────────────────────

    def _get_candle_data(self) -> List[Dict[str, float]]:
        """Retrieve OHLC candle data from MarketDataProvider or return defaults."""
        if _HB_AVAILABLE and hasattr(self, "market_data_provider"):
            try:
                candles = self.market_data_provider.get_candles_df(
                    connector_name=self.config.connector_name,
                    trading_pair=self.config.trading_pair,
                    interval="1m",
                    max_records=self.config.volatility_lookback_minutes,
                )
                return candles.to_dict("records") if candles is not None else []
            except Exception:
                pass
        return []

    def _get_mid_price(self) -> float:
        """Get current mid-price from MarketDataProvider or return default."""
        if _HB_AVAILABLE and hasattr(self, "market_data_provider"):
            try:
                return float(
                    self.market_data_provider.get_price_by_type(
                        self.config.connector_name,
                        self.config.trading_pair,
                    )
                )
            except Exception:
                pass
        return 140.0  # Default for standalone testing

    def _estimate_active_bin(self, spot_price: float) -> int:
        """Convert spot price to an estimated DLMM bin ID."""
        # Bin IDs on Meteora are derived from the log-price grid.
        # For a 10 bps bin step, bin_id ≈ 1000 + Δprice / (price × 0.001)
        reference_price = 140.0
        return int(1000 + (spot_price - reference_price) / (reference_price * 0.001))

    def _estimate_pool_volume(self) -> float:
        """Estimate 24h pool volume. In production, fetch from Meteora RPC."""
        return 10_000_000.0

    def _estimate_pool_tvl(self) -> float:
        """Estimate pool TVL. In production, fetch from Meteora RPC."""
        return 1_000_000.0

    def _get_spot_base_balance(self) -> float:
        """Get spot base token balance from connector."""
        if _HB_AVAILABLE and hasattr(self, "market_data_provider"):
            try:
                return float(
                    self.market_data_provider.get_balance(
                        self.config.connector_name, "SOL"
                    )
                )
            except Exception:
                pass
        return 0.0

    def _get_perp_short_balance(self) -> float:
        """Get current perpetual short position size."""
        if _HB_AVAILABLE and hasattr(self, "market_data_provider"):
            try:
                positions = self.market_data_provider.get_active_positions(
                    self.config.hedge_connector_name
                )
                for pos in positions:
                    if pos.trading_pair == self.config.hedge_trading_pair:
                        return abs(float(pos.amount))
            except Exception:
                pass
        return 0.0

    # ───────────────────────────────────────────────────────────────────────
    # Convenience: update_market_data() for standalone / backtest use
    # ───────────────────────────────────────────────────────────────────────

    def update_market_data(
        self,
        active_bin_id: int,
        bin_step_bps: int,
        spot_price: float,
        ohlc_candles: List[Dict[str, float]],
        pool_24h_volume_usd: float,
        pool_tvl_usd: float,
        spot_base_balance: float,
        perp_short_balance: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Standalone tick processor — mirrors the original controller interface.

        This method is used by run_agent.py, run_backtest.py, and tests.
        When running inside Hummingbot, determine_executor_actions() is called
        instead by the framework.
        """
        # 1. Volatility & Spread
        rv_gk = self.volatility_engine.calculate_garman_klass_volatility(ohlc_candles)
        half_bins, half_spread_pct = self.volatility_engine.compute_dynamic_bin_spread(
            realized_volatility=rv_gk,
            bin_step_bps=bin_step_bps,
            min_bins=self.config.min_bins_spread,
            max_bins=self.config.max_bins_spread,
        )

        # 2. Range check
        is_in_range = True
        if self.active_position_bin_range:
            min_bin = self.active_position_bin_range["min_bin_id"]
            max_bin = self.active_position_bin_range["max_bin_id"]
            is_in_range = min_bin <= active_bin_id <= max_bin
        else:
            is_in_range = False

        # 3. Drawdown circuit breaker
        current_drawdown = (
            (self.peak_portfolio_value - self.current_portfolio_value)
            / max(self.peak_portfolio_value, 1.0)
        )
        if current_drawdown >= float(self.config.max_drawdown_stop_pct):
            return {
                "action": "EMERGENCY_STOP",
                "reason": f"Max drawdown exceeded: {current_drawdown*100:.2f}%",
                "orders": [{"action": "WITHDRAW_ALL"}, {"action": "CLOSE_ALL_HEDGES"}],
            }

        # 4. Churn gate
        should_rebalance, churn_telemetry = self.churn_gate.evaluate_rebalance(
            is_in_active_range=is_in_range,
            current_capital_usd=self.current_portfolio_value,
            sol_price_usd=spot_price,
            pool_24h_volume_usd=pool_24h_volume_usd,
            pool_tvl_usd=pool_tvl_usd,
        )

        orders = []

        # 5. Position management
        if self.active_position_bin_range is None or should_rebalance:
            momentum = 0.0
            if len(ohlc_candles) >= 2:
                momentum = (
                    (ohlc_candles[-1]["close"] - ohlc_candles[0]["open"])
                    / max(ohlc_candles[0]["open"], 1e-6)
                )

            bin_weights = self.bin_shaper.generate_distribution(
                active_bin_id=active_bin_id,
                half_width_bins=half_bins,
                mode=self.config.distribution_mode,
                short_term_momentum=momentum,
            )

            self.active_position_bin_range = {
                "min_bin_id": active_bin_id - half_bins,
                "max_bin_id": active_bin_id + half_bins,
            }

            orders.append(
                {
                    "action": "DEPLOY_DLMM_POSITION",
                    "connector": self.config.connector_name,
                    "pool_address": self.config.pool_address,
                    "active_bin_id": active_bin_id,
                    "min_bin_id": active_bin_id - half_bins,
                    "max_bin_id": active_bin_id + half_bins,
                    "bin_weights": bin_weights,
                    "capital_quote": float(self.config.total_amount_quote),
                }
            )
            self.status = "ACTIVE_LP"

        # 6. Delta hedge
        if self.config.enable_delta_hedge:
            hedge_order, hedge_telemetry = self.delta_hedge_executor.compute_hedge_action(
                spot_base_amount=spot_base_balance,
                spot_price_usd=spot_price,
                current_perp_short_amount=perp_short_balance,
                total_strategy_capital_usd=self.current_portfolio_value,
            )
            if hedge_order:
                orders.append({"action": "EXECUTE_PERP_HEDGE", "payload": hedge_order})
        else:
            hedge_telemetry = {"enabled": False}

        return {
            "status": getattr(self, "status", "ACTIVE_LP"),
            "realized_volatility": round(rv_gk, 6),
            "target_half_bins": half_bins,
            "half_spread_pct": round(half_spread_pct * 100, 3),
            "is_in_range": is_in_range,
            "churn_telemetry": churn_telemetry,
            "hedge_telemetry": hedge_telemetry,
            "generated_orders": orders,
        }
