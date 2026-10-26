from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd

from .data import demo_bars, fetch_eastmoney, fetch_tencent, load_bars, save_bars
from .engine import metrics, run_backtest, stress_config
from .paper import update_paper
from .report import export_accounts, render_report
from .sentiment import load_events
from .storage import experiment_hash, read_json, validate_config, write_json

ROOT = Path(__file__).resolve().parent.parent


def now():
    return pd.Timestamp.now(tz="Asia/Shanghai")


def fetch(config, data_dir):
    clock = now()
    provider = config.get("data_provider", "eastmoney_public")
    loaders = {"eastmoney_public": fetch_eastmoney, "tencent_public": fetch_tencent}
    if provider not in loaders:
        raise ValueError("unsupported public data provider")
    bars = loaders[provider](config["symbols"], config["history_start"], clock.strftime("%Y-%m-%d"))
    save_bars(bars, data_dir / "bars.csv")
    snapshot = data_dir / "snapshots" / (clock.strftime("%Y%m%d_%H%M%S") + ".csv")
    save_bars(bars, snapshot)
    manifest = {"provider": provider, "fetched_at": str(clock),
                "latest_session": str(bars.timestamp.max()), "rows": len(bars),
                "volume_unit": "shares", "execution_prices": "unadjusted",
                "signal_prices": "current_snapshot_qfq", "snapshot": str(snapshot),
                "sha256": hashlib.sha256((data_dir / "bars.csv").read_bytes()).hexdigest()}
    write_json(data_dir / "manifest.json", manifest)
    return bars, manifest


def research(bars, config, manifest, output, events):
    if set(bars.symbol.unique()) != set(config["symbols"]):
        raise ValueError("ETF池与config不一致")
    latest_per_symbol = bars.groupby("symbol").timestamp.max()
    if latest_per_symbol.nunique() != 1:
        raise ValueError("ETF最新交易日不一致")
    sessions = sorted(bars.timestamp.unique())
    if len(sessions) < config["holdout_sessions"] + 125:
        raise ValueError("历史数据不足：至少需要125日暖机加252日样本外")
    holdout_start, full_start = sessions[-config["holdout_sessions"]], sessions[121]
    strategies = config["strategies"] + ["ensemble", "ensemble_sentiment", "buy_hold"]
    full, holdout, stress, accounts = {}, {}, {}, {}
    costly = stress_config(config)
    for strategy in strategies:
        account = run_backtest(bars, strategy, config, holdout_start, events)
        accounts[strategy] = account
        holdout[strategy] = metrics(account, config)
        full[strategy] = metrics(run_backtest(bars, strategy, config, full_start, events), config)
        stress[strategy] = metrics(run_backtest(bars, strategy, costly, holdout_start, events), costly)
    warnings = [
        "12%是回撤预算；6%减半目标仓位，10%触发停止交易及次日退出计划。跳空、跌停及T+1可能导致超限。",
        "日线模拟在次日开盘价加滑点估算成交，不等同于券商模拟盘；本系统没有实盘下单接口。",
        "分红及拆并份尚未逐项核验：结果为价格收益扣交易费用，不是完整总回报，不能据此直接转实盘。",
        "固定ETF池可能有幸存者偏差；当前快照复权价并非完整时点数据库。历史表现不代表未来收益。",
        "手续费万分之一、最低5元、单边滑点5bp均为建模假设，需按券商实际费率校准。",
        "舆情历史结果仅作探索；真实增益以接下来一个月带时间戳的前向影子对照为准。",
        "持续做多基准也采用相同仓位/波动控制和回撤停止规则，不是满仓市场指数。"
    ]
    if not events:
        warnings.append("当前无有效舆情证据，舆情影子策略与纯量价组合相同；未宣称已有舆情增强收益。")
    counts = bars.groupby("symbol").timestamp.nunique()
    missing = {s: len(sessions) - int(counts.get(s, 0)) for s in config["symbols"] if int(counts.get(s, 0)) < len(sessions)}
    if missing:
        warnings.append("数据源存在缺失日线（" + ', '.join(f"{s}缺{n}日" for s, n in missing.items())
                        + "）；缺失日不撮合，已有持仓沿用上次估值，需进一步交叉核验。")
    if all(row["total_return"] <= 0 for row in holdout.values()):
        warnings.insert(0, "本轮历史测试全部策略净收益为负，未发现已验证的盈利候选；继续模拟不表示具备投资优势。")
    if manifest["provider"] == "synthetic_demo":
        warnings.insert(0, "本页全部为合成测试数据，不能解释为真实历史投资收益。")
    summary = {"generated_at": str(now()), "provider": manifest["provider"],
               "latest_session": str(bars.timestamp.max()), "holdout_start": str(holdout_start),
               "full_start": str(full_start), "data_sha256": manifest["sha256"],
               "config": config, "warnings": warnings, "holdout": holdout, "full": full, "stress": stress}
    output.mkdir(parents=True, exist_ok=True)
    export_accounts(output / "holdout", accounts)
    write_json(output / "summary.json", summary)
    return summary, accounts


def main():
    parser = argparse.ArgumentParser(description="ETF daily research and forward paper lab")
    parser.add_argument("command", choices=["fetch", "research", "paper", "cycle", "status", "demo"])
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args()
    config = validate_config(read_json(args.config))
    data_dir, output, state_dir = ROOT / "data" / "market", ROOT / "reports", ROOT / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    if args.command == "status":
        for path in (state_dir / "status.json", state_dir / "paper.json"):
            if path.exists():
                value = read_json(path)
                if path.name == "paper.json":
                    value = {k: value[k] for k in ("status", "started_at", "expires_at", "last_session", "metrics")}
                print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    lock = state_dir / ".cycle.lock"
    descriptor = None
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(descriptor, str(os.getpid()).encode())
        if args.command == "demo":
            bars = demo_bars()
            demo_dir = ROOT / "data" / "demo"
            save_bars(bars, demo_dir / "bars.csv")
            manifest = {"provider": "synthetic_demo", "sha256": hashlib.sha256((demo_dir / "bars.csv").read_bytes()).hexdigest()}
            output = ROOT / "reports" / "demo"
        elif args.command in ("fetch", "cycle"):
            bars, manifest = fetch(config, data_dir)
        else:
            bars = load_bars(data_dir / "bars.csv")
            manifest = read_json(data_dir / "manifest.json")
            actual_hash = hashlib.sha256((data_dir / "bars.csv").read_bytes()).hexdigest()
            if actual_hash != manifest["sha256"]:
                raise ValueError("行情文件与manifest指纹不符")
        if args.command == "fetch":
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 0
        event_path = ROOT / "data" / "news" / "events.jsonl"
        events = load_events(event_path) if event_path.exists() else []
        paper = None
        if args.command in ("paper", "cycle"):
            if manifest["provider"] not in ("eastmoney_public", "tencent_public"):
                raise ValueError("前向模拟不接受合成数据")
            current = read_json(state_dir / "paper.json") if (state_dir / "paper.json").exists() else None
            paper = update_paper(bars, config, experiment_hash(ROOT, config), now(), events, current)
            write_json(state_dir / "paper.json", paper)
            write_json(output / "signals.json", paper["signals"])
            export_accounts(output / "paper", paper["accounts"])
        if args.command == "paper" and (output / "summary.json").exists():
            summary = read_json(output / "summary.json")
            # Loading a fresh report here avoids silently inventing historical curves.
            render_report(output, summary, {}, paper)
        else:
            summary, accounts = research(bars, config, manifest, output, events)
            if paper is None and args.command != "demo" and (state_dir / "paper.json").exists():
                paper = read_json(state_dir / "paper.json")
            render_report(output, summary, accounts, paper)
        status = {"status": "ok", "at": str(now()), "latest_session": str(bars.timestamp.max()),
                  "report": str(output / "dashboard.html"), "provider": manifest["provider"],
                  "paper_status": paper["status"] if paper else "not_started"}
        write_json(state_dir / "status.json", status)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    except FileExistsError:
        print("已有任务运行或遗留锁；请核实state/.cycle.lock对应进程后处理。", file=sys.stderr)
        return 2
    except Exception as exc:
        error = {"status": "error", "at": str(now()), "error": str(exc)}
        write_json(state_dir / "status.json", error)
        print(json.dumps(error, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        if descriptor is not None:
            os.close(descriptor)
            lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
