"""
Comparative Backtesting Engine for Hydra-DLMM.
Simulates high-volatility Solana market regimes over 1,000 ticks and benchmarks:
1. Baseline A: Static Unhedged Concentrated LP
2. Baseline B: Naive Immediate-Rebalance Bot (Whipsawed)
3. Hydra-DLMM: Volatility-Adaptive Bins + Economic Churn Gate + Perp Delta-Hedge
"""

import math
import random
from decimal import Decimal
from typing import Dict, List, Any, Tuple

from config.hydra_dlmm_config import HydraDLMMConfig
from controllers.hydra_dlmm_controller import HydraDLMMController


class BacktestEngine:
    """Simulates market micro-structure and benchmarks LP strategies."""

    def __init__(self, initial_capital_usd: float = 10_000.0, num_ticks: int = 1000):
        self.initial_capital_usd = initial_capital_usd
        self.num_ticks = num_ticks

    def generate_sol_market_series(self, start_price: float = 145.0) -> List[Dict[str, float]]:
        """
        Generates realistic 1-minute OHLC data for SOL/USDC including:
        - Low volatility consolidation (Ticks 0-250)
        - Upward trend & breakout (Ticks 250-500)
        - High-volatility mean-reverting chop (Ticks 500-750)
        - Flash dump and recovery (Ticks 750-1000)
        """
        random.seed(42)  # Deterministic seed for reproducible verification
        candles = []
        price = start_price

        for t in range(self.num_ticks):
            if t < 250:
                # Chop
                drift = random.uniform(-0.08, 0.08)
                vol = random.uniform(0.15, 0.35)
            elif t < 500:
                # Bull rally (+15%)
                drift = random.uniform(0.05, 0.25)
                vol = random.uniform(0.30, 0.60)
            elif t < 750:
                # High volatility whipsaw
                drift = random.uniform(-0.40, 0.40)
                vol = random.uniform(0.60, 1.20)
            else:
                # Dump and recovery
                drift = random.uniform(-0.35, 0.25) if t < 850 else random.uniform(0.10, 0.35)
                vol = random.uniform(0.50, 0.90)

            open_p = price
            price += drift
            high_p = max(open_p, price) + abs(vol) * 0.6
            low_p = min(open_p, price) - abs(vol) * 0.6
            close_p = price

            candles.append({
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p
            })

        return candles

    def run_benchmark(self) -> Dict[str, Any]:
        """Runs comparative simulations across all three strategies."""
        candles = self.generate_sol_market_series()
        bin_step_bps = 10  # 0.10% per bin
        pool_24h_vol = 25_000_000.0
        pool_tvl = 2_500_000.0

        # --- 1. Baseline A: Static Unhedged LP (Fixed ±10 bins around start) ---
        static_start_price = candles[0]["open"]
        static_min_p = static_start_price * 0.99
        static_max_p = static_start_price * 1.01
        static_spot_sol = (self.initial_capital_usd * 0.5) / static_start_price
        static_usdc = self.initial_capital_usd * 0.5
        static_fees_accum = 0.0
        static_equity_curve = []

        # --- 2. Baseline B: Naive Rebalance Bot (No dwell gate, rebalances immediately on 1-tick breach) ---
        naive_price = static_start_price
        naive_min_p = naive_price * 0.99
        naive_max_p = naive_price * 1.01
        naive_spot_sol = (self.initial_capital_usd * 0.5) / naive_price
        naive_usdc = self.initial_capital_usd * 0.5
        naive_fees_accum = 0.0
        naive_gas_slippage_cost = 0.0
        naive_rebalances_count = 0
        naive_equity_curve = []

        # --- 3. Hydra-DLMM: Adaptive Bins + Economic Churn Gate + Delta Hedge ---
        config = HydraDLMMConfig(
            total_capital_quote=Decimal(str(self.initial_capital_usd)),
            rebalance_dwell_slots=4,
            enable_delta_hedge=True
        )
        hydra_controller = HydraDLMMController(config)
        hydra_fees_accum = 0.0
        hydra_gas_slippage_cost = 0.0
        hydra_rebalances_count = 0
        hydra_hedge_pnl = 0.0
        hydra_equity_curve = []
        hydra_spot_sol = 0.0
        hydra_perp_short_sol = 0.0

        # Run 1,000 tick simulation
        for i, candle in enumerate(candles):
            cur_price = candle["close"]
            active_bin_id = int(1000 + (cur_price - static_start_price) / (static_start_price * 0.001))
            hourly_fee_share = ((pool_24h_vol * 0.0025) / 24.0) / 60.0  # per minute fee pool

            # --- A. Process Static LP ---
            if static_min_p <= cur_price <= static_max_p:
                # In range: earns concentrated fee share
                static_fees_accum += hourly_fee_share * (self.initial_capital_usd * 50.0 / pool_tvl)
            # Unhedged equity fluctuates with underlying SOL price
            static_equity = (static_spot_sol * cur_price) + static_usdc + static_fees_accum
            static_equity_curve.append(static_equity)

            # --- B. Process Naive Bot ---
            if naive_min_p <= cur_price <= naive_max_p:
                naive_fees_accum += hourly_fee_share * (self.initial_capital_usd * 50.0 / pool_tvl)
            else:
                # Instant rebalance whip: pay slippage (20 bps) + gas
                naive_rebalances_count += 1
                cost = (self.initial_capital_usd * 0.5 * 0.0020) + 0.15  # gas + slippage
                naive_gas_slippage_cost += cost
                naive_min_p = cur_price * 0.99
                naive_max_p = cur_price * 1.01
                naive_spot_sol = (self.initial_capital_usd * 0.5) / cur_price

            naive_equity = (naive_spot_sol * cur_price) + naive_usdc + naive_fees_accum - naive_gas_slippage_cost
            naive_equity_curve.append(naive_equity)

            # --- C. Process Hydra-DLMM ---
            lookback_slice = candles[max(0, i-30):i+1]
            res = hydra_controller.update_market_data(
                active_bin_id=active_bin_id,
                bin_step_bps=bin_step_bps,
                spot_price=cur_price,
                ohlc_candles=lookback_slice,
                pool_24h_volume_usd=pool_24h_vol,
                pool_tvl_usd=pool_tvl,
                spot_base_balance=hydra_spot_sol,
                perp_short_balance=hydra_perp_short_sol
            )

            # Calculate fees
            if res["is_in_range"]:
                # Volatility-scaled concentration multiplier (up to 120x)
                multiplier = 10000.0 / max(res["target_half_bins"] * bin_step_bps * 2, 10.0)
                hydra_fees_accum += hourly_fee_share * (self.initial_capital_usd * multiplier / pool_tvl)

            # Process Orders
            for o in res["generated_orders"]:
                if o["action"] == "DEPLOY_DLMM_POSITION":
                    hydra_rebalances_count += 1
                    # Gated rebalance cost
                    hydra_gas_slippage_cost += (self.initial_capital_usd * 0.5 * 0.0015) + 0.10
                    hydra_spot_sol = (self.initial_capital_usd * 0.5) / cur_price
                elif o["action"] == "EXECUTE_PERP_HEDGE":
                    p = o["payload"]
                    if p["order_side"] == "SELL":
                        hydra_perp_short_sol += p["amount"]
                    else:
                        hydra_perp_short_sol -= p["amount"]

            # Hedge P&L offsets spot delta
            delta_error = hydra_spot_sol - hydra_perp_short_sol
            hydra_hedge_pnl = hydra_perp_short_sol * (static_start_price - cur_price)
            spot_val = hydra_spot_sol * cur_price
            hydra_equity = (self.initial_capital_usd * 0.5) + spot_val + hydra_hedge_pnl + hydra_fees_accum - hydra_gas_slippage_cost
            hydra_equity_curve.append(hydra_equity)

        return {
            "summary": {
                "initial_capital": self.initial_capital_usd,
                "ticks": self.num_ticks,
                "start_price": static_start_price,
                "end_price": candles[-1]["close"],
                "price_change_pct": round(((candles[-1]["close"] - static_start_price) / static_start_price) * 100, 2)
            },
            "static_lp": self._compute_metrics(static_equity_curve, static_fees_accum, 0, 0),
            "naive_rebalance_lp": self._compute_metrics(naive_equity_curve, naive_fees_accum, naive_gas_slippage_cost, naive_rebalances_count),
            "hydra_dlmm": self._compute_metrics(hydra_equity_curve, hydra_fees_accum, hydra_gas_slippage_cost, hydra_rebalances_count)
        }

    def _compute_metrics(self, equity_curve: List[float], fees: float, costs: float, rebalances: int) -> Dict[str, Any]:
        """Calculates institutional performance statistics."""
        initial = equity_curve[0]
        final = equity_curve[-1]
        net_return_pct = ((final - initial) / initial) * 100.0

        # Max Drawdown
        peak = initial
        max_dd_pct = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / max(peak, 1.0)
            if dd > max_dd_pct:
                max_dd_pct = dd

        # Returns Sharpe (annualized estimate)
        returns = [(equity_curve[j] - equity_curve[j-1]) / equity_curve[j-1] for j in range(1, len(equity_curve))]
        mean_r = sum(returns) / max(len(returns), 1)
        std_r = math.sqrt(sum((r - mean_r)**2 for r in returns) / max(len(returns), 1))
        sharpe = (mean_r / max(std_r, 1e-6)) * math.sqrt(525600)  # annualized 1-min intervals

        return {
            "final_equity": round(final, 2),
            "net_pnl_usd": round(final - initial, 2),
            "net_return_pct": round(net_return_pct, 2),
            "gross_fees_usd": round(fees, 2),
            "friction_costs_usd": round(costs, 2),
            "rebalances_count": rebalances,
            "max_drawdown_pct": round(max_dd_pct * 100, 2),
            "sharpe_ratio": round(sharpe, 2)
        }
