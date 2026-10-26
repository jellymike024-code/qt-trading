from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd


LABELS = {"ma_trend": "均线趋势", "donchian": "唐奇安突破", "ts_momentum": "时序动量",
          "volume_momentum": "量价动量", "ensemble": "等权策略组合", "ensemble_sentiment": "组合＋舆情影子", "buy_hold": "同风控持续持有基准"}


def _table(rows, fields):
    result = '<table><thead><tr>' + ''.join(f'<th>{label}</th>' for _, label, _ in fields) + '</tr></thead><tbody>'
    for name, row in rows.items():
        values = {"strategy": LABELS.get(name, name)} | row
        result += '<tr>'
        for key, _, style in fields:
            value = values.get(key)
            text = '—' if value is None else format(value, style) if style else str(value)
            result += '<td>' + html.escape(text) + '</td>'
        result += '</tr>'
    return result + '</tbody></table>'


def render_report(root, summary, accounts, paper=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fields = [("strategy", "策略", ""), ("total_return", "净收益", ".2%"),
              ("max_drawdown", "最大回撤", ".2%"), ("sharpe", "夏普（日频估算）", ".2f"),
              ("mean_exposure", "平均仓位", ".1%"), ("fees", "费用/元", ",.2f"), ("fills", "成交笔数", "d")]
    title = "合成数据演示 · 不代表市场收益" if summary["provider"] == "synthetic_demo" else "ETF量化研究 · 公开日线数据"
    chart = _chart(accounts)
    social_link = '<p><a href="social_dashboard.html">免费舆情采集与影子分析</a></p>' if (root / "social_dashboard.html").exists() else ''
    warnings = ''.join('<li>' + html.escape(x) + '</li>' for x in summary["warnings"])
    paper_html = '<p>尚未启动前向模拟盘。</p>'
    if paper:
        paper_html = '<p>状态：' + html.escape(paper["status"]) + '；开始：' + html.escape(paper["started_at"]) + '；到期：' + html.escape(paper["expires_at"]) + '</p>'
        paper_html += _table(paper["metrics"], fields)
        paper_html += '<p>以下为组合账户下一个交易日的目标仓位；实际可成交数量受现金、整手与撮合约束影响。</p>'
        for strategy in ("ensemble", "ensemble_sentiment"):
            targets = paper["signals"].get(strategy, {}).get("targets", {})
            paper_html += '<h3>' + LABELS[strategy] + '</h3><ul>'
            for symbol, value in targets.items():
                news_status = value.get("news", {}).get("status", "disabled")
                paper_html += f'<li>{symbol}：{value["weight"]:.1%}；舆情状态 {html.escape(news_status)}</li>'
            paper_html += '</ul>'
    content = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ETF量化实验室</title>
<style>body{{background:#101622;color:#e8edf5;font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif;max-width:1220px;margin:36px auto;padding:0 22px}}h1{{font-size:30px}}h2{{margin-top:34px}}.muted{{color:#a8b7ca}}.notice{{background:#293243;border-left:4px solid #ebba6a;padding:16px}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{padding:11px;text-align:right;border-bottom:1px solid #354053}}td:first-child,th:first-child{{text-align:left}}svg{{width:100%;height:auto;background:#182131;border-radius:10px}}.scroll{{overflow-x:auto}}a{{color:#7cc7ff}}</style>
<h1>{title}</h1><p class="muted">资金100万元 · 国内股票类ETF · 日线信号 · 本地研究与指示性模拟</p>{social_link}
<div class="notice"><ul>{warnings}</ul></div>
<h2>历史样本外对比</h2><p class="muted">{html.escape(summary['holdout_start'])} 至 {html.escape(summary['latest_session'])}；参数预先固定，每个账户独立100万元。组合账户本身总资金也是100万元。</p>
{chart}<div class="scroll">{_table(summary['holdout'], fields)}</div>
<h2>双倍成本压力测试</h2><div class="scroll">{_table(summary['stress'], fields)}</div>
<h2>扩展历史区间</h2><p class="muted">该区间含样本外部分，不是独立验证。策略在统一120日暖机后起算。</p><div class="scroll">{_table(summary['full'], fields)}</div>
<h2>一个月前向模拟</h2>{paper_html}
<h2>结果文件与口径</h2><p>净值、交易、拒单、相关性和数据快照保存在本目录及data/market。收盘信号次日开盘加滑点估算成交；不是券商模拟成交回报。分红及拆并份事件尚未核验，当前收益为价格收益扣交易费用，不是完整总回报。</p>
<p class="muted">生成于 {html.escape(summary['generated_at'])}；数据指纹 {html.escape(summary['data_sha256'][:16])}</p></html>'''
    (root / "dashboard.html").write_text(content, encoding="utf-8")


def _chart(accounts):
    colors = ['#68b7ff', '#efb66e', '#79d2b0', '#c3a0fb', '#f27e95', '#f0e38b', '#aab6c8']
    curves = {s: [1000000.] + [x['equity'] for x in a['equity']] for s, a in accounts.items()}
    flat = [v for values in curves.values() for v in values]
    if not flat:
        return ''
    lo, hi = min(flat), max(flat)
    span = max(hi - lo, 1.)
    svg = '<svg viewBox="0 0 1100 390" role="img" aria-label="各策略净值比较">'
    for i in range(5):
        y = 32 + i * 65
        svg += f'<line x1="85" y1="{y}" x2="1070" y2="{y}" stroke="#354053"/><text x="8" y="{y+5}" fill="#a8b7ca" font-size="13">{(hi-i*span/4)/10000:.1f}万</text>'
    for (name, values), color in zip(curves.items(), colors):
        points = ' '.join(f'{85+i/max(len(values)-1,1)*985:.1f},{32+(hi-v)/span*260:.1f}' for i, v in enumerate(values))
        svg += f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>'
    for i, (name, color) in enumerate(zip(curves, colors)):
        x, y = 20 + (i % 4) * 270, 333 + (i // 4) * 29
        svg += f'<text x="{x}" y="{y}" fill="{color}" font-size="15">● {LABELS[name]}</text>'
    return svg + '</svg>'


def export_accounts(root, accounts):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    returns = {}
    for name, account in accounts.items():
        curve = pd.DataFrame(account["equity"])
        curve.to_csv(root / f"{name}_equity.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(account["trades"]).to_csv(root / f"{name}_trades.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(account["rejections"]).to_csv(root / f"{name}_rejections.csv", index=False, encoding="utf-8-sig")
        if not curve.empty:
            returns[name] = curve.set_index("timestamp").equity.pct_change(fill_method=None)
    if returns:
        pd.DataFrame(returns).corr().to_csv(root / "return_correlations.csv", encoding="utf-8-sig")
