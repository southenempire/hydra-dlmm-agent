"""
Comprehensive Test Suite for Hydra-DLMM Agent.
Verifies Volatility Engine, Bin Shaper, Economic Churn Gate,
Delta Hedge Executor, Controller Orchestration, and Backtest Engine.
"""

import unittest
from decimal import Decimal
from config.hydra_dlmm_config import HydraDLMMConfig
from strategy.volatility_engine import VolatilityEngine
from strategy.bin_shaper import BinShaper
from strategy.churn_gate import EconomicChurnGate
from executors.delta_hedge_executor import DeltaHedgeExecutor
from controllers.hydra_dlmm_controller import HydraDLMMController
from strategy.backtest_engine import BacktestEngine


class TestHydraDLMM(unittest.TestCase):

    def setUp(self):
        self.sample_candles = [
            {"open": 140.0, "high": 142.5, "low": 139.5, "close": 141.2},
            {"open": 141.2, "high": 143.0, "low": 140.8, "close": 142.8},
            {"open": 142.8, "high": 144.1, "low": 142.0, "close": 143.5},
            {"open": 143.5, "high": 143.8, "low": 141.5, "close": 142.0},
        ]

    def test_volatility_engine(self):
        engine = VolatilityEngine(lookback_minutes=30, target_sigma=Decimal("1.5"))
        rv = engine.calculate_garman_klass_volatility(self.sample_candles)
        self.assertGreater(rv, 0.0)
        self.assertLess(rv, 0.20)

        bins, spread_pct = engine.compute_dynamic_bin_spread(rv, bin_step_bps=10, min_bins=5, max_bins=35)
        self.assertGreaterEqual(bins, 5)
        self.assertLessEqual(bins, 35)
        self.assertGreater(spread_pct, 0.0)

    def test_bin_shaper_curve(self):
        shaper = BinShaper(curve_concentration=2.0)
        active_bin = 1000
        weights = shaper.generate_distribution(active_bin, half_width_bins=5, mode="curve")
        self.assertEqual(len(weights), 11)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=5)
        self.assertEqual(max(weights, key=weights.get), active_bin)

    def test_bin_shaper_bid_ask_skew(self):
        shaper = BinShaper(momentum_threshold=0.005)
        active_bin = 1000
        weights = shaper.generate_distribution(active_bin, half_width_bins=5, mode="bid_ask", short_term_momentum=0.02)
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=5)
        ask_weights = sum(weights[b] for b in range(active_bin + 1, active_bin + 6))
        bid_weights = sum(weights[b] for b in range(active_bin - 5, active_bin))
        self.assertGreater(ask_weights, bid_weights)

    def test_churn_gate(self):
        gate = EconomicChurnGate(dwell_slots_threshold=3, min_fee_improvement_ratio=Decimal("1.25"))
        should_rebalance, telemetry = gate.evaluate_rebalance(
            is_in_active_range=False,
            current_capital_usd=1000,
            sol_price_usd=140,
            pool_24h_volume_usd=5_000_000,
            pool_tvl_usd=500_000
        )
        self.assertFalse(should_rebalance)
        self.assertEqual(telemetry["consecutive_out_of_range_slots"], 1)

        gate.evaluate_rebalance(False, 1000, 140, 5_000_000, 500_000)
        should_rebalance_3, telemetry_3 = gate.evaluate_rebalance(False, 1000, 140, 5_000_000, 500_000)
        self.assertTrue(should_rebalance_3)
        self.assertTrue(telemetry_3["passed_dwell_gate"])

    def test_delta_hedge_executor(self):
        hedger = DeltaHedgeExecutor(delta_threshold_pct=Decimal("0.05"))
        order, telemetry = hedger.compute_hedge_action(
            spot_base_amount=5.0,
            spot_price_usd=140.0,
            current_perp_short_amount=0.0,
            total_strategy_capital_usd=1000.0
        )
        self.assertIsNotNone(order)
        self.assertEqual(order["order_side"], "SELL")

    def test_full_controller_tick(self):
        config = HydraDLMMConfig(total_capital_quote=Decimal("1000"))
        controller = HydraDLMMController(config)
        result = controller.update_market_data(
            active_bin_id=1000,
            bin_step_bps=10,
            spot_price=142.0,
            ohlc_candles=self.sample_candles,
            pool_24h_volume_usd=10_000_000,
            pool_tvl_usd=1_000_000,
            spot_base_balance=3.5,
            perp_short_balance=0.0
        )
        self.assertEqual(result["status"], "ACTIVE_LP")

    def test_backtest_engine_benchmark(self):
        engine = BacktestEngine(initial_capital_usd=1000.0, num_ticks=50)
        res = engine.run_benchmark()
        self.assertIn("hydra_dlmm", res)
        self.assertIn("static_lp", res)
        self.assertIn("naive_rebalance_lp", res)
        self.assertGreater(res["hydra_dlmm"]["final_equity"], 0.0)


if __name__ == "__main__":
    unittest.main()
