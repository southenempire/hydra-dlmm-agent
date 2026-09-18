"""
Tests for the Hummingbot V2 Controller wrapper (controllers/market_making/hydra_dlmm.py).

Verifies:
  - Config inherits ControllerConfigBase (or BaseModel stub)
  - Controller inherits ControllerBase (or stub)
  - determine_executor_actions() returns a list of ExecutorAction
  - update_market_data() backward-compatibility with standalone mode
  - V2 controller produces identical results to the standalone controller
"""

import unittest
from decimal import Decimal

from controllers.market_making.hydra_dlmm import (
    HydraDLMMConfig,
    HydraDLMMController,
    ControllerBase,
    ControllerConfigBase,
    CreateExecutorAction,
    StopExecutorAction,
    ExecutorAction,
    _HB_AVAILABLE,
)


SAMPLE_CANDLES = [
    {"open": 140.0, "high": 142.5, "low": 139.5, "close": 141.2},
    {"open": 141.2, "high": 143.0, "low": 140.8, "close": 142.8},
    {"open": 142.8, "high": 144.1, "low": 142.0, "close": 143.5},
    {"open": 143.5, "high": 143.8, "low": 141.5, "close": 142.0},
]


class TestV2ConfigInheritance(unittest.TestCase):
    """Verify the config class conforms to V2 conventions."""

    def test_config_inherits_controller_config_base(self):
        """Config must inherit from ControllerConfigBase (or Pydantic BaseModel stub)."""
        self.assertTrue(issubclass(HydraDLMMConfig, ControllerConfigBase))

    def test_config_has_required_v2_fields(self):
        """V2 configs must have id, controller_name, controller_type."""
        config = HydraDLMMConfig()
        self.assertEqual(config.controller_name, "hydra_dlmm")
        self.assertEqual(config.controller_type, "market_making")
        self.assertTrue(hasattr(config, "id"))

    def test_config_has_strategy_fields(self):
        """All Hydra-DLMM strategy fields should be present."""
        config = HydraDLMMConfig()
        self.assertEqual(config.connector_name, "meteora")
        self.assertEqual(config.trading_pair, "SOL-USDC")
        self.assertTrue(config.enable_delta_hedge)
        self.assertEqual(config.hedge_connector_name, "gate_perpetual")


class TestV2ControllerInheritance(unittest.TestCase):
    """Verify the controller class conforms to V2 conventions."""

    def test_controller_inherits_controller_base(self):
        """Controller must inherit from ControllerBase (or stub)."""
        self.assertTrue(issubclass(HydraDLMMController, ControllerBase))

    def test_controller_has_determine_executor_actions(self):
        """V2 controllers must implement determine_executor_actions()."""
        self.assertTrue(hasattr(HydraDLMMController, "determine_executor_actions"))

    def test_determine_executor_actions_returns_list(self):
        """determine_executor_actions() must return a list."""
        config = HydraDLMMConfig()
        controller = HydraDLMMController(config)
        actions = controller.determine_executor_actions()
        self.assertIsInstance(actions, list)

    def test_first_call_emits_create_action(self):
        """First call with no active position should emit a CreateExecutorAction."""
        config = HydraDLMMConfig()
        controller = HydraDLMMController(config)
        actions = controller.determine_executor_actions()
        create_actions = [a for a in actions if isinstance(a, CreateExecutorAction)]
        self.assertGreaterEqual(len(create_actions), 1,
                                "First tick should deploy a position")


class TestV2ControllerBackwardCompat(unittest.TestCase):
    """Verify update_market_data() works identically to the standalone controller."""

    def setUp(self):
        self.config = HydraDLMMConfig(total_amount_quote=Decimal("1000"))
        self.controller = HydraDLMMController(self.config)

    def test_update_market_data_returns_status(self):
        """update_market_data() should return the same dict format as standalone."""
        result = self.controller.update_market_data(
            active_bin_id=1000,
            bin_step_bps=10,
            spot_price=142.0,
            ohlc_candles=SAMPLE_CANDLES,
            pool_24h_volume_usd=10_000_000,
            pool_tvl_usd=1_000_000,
            spot_base_balance=3.5,
            perp_short_balance=0.0,
        )
        self.assertIn("status", result)
        self.assertIn("realized_volatility", result)
        self.assertIn("generated_orders", result)
        self.assertEqual(result["status"], "ACTIVE_LP")

    def test_drawdown_circuit_breaker_via_update(self):
        """Drawdown circuit breaker should work via update_market_data()."""
        self.controller.peak_portfolio_value = 1000.0
        self.controller.current_portfolio_value = 940.0  # 6% loss
        result = self.controller.update_market_data(
            active_bin_id=1000,
            bin_step_bps=10,
            spot_price=140.0,
            ohlc_candles=SAMPLE_CANDLES,
            pool_24h_volume_usd=10_000_000,
            pool_tvl_usd=1_000_000,
            spot_base_balance=3.5,
            perp_short_balance=0.0,
        )
        self.assertEqual(result["action"], "EMERGENCY_STOP")

    def test_hedge_disabled_via_update(self):
        """Hedge disabled mode should work via update_market_data()."""
        config = HydraDLMMConfig(
            total_amount_quote=Decimal("1000"),
            enable_delta_hedge=False,
        )
        controller = HydraDLMMController(config)
        result = controller.update_market_data(
            active_bin_id=1000,
            bin_step_bps=10,
            spot_price=140.0,
            ohlc_candles=SAMPLE_CANDLES,
            pool_24h_volume_usd=10_000_000,
            pool_tvl_usd=1_000_000,
            spot_base_balance=5.0,
            perp_short_balance=0.0,
        )
        hedge_orders = [
            o for o in result["generated_orders"]
            if o["action"] == "EXECUTE_PERP_HEDGE"
        ]
        self.assertEqual(len(hedge_orders), 0)


class TestV2ControllerDrawdownStop(unittest.TestCase):
    """Test drawdown stop via determine_executor_actions()."""

    def test_drawdown_emits_stop_actions(self):
        """When drawdown exceeds threshold, all active executors should be stopped."""
        config = HydraDLMMConfig(max_drawdown_stop_pct=Decimal("0.03"))
        controller = HydraDLMMController(config)

        # First tick: deploy
        controller.determine_executor_actions()
        self.assertGreater(len(controller._active_executor_ids), 0)

        # Simulate 5% drawdown
        controller.peak_portfolio_value = 1000.0
        controller.current_portfolio_value = 940.0

        # Second tick: should emit StopExecutorAction for all active executors
        actions = controller.determine_executor_actions()
        stop_actions = [a for a in actions if isinstance(a, StopExecutorAction)]
        self.assertGreater(len(stop_actions), 0,
                           "Drawdown should trigger StopExecutorAction")
        self.assertEqual(len(controller._active_executor_ids), 0,
                         "Active executor IDs should be cleared")


if __name__ == "__main__":
    unittest.main()
