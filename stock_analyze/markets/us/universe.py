"""US universe definitions.

Two parallel accounts:
  - SP500 (S&P 500 top 50 by market cap)
  - NDX100 (NASDAQ-100 top 50)

Tickers use the bare yfinance convention (no exchange suffix). The
member lists are hand-snapshotted; subsequent rebalances are routine
and don't require code changes.
"""

from __future__ import annotations


# S&P 500 top 50 by market cap, snapshotted 2026-05.
# Note: heavy mega-cap concentration (the top 10 names dominate index
# weight). Random subset further down the list adds diversification.
SP500_TICKERS: list[str] = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "NVDA",   # Nvidia
    "GOOGL",  # Alphabet
    "AMZN",   # Amazon
    "META",   # Meta Platforms
    "TSLA",   # Tesla
    "BRK-B",  # Berkshire Hathaway B
    "AVGO",   # Broadcom
    "LLY",    # Eli Lilly
    "JPM",    # JPMorgan
    "WMT",    # Walmart
    "V",      # Visa
    "XOM",    # Exxon
    "UNH",    # UnitedHealth
    "MA",     # Mastercard
    "PG",     # P&G
    "HD",     # Home Depot
    "JNJ",    # Johnson & Johnson
    "COST",   # Costco
    "ORCL",   # Oracle
    "ABBV",   # AbbVie
    "BAC",    # Bank of America
    "NFLX",   # Netflix
    "KO",     # Coca-Cola
    "CRM",    # Salesforce
    "MRK",    # Merck
    "CVX",    # Chevron
    "TMO",    # Thermo Fisher
    "AMD",    # AMD
    "ADBE",   # Adobe
    "PEP",    # PepsiCo
    "LIN",    # Linde
    "ACN",    # Accenture
    "WFC",    # Wells Fargo
    "DIS",    # Disney
    "MCD",    # McDonald's
    "CSCO",   # Cisco
    "ABT",    # Abbott
    "PM",     # Philip Morris
    "INTU",   # Intuit
    "IBM",    # IBM
    "GE",     # GE
    "TXN",    # Texas Instruments
    "VZ",     # Verizon
    "QCOM",   # Qualcomm
    "ISRG",   # Intuitive Surgical
    "AXP",    # American Express
    "DHR",    # Danaher
    "BX",     # Blackstone
]

# NASDAQ-100 top 50 — significant overlap with S&P 500 mega-caps.
NDX100_TICKERS: list[str] = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "NVDA",   # Nvidia
    "GOOGL",  # Alphabet
    "AMZN",   # Amazon
    "META",   # Meta
    "TSLA",   # Tesla
    "AVGO",   # Broadcom
    "COST",   # Costco
    "ORCL",   # Oracle
    "NFLX",   # Netflix
    "AMD",    # AMD
    "ADBE",   # Adobe
    "CSCO",   # Cisco
    "QCOM",   # Qualcomm
    "INTU",   # Intuit
    "TXN",    # Texas Instruments
    "AMGN",   # Amgen
    "ISRG",   # Intuitive Surgical
    "BKNG",   # Booking Holdings
    "GILD",   # Gilead
    "VRTX",   # Vertex
    "MU",     # Micron
    "ADP",    # ADP
    "REGN",   # Regeneron
    "PANW",   # Palo Alto Networks
    "LRCX",   # Lam Research
    "KLAC",   # KLA Corp
    "SBUX",   # Starbucks
    "PYPL",   # PayPal
    "MDLZ",   # Mondelez
    "INTC",   # Intel
    "CDNS",   # Cadence Design
    "SNPS",   # Synopsys
    "ABNB",   # Airbnb
    "MAR",    # Marriott
    "FTNT",   # Fortinet
    "AMAT",   # Applied Materials
    "MELI",   # MercadoLibre
    "ASML",   # ASML
    "CRWD",   # CrowdStrike
    "CTAS",   # Cintas
    "ADI",    # Analog Devices
    "MRVL",   # Marvell
    "NXPI",   # NXP
    "WDAY",   # Workday
    "ROST",   # Ross Stores
    "MNST",   # Monster Beverage
    "ORLY",   # O'Reilly
    "ON",     # ON Semiconductor
]


def resolve_universe(scope: str) -> list[str]:
    """Return the ticker list for a US account scope.

    Valid scopes: ``sp500``, ``ndx100``.
    """
    scope = scope.lower()
    if scope == "sp500":
        return list(SP500_TICKERS)
    if scope == "ndx100":
        return list(NDX100_TICKERS)
    raise ValueError(
        f"unknown US scope: {scope!r}; expected one of ['sp500', 'ndx100']"
    )
