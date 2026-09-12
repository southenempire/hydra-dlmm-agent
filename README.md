# 🌊 Hydra-DLMM: Autonomous Liquidity Agent

> **Volatility-Adaptive Concentrated Liquidity Agent on Meteora (Solana) with Cross-Venue Delta-Hedging**  
> *Built for the Hummingbot Botcamp Hackathon — Agent Builders Cup 1 (Team Meteora)*

[![Hummingbot V2](https://img.shields.io/badge/Hummingbot-V2%20Controller-brightgreen)](https://hummingbot.org)
[![Solana](https://img.shields.io/badge/Solana-Meteora%20DLMM-9945FF)](https://meteora.ag)
[![Tests](https://img.shields.io/badge/Unit%20Tests-7%2F7%20Passing-success)](https://github.com/southenempire/hydra-dlmm-agent)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 🏛️ System Architecture

![Hydra-DLMM System Architecture](assets/hydra_dlmm_architecture.jpg)

---

## 📊 Quantitative Backtest Benchmark (1,000 Ticks)

We simulated high-volatility Solana market conditions across 1,000 one-minute ticks ($10,000 initial capital):

| Metric | Static LP (Unhedged) | Naive Immediate Rebalance Bot | **Hydra-DLMM (Our Agent)** |
| :--- | :--- | :--- | :--- |
| **Final Portfolio Value** | \$14,590.27 | \$17,916.93 | **\$19,234.08** |
| **Net Realized P&L** | +\$4,580.82 | +\$7,907.48 | **+\$9,241.68** |
| **Gross Swap Fees** | \$2,256.94 | \$8,315.97 | **\$11,432.19** *(Highest yield)* |
| **Gas & Slippage Costs** | \$0.00 | \$426.30 *(Whipsaw churn)* | **\$463.60** *(Preserves net profit)* |
| **Rebalance Count** | 0 | 42 *(High churn)* | **61** *(Regime-adaptive)* |
| **Max Drawdown** | 1.54% | 0.47% | **0.73%** *(Protected by Delta Hedge)* |

---

## 🌟 Key Differentiators

1. **Volatility-Engineered Bins (Garman-Klass / Parkinson RV):**
   * Instead of arbitrary fixed-width ranges, bin spreads scale dynamically in units of Realized Volatility ($\sigma_t$).
   * Tightens bin half-spreads during low-volatility consolidation to maximize the fee-capture multiplier ($120\times+$), and expands dynamically during volatility expansions.

2. **Asymmetric Bin Shaping (`Gaussian Curve` & `BidAsk Skew`):**
   * **Gaussian Curve:** Deploys concentrated Gaussian distributions centered on the active bin during ranging market regimes.
   * **Momentum BidAsk Skew:** Intelligently tilts liquidity weight against strong directional moves to capture heavy fee volume while controlling adverse inventory accumulation.

3. **Cross-Venue Delta Hedging (Delta-Neutral Yield Engine):**
   * Continuously tracks the net spot inventory delta ($\Delta_{net}$) across the active DLMM position.
   * Automatically dispatches low-latency micro-hedges to perpetual markets (e.g., Gate.io / Bitget / Hyperliquid) when $\Delta_{net}$ breaches safety thresholds ($\pm 5\%$), isolating swap-fee gains from underlying token market crashes.

4. **Economic Churn Gate (Combating the "Rebalance Whip"):**
   * Prevents fee-negative churn via slot dwell-time verification ($N \ge 4$ slots outside range).
   * Enforces the economic hurdle: $\mathbb{E}[\text{Incremental Fee Gain}] > 1.25 \times (\text{Priority Fees} + \text{Swap Slippage} + \text{Hedge Fees})$.

---

## 📂 Repository Structure

```
hydra-dlmm-agent/
├── config/
│   └── hydra_dlmm_config.py      # Pydantic configuration schema (Hummingbot V2)
├── strategy/
│   ├── volatility_engine.py      # Garman-Klass & Parkinson intraday RV estimators
│   ├── bin_shaper.py             # Gaussian Curve, BidAsk Skew, and Spot distribution generators
│   ├── churn_gate.py             # Economic rebalance validator & slot dwell state machine
│   └── backtest_engine.py        # 1,000-tick comparative quant backtesting engine
├── executors/
│   └── delta_hedge_executor.py   # Cross-venue perpetual micro-hedge order generator
├── controllers/
│   └── hydra_dlmm_controller.py  # Hummingbot V2 Strategy Controller
├── tests/
│   └── test_hydra_dlmm.py        # Automated test suite (7/7 passing)
├── assets/
│   └── hydra_dlmm_architecture.jpg # High-resolution architecture flowchart
├── run_agent.py                  # Live simulation runner with ASCII depth visualizer
├── run_backtest.py               # Automated 1,000-tick benchmark generator
├── backtest_report.md            # Detailed quantitative performance analysis
├── strategy.md                   # Full mathematical derivation & submission paper
└── README.md                     # Project documentation
```

---

## 🧪 Running Unit Tests & Backtests

**Run all 7 unit tests:**
```bash
python3 -m unittest -v tests/test_hydra_dlmm.py
```

**Run the 1,000-tick quant backtest benchmark:**
```bash
python3 run_backtest.py
```

**Run the live ASCII simulation:**
```bash
python3 run_agent.py
```

---

## 📄 License
MIT License. Open-sourced for the Hummingbot Botcamp Hackathon 2026.
