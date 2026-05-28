"""HK universe definitions for the paper-trading competition.

The HK competition uses two parallel accounts:

  - HSI (Hang Seng Index, 50 blue chips)
  - HSCEI (Hang Seng China Enterprises Index, 50 China-related)

These overlap significantly (Tencent, Alibaba, etc. appear in both),
which is intentional — it mirrors A-share's HS300 + ZZ500 pattern where
the two accounts produce 50 holdings each with natural overlap.

Tickers use the yfinance suffix convention: ``0005.HK`` for HSBC, etc.
The HSI / HSCEI member lists are maintained as static lists below; a
future enhancement could refresh them from yfinance's index info, but
for v1 a hand-curated snapshot is more reliable than a runtime fetch.
"""

from __future__ import annotations

# Hang Seng Index (HSI) — top 50 HK blue chips, hand-snapshotted 2026-05.
# Tickers in yfinance format (.HK suffix). Order is by market cap descending
# at the time of the snapshot; subsequent rebalances are routine.
HSI_TICKERS: list[str] = [
    "0700.HK",  # Tencent
    "9988.HK",  # Alibaba
    "0005.HK",  # HSBC
    "0939.HK",  # CCB
    "1299.HK",  # AIA
    "1398.HK",  # ICBC
    "3690.HK",  # Meituan
    "0941.HK",  # China Mobile
    "0388.HK",  # HKEX
    "2318.HK",  # Ping An
    "0883.HK",  # CNOOC
    "1810.HK",  # Xiaomi
    "0027.HK",  # Galaxy Entertainment
    "0001.HK",  # CK Hutchison
    "1113.HK",  # CK Asset
    "0016.HK",  # SHK Properties
    "0011.HK",  # Hang Seng Bank
    "0002.HK",  # CLP Holdings
    "0006.HK",  # Power Assets
    "0003.HK",  # HK & China Gas
    "0066.HK",  # MTR
    "0017.HK",  # New World Development
    "0288.HK",  # WH Group
    "0291.HK",  # China Resources Beer
    "0386.HK",  # Sinopec
    "0688.HK",  # China Overseas Land
    "0762.HK",  # China Unicom
    "0823.HK",  # Link REIT
    "0857.HK",  # PetroChina
    "0867.HK",  # CSPC Pharma
    "0960.HK",  # Longfor
    "0992.HK",  # Lenovo
    "1038.HK",  # CKI Holdings
    "1044.HK",  # Hengan
    "1093.HK",  # CSPC Innovation
    "1109.HK",  # China Resources Land
    "1177.HK",  # Sino Biopharm
    "1209.HK",  # China Resources Mixc
    "1211.HK",  # BYD
    "1378.HK",  # China Hongqiao
    "1928.HK",  # Sands China
    "1929.HK",  # Chow Tai Fook
    "2007.HK",  # Country Garden
    "2020.HK",  # Anta Sports
    "2269.HK",  # WuXi Biologics
    "2313.HK",  # Shenzhou Intl
    "2331.HK",  # Li Ning
    "2382.HK",  # Sunny Optical
    "2628.HK",  # China Life
    "2688.HK",  # ENN Energy
]

# Hang Seng China Enterprises Index (HSCEI) — 50 H-shares of mainland
# Chinese companies listed in HK. Significant overlap with HSI for
# China-domiciled mega caps.
HSCEI_TICKERS: list[str] = [
    "0700.HK",  # Tencent (also in HSI)
    "9988.HK",  # Alibaba (also in HSI)
    "0939.HK",  # CCB
    "1398.HK",  # ICBC
    "3690.HK",  # Meituan
    "0941.HK",  # China Mobile
    "2318.HK",  # Ping An
    "0883.HK",  # CNOOC
    "1810.HK",  # Xiaomi
    "1211.HK",  # BYD
    "0386.HK",  # Sinopec
    "0857.HK",  # PetroChina
    "2628.HK",  # China Life
    "1288.HK",  # ABC
    "3328.HK",  # BoCom
    "3968.HK",  # CMB
    "0998.HK",  # CITIC Bank
    "1658.HK",  # PSBC
    "2388.HK",  # BOC HK
    "0762.HK",  # China Unicom
    "0728.HK",  # China Telecom
    "0688.HK",  # China Overseas Land
    "1109.HK",  # China Resources Land
    "0960.HK",  # Longfor
    "2007.HK",  # Country Garden
    "1918.HK",  # Sunac
    "0291.HK",  # China Resources Beer
    "2319.HK",  # Mengniu
    "0322.HK",  # Tingyi
    "0151.HK",  # Want Want
    "2020.HK",  # Anta Sports
    "2331.HK",  # Li Ning
    "1929.HK",  # Chow Tai Fook
    "2313.HK",  # Shenzhou Intl
    "0992.HK",  # Lenovo
    "2382.HK",  # Sunny Optical
    "1378.HK",  # China Hongqiao
    "0489.HK",  # Dongfeng Motor
    "2333.HK",  # Great Wall Motor
    "2238.HK",  # Guangzhou Auto
    "0175.HK",  # Geely Auto
    "1093.HK",  # CSPC Innovation
    "1177.HK",  # Sino Biopharm
    "0867.HK",  # CSPC Pharma
    "2269.HK",  # WuXi Biologics
    "1801.HK",  # Innovent
    "6160.HK",  # BeiGene
    "9618.HK",  # JD
    "9999.HK",  # NetEase
    "1024.HK",  # Kuaishou
]


def resolve_universe(scope: str) -> list[str]:
    """Return the ticker list for an HK account scope.

    Valid scopes: ``hsi``, ``hscei``. Other values raise ``ValueError``.
    """
    scope = scope.lower()
    if scope == "hsi":
        return list(HSI_TICKERS)
    if scope == "hscei":
        return list(HSCEI_TICKERS)
    raise ValueError(
        f"unknown HK scope: {scope!r}; expected one of ['hsi', 'hscei']"
    )
