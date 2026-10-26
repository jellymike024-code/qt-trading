from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import requests

from quantlab.data import demo_bars, fetch_eastmoney, fetch_tencent, load_bars, save_bars


def example_bars() -> pd.DataFrame:
    return pd.DataFrame([
        {"timestamp": "2024-01-03", "symbol": "000001", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 1000},
        {"timestamp": "2024-01-02", "symbol": "510300", "open": 2.0, "high": 2.2, "low": 1.9, "close": 2.1, "volume": 0},
    ])


def response_for(symbol: str, rows: list[str]) -> Mock:
    response = Mock()
    response.json.return_value = {"data": {"code": symbol, "klines": rows}}
    return response


class DataValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "bars.csv"

    def write(self, bars: pd.DataFrame) -> None:
        bars.to_csv(self.path, index=False)

    def test_load_preserves_leading_zero_sorts_and_normalizes_close(self) -> None:
        self.write(example_bars())
        bars = load_bars(self.path)
        self.assertEqual(bars["symbol"].tolist(), ["510300", "000001"])
        self.assertEqual(str(bars["timestamp"].dtype), "datetime64[ns, UTC]")
        self.assertEqual(bars.iloc[0]["timestamp"], pd.Timestamp("2024-01-02T07:00:00Z"))
        self.assertEqual(bars.iloc[1]["volume"], 1000)

    def test_timestamp_must_be_daily_close(self) -> None:
        bars = example_bars()
        bars.loc[0, "timestamp"] = "2024-01-03T14:59:00+08:00"
        self.write(bars)
        with self.assertRaisesRegex(ValueError, "15:00"):
            load_bars(self.path)

    def test_invalid_values_are_rejected(self) -> None:
        cases = [("open", 0), ("close", -1), ("high", 0.95), ("low", 1.05),
                 ("volume", -1), ("close", np.nan), ("volume", np.inf),
                 ("symbol", "12345"), ("timestamp", "2099-01-01")]
        for column, value in cases:
            with self.subTest(column=column, value=value):
                bars = example_bars()
                if column == "volume":
                    bars["volume"] = bars["volume"].astype(float)
                bars.loc[0, column] = value
                self.write(bars)
                with self.assertRaises(ValueError):
                    load_bars(self.path)

    def test_duplicate_days_are_rejected_after_timestamp_normalization(self) -> None:
        bars = example_bars().iloc[[0, 0]].reset_index(drop=True)
        bars.loc[1, "timestamp"] = "2024-01-03T07:00:00Z"
        self.write(bars)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_bars(self.path)

    def test_missing_columns_and_empty_data_are_rejected(self) -> None:
        self.write(example_bars().drop(columns="close"))
        with self.assertRaisesRegex(ValueError, "missing"):
            load_bars(self.path)
        self.write(example_bars().iloc[:0])
        with self.assertRaisesRegex(ValueError, "empty"):
            load_bars(self.path)

    def test_save_roundtrip_and_atomic_failure_leave_old_file(self) -> None:
        bars = example_bars()
        bars["signal_close"] = [1.3, 2.5]
        save_bars(bars, self.path)
        loaded = load_bars(self.path)
        self.assertEqual(loaded["signal_close"].tolist(), [2.5, 1.3])
        original = self.path.read_bytes()
        with patch("quantlab.data.os.replace", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                save_bars(bars, self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_demo_is_deterministic_and_uses_four_complete_streams(self) -> None:
        first, second = demo_bars(), demo_bars()
        pd.testing.assert_frame_equal(first, second)
        self.assertEqual(len(first), 2400)
        self.assertEqual(first.groupby("symbol").size().to_dict(), {"159915": 600, "510300": 600, "510500": 600, "588000": 600})


class EastmoneyProviderTests(unittest.TestCase):
    rows = ["2024-01-02,1.0,1.1,1.2,0.9,123,999,0,0,0,0", "2024-01-03,1.1,1.2,1.3,1.0,456,999,0,0,0,0"]
    adjusted = ["2024-01-02,1.1,1.2,1.3,1.0,123,999,0,0,0,0", "2024-01-03,1.2,1.3,1.4,1.1,456,999,0,0,0,0"]

    @patch("quantlab.data._now_shanghai", return_value=pd.Timestamp("2024-01-04T16:00:00+08:00"))
    @patch("quantlab.data.requests.get")
    def test_parse_lots_adjusted_feature_and_exchange_mapping(self, get: Mock, now: Mock) -> None:
        get.side_effect = [response_for("510300", self.rows), response_for("510300", self.adjusted),
                           response_for("159915", self.rows), response_for("159915", self.adjusted)]
        bars = fetch_eastmoney(["510300", "159915"], "20240101", "2024-01-04")
        self.assertEqual(len(bars), 4)
        self.assertEqual(bars.iloc[0]["close"], 1.1)
        self.assertEqual(bars.iloc[0]["signal_close"], 1.2)
        self.assertEqual(bars.iloc[0]["volume"], 12300)
        self.assertEqual(bars.iloc[0]["timestamp"], pd.Timestamp("2024-01-02T07:00:00Z"))
        self.assertEqual([call.kwargs["params"]["secid"] for call in get.call_args_list], ["1.510300", "1.510300", "0.159915", "0.159915"])
        self.assertEqual([call.kwargs["params"]["fqt"] for call in get.call_args_list], ["0", "1", "0", "1"])

    @patch("quantlab.data.requests.get")
    def test_today_is_excluded_before_close_buffer_and_included_after(self, get: Mock) -> None:
        for clock, count in [("2024-01-03T15:09:59+08:00", 1), ("2024-01-03T15:10:00+08:00", 2)]:
            with self.subTest(clock=clock), patch("quantlab.data._now_shanghai", return_value=pd.Timestamp(clock)):
                get.side_effect = [response_for("510300", self.rows), response_for("510300", self.adjusted)]
                self.assertEqual(len(fetch_eastmoney(["510300"], "20240101", "20240103")), count)

    @patch("quantlab.data._now_shanghai", return_value=pd.Timestamp("2024-01-02T16:00:00+08:00"))
    @patch("quantlab.data.requests.get")
    def test_future_returned_bar_is_rejected(self, get: Mock, now: Mock) -> None:
        get.return_value = response_for("510300", self.rows)
        with self.assertRaisesRegex(ValueError, "future"):
            fetch_eastmoney(["510300"], "20240101", "20240104")

    @patch("quantlab.data._now_shanghai", return_value=pd.Timestamp("2024-01-04T16:00:00+08:00"))
    @patch("quantlab.data.requests.get")
    def test_adjusted_date_mismatch_is_rejected(self, get: Mock, now: Mock) -> None:
        get.side_effect = [response_for("510300", self.rows), response_for("510300", self.adjusted[:1])]
        with self.assertRaisesRegex(ValueError, "dates do not match"):
            fetch_eastmoney(["510300"], "20240101", "20240104")

    @patch("quantlab.data._now_shanghai", return_value=pd.Timestamp("2024-01-04T16:00:00+08:00"))
    @patch("quantlab.data.requests.get")
    def test_latest_dates_must_match_across_symbols(self, get: Mock, now: Mock) -> None:
        get.side_effect = [response_for("510300", self.rows), response_for("510300", self.adjusted),
                           response_for("159915", self.rows[:1]), response_for("159915", self.adjusted[:1])]
        with self.assertRaisesRegex(ValueError, "latest"):
            fetch_eastmoney(["510300", "159915"], "20240101", "20240104")

    @patch("quantlab.data.requests.get")
    def test_network_failure_does_not_return_demo_or_partial_data(self, get: Mock) -> None:
        get.side_effect = requests.ConnectionError("offline")
        with self.assertRaisesRegex(ValueError, "market data fetch failed"):
            fetch_eastmoney(["510300"], "20240101", "20240104")

    @patch("quantlab.data.requests.get")
    def test_missing_symbol_data_is_rejected(self, get: Mock) -> None:
        response = Mock()
        response.json.return_value = {"data": None}
        get.return_value = response
        with self.assertRaisesRegex(ValueError, "no bars"):
            fetch_eastmoney(["510300"], "20240101", "20240104")


if __name__ == "__main__":
    unittest.main()


@patch("quantlab.data._now_shanghai", return_value=pd.Timestamp("2024-01-04T16:00:00+08:00"))
@patch("quantlab.data.requests.get")
def test_tencent_raw_prices_adjusted_features_and_lot_units(get, clock):
    raw, adj = Mock(), Mock()
    raw.json.return_value = {"data": {"sh510300": {"day": [["2024-01-02", "3", "3.1", "3.2", "2.9", "1200"]]}}}
    adj.json.return_value = {"data": {"sh510300": {"qfqday": [["2024-01-02", "2", "2.1", "2.2", "1.9", "1200"]]}}}
    get.side_effect = [raw, adj]
    bars = fetch_tencent(["510300"], "2024-01-01", "2024-01-03")
    assert bars.iloc[0]["close"] == 3.1
    assert bars.iloc[0]["signal_close"] == 2.1
    assert bars.iloc[0]["volume"] == 120000


@patch("quantlab.data._now_shanghai", return_value=pd.Timestamp("2024-01-04T16:00:00+08:00"))
@patch("quantlab.data.requests.get")
def test_tencent_adjusted_date_mismatch_rejected(get, clock):
    raw, adj = Mock(), Mock()
    raw.json.return_value = {"data": {"sh510300": {"day": [["2024-01-02", "3", "3.1", "3.2", "2.9", "1200"]]}}}
    adj.json.return_value = {"data": {"sh510300": {"qfqday": [["2024-01-03", "2", "2.1", "2.2", "1.9", "1200"]]}}}
    get.side_effect = [raw, adj]
    with unittest.TestCase().assertRaisesRegex(ValueError, "dates differ"):
        fetch_tencent(["510300"], "2024-01-01", "2024-01-03")
