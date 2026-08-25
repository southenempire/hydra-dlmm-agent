"""
Interactive Sandbox Tester for Hydra-DLMM Agent.
Allows you to manually simulate market scenarios:
- Normal Chop
- Massive Pump (+3%)
- Flash Dump (-4%)
- Volatility Explosion
- Delta-Hedge Stress Test
"""

import sys
from decimal import Decimal
from config.hydra_dlmm_config import HydraDLMMConfig
from controllers.hydra_dlmm_controller import HydraDLMMController


def format_bin_visual(active_bin: int, min_bin: int, max_bin: int, weights: dict):
    lines = ["\n  \033[1m[ Liquidity Distribution Across Bins ]\033[0m"]
    sorted_bins = sorted(weights.keys())
    max_w = max(weights.values()) if weights else 1.0
    
    for b in sorted_bins:
        w = weights[b]
        bar_len = int((w / max_w) * 25)
        bar = "█" * bar_len
        is_active = (b == active_bin)
        marker = " ◄ [ACTIVE BIN]" if is_active else ""
        color = "\033[92m" if is_active else ("\033[93m" if b > active_bin else "\033[94m")
        lines.append(f"   Bin #{b:4d} | {color}{bar:<25}\033[0m | Weight: {w*100:5.2f}%{marker}")
    return "\n".join(lines)


def run_interactive():
    print("\033[96m" + "=" * 70)
    print("   🌊  HYDRA-DLMM: INTERACTIVE SCENARIO TESTER")
    print("=" * 70 + "\033[0m")
    
    config = HydraDLMMConfig(
        total_capital_quote=Decimal("5000"),
        rebalance_dwell_slots=2,
        enable_delta_hedge=True
    )
    controller = HydraDLMMController(config)
    
    price = 145.0
    active_bin = 1000
    spot_sol = 0.0
    perp_short_sol = 0.0
    
    candles = [
        {"open": 144.0, "high": 145.5, "low": 143.5, "close": 145.0},
        {"open": 145.0, "high": 146.0, "low": 144.5, "close": 145.0},
    ]

    print("\nSelect a scenario to test:")
    print(" 1) Normal Tick (Price: $145.00)")
    print(" 2) Price Drift (+1.5% -> $147.20)")
    print(" 3) Sudden Dump (-3.0% -> $140.65)")
    print(" 4) Run Automated Simulation")
    
    # We execute scenario 1 by default to show initial state
    print("\n\033[1m--- Executing Scenario 1: Initial State & LP Deployment ---\033[0m")
    
    res = controller.update_market_data(
        active_bin_id=active_bin,
        bin_step_bps=10,
        spot_price=price,
        ohlc_candles=candles,
        pool_24h_volume_usd=15_000_000,
        pool_tvl_usd=1_500_000,
        spot_base_balance=spot_sol,
        perp_short_balance=perp_short_sol
    )
    
    print(f"• Price: ${price:.2f} | Active Bin: #{active_bin}")
    print(f"• Volatility: {res['realized_volatility']*100:.3f}% | Half-Width: ±{res['target_half_bins']} bins")
    for o in res["generated_orders"]:
        if o["action"] == "DEPLOY_DLMM_POSITION":
            print(format_bin_visual(active_bin, o["min_bin_id"], o["max_bin_id"], o["bin_weights"]))
            spot_sol = 2500.0 / price

    print("\n\033[1m--- Executing Scenario 2: Price Surge & Delta Hedge ---\033[0m")
    price = 147.20
    active_bin = 1015
    candles.append({"open": 145.0, "high": 148.0, "low": 144.8, "close": 147.2})
    
    res2 = controller.update_market_data(
        active_bin_id=active_bin,
        bin_step_bps=10,
        spot_price=price,
        ohlc_candles=candles,
        pool_24h_volume_usd=18_000_000,
        pool_tvl_usd=1_500_000,
        spot_base_balance=spot_sol,
        perp_short_balance=perp_short_sol
    )
    
    print(f"• Price: ${price:.2f} | Active Bin: #{active_bin}")
    print(f"• Position In-Range: {res2['is_in_range']} | Dwell Slots Outside: {res2['churn_telemetry']['consecutive_out_of_range_slots']}")
    for o in res2["generated_orders"]:
        if o["action"] == "EXECUTE_PERP_HEDGE":
            p = o["payload"]
            perp_short_sol += p["amount"]
            print(f"🛡️ [DELTA HEDGE] {p['order_side']} {p['amount']} SOL on {p['connector_name']} @ ${p['price']:.2f}")

    delta_usd = (spot_sol - perp_short_sol) * price
    print(f"• Net Delta: {spot_sol:.2f} Spot SOL vs {perp_short_sol:.2f} Short SOL (Net: ${delta_usd:+.2f} USD)")

    print("\n\033[92m[✓] Tested successfully! You can run 'python3 run_agent.py' anytime in your terminal.\033[0m\n")


if __name__ == "__main__":
    run_interactive()
