"""Preregistered A-share permanent portfolio research."""

from .contract import (
    STUDY_ID,
    AssetSpec,
    PermanentPortfolioContract,
    canonical_hash,
    load_contract,
)

__all__ = [
    "STUDY_ID",
    "AssetSpec",
    "PermanentPortfolioContract",
    "canonical_hash",
    "load_contract",
]
