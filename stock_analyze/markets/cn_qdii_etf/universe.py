"""Static universes for domestic cross-border ETF exposure."""

from __future__ import annotations


US_EXPOSURE = [
    "513100.SH",  # Nasdaq 100
    "159941.SZ",  # Nasdaq 100
    "513500.SH",  # S&P 500
    "159655.SZ",  # S&P 500
    "513300.SH",  # Nasdaq 100
    "159632.SZ",  # Nasdaq 100
    "513850.SH",  # US internet / China ADR exposure
]

HK_EXPOSURE = [
    "513130.SH",  # Hang Seng Tech
    "159920.SZ",  # Hang Seng
    "513180.SH",  # Hang Seng Tech
    "513330.SH",  # Hang Seng internet
    "513060.SH",  # Hang Seng healthcare
    "159726.SZ",  # Hang Seng tech
    "513690.SH",  # Hong Kong high dividend
]

UNIVERSES = {
    "us_exposure": US_EXPOSURE,
    "hk_exposure": HK_EXPOSURE,
}

ETF_METADATA = {
    "513100.SH": {"exposure_group": "美国市场", "theme": "纳斯达克100"},
    "159941.SZ": {"exposure_group": "美国市场", "theme": "纳斯达克100"},
    "513500.SH": {"exposure_group": "美国市场", "theme": "标普500"},
    "159655.SZ": {"exposure_group": "美国市场", "theme": "标普500"},
    "513300.SH": {"exposure_group": "美国市场", "theme": "纳斯达克100"},
    "159632.SZ": {"exposure_group": "美国市场", "theme": "纳斯达克100"},
    "513850.SH": {"exposure_group": "美国市场", "theme": "美国大盘"},
    "513130.SH": {"exposure_group": "香港市场", "theme": "恒生科技"},
    "159920.SZ": {"exposure_group": "香港市场", "theme": "恒生综合"},
    "513180.SH": {"exposure_group": "香港市场", "theme": "恒生科技"},
    "513330.SH": {"exposure_group": "香港市场", "theme": "恒生互联网"},
    "513060.SH": {"exposure_group": "香港市场", "theme": "恒生医疗"},
    "159726.SZ": {"exposure_group": "香港市场", "theme": "港股红利"},
    "513690.SH": {"exposure_group": "香港市场", "theme": "港股红利"},
}


def resolve_universe(scope: str) -> list[str]:
    """Return ETF ts_codes for a configured account scope."""
    try:
        return list(UNIVERSES[scope])
    except KeyError as exc:
        raise ValueError(f"unknown cn_qdii_etf universe scope: {scope}") from exc


def classify_scope(code: str) -> str:
    """Return the first configured scope containing ``code``."""
    for scope, codes in UNIVERSES.items():
        if code in codes:
            return scope
    return "cn_qdii_etf"


def metadata_for_code(code: str) -> dict[str, str]:
    """Return stable display metadata for a configured cross-border ETF."""
    return dict(
        ETF_METADATA.get(
            str(code).upper(),
            {"exposure_group": "全球市场", "theme": "跨境ETF"},
        )
    )
