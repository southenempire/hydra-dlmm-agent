"""
Configuration schema for Hydra-DLMM Strategy Controller.
Compatible with Hummingbot V2 Controller framework.
"""

from decimal import Decimal
from typing import Literal, Optional
from pydantic import BaseModel, Field


class HydraDLMMConfig(BaseModel):
    """Configuration model for Hydra-DLMM Controller."""
    controller_name: str = "hydra_dlmm"
    
    # Primary DEX & Pool Configuration
    connector_name: str = Field(
        default="meteora",
        description="DEX connector name in Hummingbot Gateway (e.g., meteora, orca)"
    )
    trading_pair: str = Field(
        default="SOL-USDC",
        description="Trading pair symbol (e.g. SOL-USDC)"
    )
    pool_address: str = Field(
        default="ARwi1S4DaiTG5DX7S4M4ZsrXqpMD1MrTMsonBK52BuJn",
        description="Solana address for the Meteora DLMM pool"
    )
    total_capital_quote: Decimal = Field(
        default=Decimal("1000"),
        description="Total capital allocated to this strategy in Quote asset (USDC)"
    )

    # Volatility Engine Parameters
    volatility_lookback_minutes: int = Field(
        default=30,
        description="Lookback window in minutes for Garman-Klass / Parkinson Realized Volatility"
    )
    target_bin_spread_sigma: Decimal = Field(
        default=Decimal("1.5"),
        description="Target half-spread width expressed in Realized Volatility units (sigma)"
    )
    min_bins_spread: int = Field(
        default=5,
        description="Minimum half-width in number of bins from active bin"
    )
    max_bins_spread: int = Field(
        default=35,
        description="Maximum half-width in number of bins from active bin"
    )

    # Bin Shaping & Distribution
    distribution_mode: Literal["dynamic", "curve", "bid_ask", "spot"] = Field(
        default="dynamic",
        description="Liquidity distribution shape across bins"
    )
    curve_concentration_factor: Decimal = Field(
        default=Decimal("2.0"),
        description="Gaussian steepness parameter for Curve distribution"
    )
    momentum_skew_threshold: Decimal = Field(
        default=Decimal("0.005"),
        description="Minimum price momentum to trigger asymmetric BidAsk skew"
    )

    # Economic Churn Gate & Rebalancing
    rebalance_dwell_slots: int = Field(
        default=5,
        description="Minimum consecutive slots outside active bin before triggering rebalance"
    )
    estimated_priority_fee_sol: Decimal = Field(
        default=Decimal("0.0005"),
        description="Estimated Solana priority fee in SOL per transaction"
    )
    max_slippage_bps: int = Field(
        default=25,
        description="Maximum slippage tolerance in basis points for swaps/rebalance"
    )
    min_net_fee_improvement_ratio: Decimal = Field(
        default=Decimal("1.25"),
        description="Minimum expected fee gain multiple over total rebalancing costs"
    )

    # Cross-Venue Delta Hedging
    enable_delta_hedge: bool = Field(
        default=True,
        description="Enable cross-venue perpetual hedging for delta neutrality"
    )
    hedge_connector_name: str = Field(
        default="gate_perpetual",
        description="Perpetual exchange connector (e.g. gate_perpetual, bitget_perpetual, hyperliquid_perpetual)"
    )
    hedge_trading_pair: str = Field(
        default="SOL-USDT",
        description="Trading pair on perpetual exchange for delta hedging"
    )
    delta_hedge_threshold_pct: Decimal = Field(
        default=Decimal("0.05"),
        description="Portfolio net delta skew (5%) before firing a micro-hedge"
    )
    hedge_leverage: int = Field(
        default=1,
        description="Target leverage multiplier for the hedging venue (1 = 1x delta-neutral)"
    )

    # Risk Management & Emergency Circuit Breakers
    max_drawdown_stop_pct: Decimal = Field(
        default=Decimal("0.03"),
        description="Hard stop-loss circuit breaker threshold (3% total drawdown)"
    )
    max_rpc_staleness_seconds: int = Field(
        default=15,
        description="Maximum allowable staleness for Solana RPC pool updates before pausing"
    )
