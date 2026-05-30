#!/usr/bin/env python3
"""verify_data_sources.py — 验证「这台机器」能否拉到港股/美股数据。

在你打算用来"每周本地拉数"的那台机器上跑（尤其是国内家宽那台）：

    python3 -m pip install --quiet yfinance akshare
    python3 scripts/verify_data_sources.py

它会分别测：
  • yfinance（Yahoo，境外站）：港股 .HK / 美股的基本面(PE/PB/股息) + 日线历史
  • akshare（东方财富/新浪，国内站）：港股日线 / 港股&美股整市场快照
对每个源报告：能否【连通】、数据是否【新鲜】（最近日期）、覆盖样本；
最后给一个【结论】，告诉你这台机器该用哪个源。

把整段输出发回给我即可。脚本是自包含的——不依赖本项目其它代码，
只需要 yfinance + akshare 两个包。无网络副作用，纯读取。
"""
from __future__ import annotations

import datetime as _dt
import time

OK = "[OK]"
NO = "[FAIL]"
WARN = "[WARN]"


def _section(title: str) -> None:
    print("\n" + "=" * 64 + f"\n{title}\n" + "=" * 64)


def _is_rate_limited(exc: Exception) -> bool:
    m = str(exc).lower()
    return "too many requests" in m or "rate limit" in m or "429" in m


def _is_unreachable(exc: Exception) -> bool:
    m = str(exc).lower()
    return ("max retries" in m or "connection" in m or "timed out" in m
            or "failed to establish" in m or "name resolution" in m)


# --------------------------------------------------------------------------
# yfinance (Yahoo — 境外)
# --------------------------------------------------------------------------
def test_yfinance() -> dict | None:
    _section("yfinance (Yahoo Finance, 境外站)  —  港股 .HK / 美股")
    try:
        import yfinance as yf
    except ImportError:
        print(f"{NO} yfinance 未安装 →  python3 -m pip install yfinance")
        return None
    print("yfinance version:", getattr(yf, "__version__", "?"))

    hk = ["0700.HK", "0005.HK", "0941.HK"]   # 腾讯 / 汇丰 / 中移动
    us = ["AAPL", "MSFT", "NVDA"]
    res = {"hk_fund": 0, "us_fund": 0, "hist_ok": False,
           "latest": None, "rate_limited": False, "other_err": False}

    print("\n-- 基本面 (.info: trailingPE / priceToBook / dividendYield) --")
    for tk in hk + us:
        try:
            i = yf.Ticker(tk).info
            pe, pb, dv = i.get("trailingPE"), i.get("priceToBook"), i.get("dividendYield")
            has = sum(x is not None for x in (pe, pb, dv))
            if tk.endswith(".HK"):
                res["hk_fund"] += (has >= 2)
            else:
                res["us_fund"] += (has >= 2)
            print(f"  {tk:9} PE={pe} PB={pb} 股息={dv}   ({has}/3)")
        except Exception as e:
            if _is_rate_limited(e):
                res["rate_limited"] = True
                print(f"  {tk:9} {NO} 限流 429 Too Many Requests")
            else:
                res["other_err"] = True
                print(f"  {tk:9} {NO} {str(e)[:70]}")
        time.sleep(1.5)   # 礼貌间隔，别把 Yahoo 惹毛

    print("\n-- 日线历史 (新鲜度) --")
    try:
        h = yf.Ticker("0700.HK").history(period="1mo")
        if len(h):
            res["hist_ok"] = True
            res["latest"] = str(h.index[-1].date())
        print(f"  0700.HK history: {len(h)} 根日线, 最新 {res['latest']}")
    except Exception as e:
        (res.__setitem__("rate_limited", True) if _is_rate_limited(e)
         else res.__setitem__("other_err", True))
        print(f"  0700.HK history: {NO} {str(e)[:70]}")
    return res


# --------------------------------------------------------------------------
# akshare (东方财富/新浪 — 国内)
# --------------------------------------------------------------------------
def test_akshare() -> dict | None:
    _section("akshare (东方财富/新浪, 国内站)  —  港股 / 美股")
    try:
        import akshare as ak
    except ImportError:
        print(f"{NO} akshare 未安装 →  python3 -m pip install akshare")
        return None
    print("akshare version:", getattr(ak, "__version__", "?"))

    res = {"hk_hist": False, "hk_spot": False, "us_spot": False,
           "latest": None, "unreachable": False, "other_err": False}
    today = _dt.date.today()
    start = (today - _dt.timedelta(days=40)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    print("\n-- 港股日线 (stock_hk_hist 00700) --")
    try:
        df = ak.stock_hk_hist(symbol="00700", period="daily",
                              start_date=start, end_date=end, adjust="qfq")
        if len(df):
            res["hk_hist"] = True
            res["latest"] = str(df.iloc[-1, 0])
        print(f"  {OK if len(df) else NO} {len(df)} 行, 最新 {res['latest']}")
    except Exception as e:
        if _is_unreachable(e):
            res["unreachable"] = True
        else:
            res["other_err"] = True
        print(f"  {NO} {str(e)[:90]}")

    print("\n-- 港股整市场快照 (stock_hk_spot_em, 一次拉, 含 PE/最新价) --")
    try:
        s = ak.stock_hk_spot_em()
        res["hk_spot"] = bool(len(s))
        print(f"  {OK if len(s) else NO} shape={getattr(s, 'shape', None)}")
    except Exception as e:
        if _is_unreachable(e):
            res["unreachable"] = True
        print(f"  {NO} {str(e)[:90]}")

    print("\n-- 美股整市场快照 (stock_us_spot_em) --")
    try:
        u = ak.stock_us_spot_em()
        res["us_spot"] = bool(len(u))
        print(f"  {OK if len(u) else NO} shape={getattr(u, 'shape', None)}")
    except Exception as e:
        if _is_unreachable(e):
            res["unreachable"] = True
        print(f"  {NO} {str(e)[:90]}")
    return res


# --------------------------------------------------------------------------
# verdict
# --------------------------------------------------------------------------
def verdict(y: dict | None, a: dict | None) -> None:
    _section("结论 / VERDICT")

    if y is None:
        yf_line = "未测（yfinance 未安装）"
        yf_ok = False
    elif y["rate_limited"]:
        yf_line = f"{NO} 被 Yahoo 限流(429) —— 这台 IP 被限（机房/云 IP 常见；住宅 IP 一般没事）"
        yf_ok = False
    elif y["hk_fund"] >= 2 and y["hist_ok"]:
        yf_line = f"{OK} 可用（港股基本面 {y['hk_fund']}/3，历史最新 {y['latest']}）"
        yf_ok = True
    else:
        yf_line = f"{WARN} 部分可用/异常（港股基本面 {y['hk_fund']}/3, 历史 {y['hist_ok']}）"
        yf_ok = False
    print("yfinance:", yf_line)

    if a is None:
        ak_line = "未测（akshare 未安装）"
        ak_ok = False
    elif a["unreachable"] and not (a["hk_hist"] or a["hk_spot"]):
        ak_line = (f"{NO} 连不上东方财富行情服务器(push2.eastmoney.com) "
                   "—— 这台多半不是国内家宽（境外/VPN/云）")
        ak_ok = False
    elif a["hk_hist"] or a["hk_spot"]:
        ak_line = f"{OK} 可用（港股历史/快照能拉，最新 {a.get('latest')}）"
        ak_ok = True
    else:
        ak_line = f"{WARN} 异常"
        ak_ok = False
    print("akshare: ", ak_line)

    print("\n→ 这台机器适合用哪个源:")
    if yf_ok and not ak_ok:
        print(f"   {OK} 用 yfinance（能连 Yahoo；akshare 的东财行情连不上）")
    elif ak_ok and not yf_ok:
        print(f"   {OK} 用 akshare（能连东方财富；yfinance 被限流/不通）")
    elif yf_ok and ak_ok:
        print(f"   {OK} 两个都能用 → 优先 yfinance（港美一体，代码已就绪）")
    else:
        print(f"   {NO} 两个都不通 —— 换台机器/网络再测")

    print("\n把上面【整段输出】发回来，我据此定数据源 + 写对应 provider。")


if __name__ == "__main__":
    print("HK/US 数据源连通性验证  ·",
          _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("（纯读取、无副作用；港股 .HK 用 yfinance，国内站用 akshare）")
    y_res = test_yfinance()
    a_res = test_akshare()
    verdict(y_res, a_res)
