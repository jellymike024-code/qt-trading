import numpy as np
import pandas as pd
import pytest

from quantlab.strategies import STRATEGIES, generate_signals, min_history


def bars(symbol="510300", n=260, seed=8):
    rng = np.random.default_rng(seed)
    close = 4 * np.exp(np.cumsum(rng.normal(0.001, 0.018, n)))
    return pd.DataFrame({
        "timestamp": pd.date_range("2022-01-04", periods=n, freq="B", tz="UTC"),
        "symbol": symbol,
        "open": close * 0.999,
        "high": close * 1.004,
        "low": close * 0.996,
        "close": close,
        "volume": rng.integers(100_000, 300_000, n),
    })


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_future_bars_cannot_change_past_scores(strategy):
    data = bars()
    full = generate_signals(data, strategy)
    for length in (60, 121, 180, 237):
        prefix = generate_signals(data.iloc[:length], strategy)
        np.testing.assert_array_equal(prefix.score, full.score.iloc[:length])


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_symbols_are_independent_and_input_order_is_preserved(strategy):
    first, second = bars(), bars("159915", seed=17)
    combined = pd.concat([first, second], ignore_index=True).sample(frac=1, random_state=3)
    actual = generate_signals(combined, strategy)
    expected = pd.concat([generate_signals(first, strategy), generate_signals(second, strategy)])
    actual_sorted = actual.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    expected_sorted = expected.sort_values(["symbol", "timestamp"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(actual_sorted, expected_sorted)
    pd.testing.assert_series_equal(actual.symbol, combined.symbol)
    pd.testing.assert_series_equal(actual.timestamp, combined.timestamp)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_scores_are_long_only_finite_and_warmup_is_cash(strategy):
    data = bars()
    scores = generate_signals(data, strategy).score
    assert np.isfinite(scores).all()
    assert scores.between(0, 1).all()
    assert (scores.iloc[:min_history(strategy) - 1] == 0).all()


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_steady_uptrend_produces_long_signal(strategy):
    data = bars()
    data["close"] = np.linspace(4, 9, len(data))
    data["open"] = data.close
    data["high"] = data.close + 0.005
    data["low"] = data.close - 0.005
    assert generate_signals(data, strategy).score.iloc[-1] > 0


def test_donchian_holds_between_entry_and_exit():
    data = bars(n=80)
    close = np.full(80, 4.0)
    close[60:65] = 4.2
    close[65:70] = 4.1
    close[70:] = 3.8
    data["close"] = close
    data["high"] = close + 0.01
    data["low"] = close - 0.01
    data["open"] = close
    scores = generate_signals(data, "donchian").score
    assert (scores.iloc[:60] == 0).all()
    assert (scores.iloc[60:70] == 1).all()
    assert (scores.iloc[70:] == 0).all()


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_adjusted_signal_prices_scale_channels_consistently(strategy):
    data = bars()
    expected = generate_signals(data, strategy).score
    data["signal_close"] = data.close * 0.5
    actual = generate_signals(data, strategy).score
    np.testing.assert_array_equal(actual, expected)


def test_invalid_strategy_or_bars_fail_explicitly():
    with pytest.raises(ValueError, match="Unknown strategy"):
        generate_signals(bars(), "bogus")
    duplicate = pd.concat([bars(), bars().iloc[:1]])
    with pytest.raises(ValueError, match="Duplicate"):
        generate_signals(duplicate, "ma_trend")
    invalid = bars()
    invalid.loc[0, "close"] = np.nan
    with pytest.raises(ValueError, match="finite"):
        generate_signals(invalid, "ma_trend")
