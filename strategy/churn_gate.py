"""
Economic Churn Gate for Hydra-DLMM.
Guards against the rebalance whipsaw by enforcing slot dwell-time confirmation
and economic profitability hurdles before authorizing on-chain position teardown.
"""

from decimal import Decimal
from typing import Dict, Any, Tuple


class EconomicChurnGate:
    """Validates whether a position rebalance is economically rational."""

    def __init__(
        self,
        dwell_slots_threshold: int = 5,
        min_fee_improvement_ratio: Decimal = Decimal("1.25"),
        estimated_priority_fee_sol: Decimal = Decimal("0.0005"),
        max_slippage_bps: int = 25
    ):
        self.dwell_slots_threshold = dwell_slots_threshold
        self.min_fee_improvement_ratio = float(min_fee_improvement_ratio)
        self.estimated_priority_fee_sol = float(estimated_priority_fee_sol)
        self.max_slippage_bps = max_slippage_bps
        self._consecutive_out_of_range_slots = 0

    def record_slot_state(self, is_in_active_range: bool) -> int:
        """Updates internal slot dwell counter."""
        if is_in_active_range:
            self._consecutive_out_of_range_slots = 0
        else:
            self._consecutive_out_of_range_slots += 1
        return self._consecutive_out_of_range_slots

    def evaluate_rebalance(
        self,
        is_in_active_range: bool,
        current_capital_usd: float,
        sol_price_usd: float,
        pool_24h_volume_usd: float,
        pool_tvl_usd: float,
        pool_fee_pct: float = 0.0025,
        expected_dwell_hours: float = 4.0
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Evaluates whether an on-chain rebalance should proceed.
        
        Returns:
            Tuple of (should_rebalance: bool, telemetry: dict)
        """
        dwell_count = self.record_slot_state(is_in_active_range)
        
        # 1. Dwell Time Gate
        passed_dwell_gate = dwell_count >= self.dwell_slots_threshold
        
        # 2. Cost Estimation
        # On-chain transactions: Withdraw, Swap Rebalance (Jupiter), Deposit (Meteora)
        tx_count = 3
        priority_fee_cost_usd = tx_count * self.estimated_priority_fee_sol * sol_price_usd
        
        # Slippage cost on rebalancing swap (approx 50% of capital swapped)
        swap_amount = current_capital_usd * 0.5
        slippage_cost_usd = swap_amount * (self.max_slippage_bps / 10000.0)
        
        # Perp micro-hedge taker fee (approx 2-4 bps)
        perp_hedge_cost_usd = current_capital_usd * 0.0004
        
        total_rebalance_cost_usd = priority_fee_cost_usd + slippage_cost_usd + perp_hedge_cost_usd
        
        # 3. Expected Incremental Fee Gain Estimation
        # Pool hourly fee pool = (24h_volume * fee_pct) / 24
        hourly_fee_pool = (pool_24h_volume_usd * pool_fee_pct) / 24.0
        # Agent share of active TVL (with 50x concentration multiplier)
        agent_effective_tvl = current_capital_usd * 50.0
        agent_tvl_share = min(1.0, agent_effective_tvl / max(pool_tvl_usd + agent_effective_tvl, 1.0))
        
        expected_incremental_fees_usd = hourly_fee_pool * agent_tvl_share * expected_dwell_hours
        
        # 4. Economic Benefit Hurdle
        economic_hurdle_passed = expected_incremental_fees_usd >= (
            total_rebalance_cost_usd * self.min_fee_improvement_ratio
        )
        
        should_rebalance = (not is_in_active_range) and passed_dwell_gate and economic_hurdle_passed
        
        telemetry = {
            "is_in_active_range": is_in_active_range,
            "consecutive_out_of_range_slots": dwell_count,
            "passed_dwell_gate": passed_dwell_gate,
            "total_rebalance_cost_usd": round(total_rebalance_cost_usd, 4),
            "expected_incremental_fees_usd": round(expected_incremental_fees_usd, 4),
            "benefit_to_cost_ratio": round(
                expected_incremental_fees_usd / max(total_rebalance_cost_usd, 1e-4), 2
            ),
            "economic_hurdle_passed": economic_hurdle_passed,
            "decision": "EXECUTE_REBALANCE" if should_rebalance else "HOLD_POSITION"
        }
        
        return should_rebalance, telemetry
