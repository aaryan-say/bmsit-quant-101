"""Engine tests. Run either way:

    python -m pytest tests            (if pytest is installed)
    python tests\\test_engine.py       (plain Python, no pytest needed)

What they prove:
  1. the three example strategies run and produce a finite scorecard
  2. NEXT-DAY execution: a strategy that turns long on the day of a known jump does NOT earn that jump
  3. costs reduce returns (same trades, lower equity)
  4. an obvious look-ahead strategy (shift(-1)) is rejected; if you force it through, its Sharpe is absurd
     -> this is exactly why the competition is judged on hidden July-September data
  5. guards: index mismatch rejected, NaN -> flat, |position| clipped to 1, df mutation harmless, --long-only
"""
import math
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import backtest as bt  # noqa: E402

_CACHE = {}


def data():
    if "frames" not in _CACHE:
        _CACHE["frames"], _CACHE["index"] = bt.load_data(os.path.join(ROOT, "data", "daily"))
    return _CACHE["frames"]


def jump_frame(n=60, jump_day=30, jump=0.20):
    """Flat price 100, then on `jump_day` the close jumps +20% (open still 100); flat at 120 afterwards."""
    dates = pd.bdate_range("2026-01-01", periods=n)
    px = np.where(np.arange(n) >= jump_day, 100 * (1 + jump), 100.0)
    open_ = px.copy()
    open_[jump_day] = 100.0           # the jump happens DURING jump_day (open 100 -> close 120)
    return pd.DataFrame({"date": dates, "open": open_, "high": px, "low": open_, "close": px, "volume": 1000})


def run(strategy, frames, **kw):
    return bt.run_backtest(strategy, frames, name="t", check_lookahead=False, **kw)["scorecard"]


# ------------------------------------------------------------------ 1. examples run
def test_examples_run():
    for f in ("ma_crossover", "zscore_meanrev", "breakout"):
        name, strat = bt.load_strategy(os.path.join(ROOT, "strategies", f + ".py"))
        sc = bt.run_backtest(strat, data(), name)["scorecard"]
        assert sc["trades"] > 10 and math.isfinite(sc["sharpe"]) and -100 < sc["max_drawdown_pct"] <= 0
        assert "SUBMIT |" in bt.submit_line(sc)


# ------------------------------------------------------------------ 2. next-day execution
def test_next_day_execution_does_not_capture_signal_day_jump():
    df = jump_frame()
    long_from_jump_day = lambda d: pd.Series((np.arange(len(d)) >= 30).astype(float), index=d.index)
    long_from_day_before = lambda d: pd.Series((np.arange(len(d)) >= 29).astype(float), index=d.index)
    sc_late = run(long_from_jump_day, {"X": df}, cost_per_order=0, slippage=0)
    sc_early = run(long_from_day_before, {"X": df}, cost_per_order=0, slippage=0)
    # signal on the jump day is executed at the NEXT open (already 120) -> no profit from the jump
    assert abs(sc_late["total_return_pct"]) < 1e-9, sc_late
    # a signal the day BEFORE is executed at the jump day's open (100) -> earns the +20%
    assert abs(sc_early["total_return_pct"] - 20.0) < 1e-6, sc_early


# ------------------------------------------------------------------ 3. costs
def test_costs_reduce_returns():
    _, strat = bt.load_strategy(os.path.join(ROOT, "strategies", "ma_crossover.py"))
    gross = run(strat, data(), cost_per_order=0, slippage=0)
    net = run(strat, data())
    assert net["trades"] == gross["trades"]
    assert net["total_return_pct"] < gross["total_return_pct"] - 0.5     # 267 trades cost real money


# ------------------------------------------------------------------ 4. look-ahead
def lookahead_strategy(df):
    return np.sign(df["close"].shift(-1) - df["close"])         # "buy if tomorrow is up": cheating


def test_lookahead_is_rejected():
    try:
        bt.run_backtest(lookahead_strategy, data(), "cheat")
    except bt.StrategyError as e:
        assert "LOOK-AHEAD" in str(e)
    else:
        raise AssertionError("look-ahead strategy was not rejected")


def test_lookahead_scores_suspiciously_high_if_forced():
    cheat = run(lookahead_strategy, data())
    _, strat = bt.load_strategy(os.path.join(ROOT, "strategies", "zscore_meanrev.py"))
    honest = run(strat, data())
    # Even with next-day execution and costs, knowing tomorrow's close is a licence to print money.
    assert cheat["sharpe"] > 5 and cheat["sharpe"] > 3 * abs(honest["sharpe"]), (cheat, honest)


# ------------------------------------------------------------------ 5. guards
def test_index_mismatch_rejected():
    bad = lambda d: pd.Series(1.0, index=d.index[1:])
    try:
        bt.get_positions(bad, data()["RELIANCE"])
    except bt.StrategyError as e:
        assert "index" in str(e)
    else:
        raise AssertionError("mismatched index accepted")


def test_nan_is_flat_and_positions_clipped():
    df = data()["RELIANCE"]
    pos = bt.get_positions(lambda d: pd.Series([np.nan, 5.0, -7.0] + [0.5] * (len(d) - 3), index=d.index), df)
    assert list(pos.iloc[:3]) == [0.0, 1.0, -1.0] and pos.iloc[3] == 0.5
    pos = bt.get_positions(lambda d: pd.Series(-1.0, index=d.index), df, long_only=True)
    assert (pos == 0).all()


def test_mutating_df_is_harmless():
    frames = data()
    before = frames["RELIANCE"]["close"].copy()

    def vandal(d):
        d["close"] = 0.0
        d.drop(columns=["open"], inplace=True)
        return pd.Series(0.0, index=d.index)

    run(vandal, frames)
    assert frames["RELIANCE"]["close"].equals(before)


def test_long_only_flag():
    _, strat = bt.load_strategy(os.path.join(ROOT, "strategies", "ma_crossover.py"))
    both, lo = run(strat, data()), run(strat, data(), long_only=True)
    assert lo["exposure_pct"] < both["exposure_pct"] and lo["trades"] < both["trades"]


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS  {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {str(e)[:300]}")
    print("\nall tests passed" if not failed else f"\n{failed} test(s) FAILED")
    sys.exit(1 if failed else 0)
