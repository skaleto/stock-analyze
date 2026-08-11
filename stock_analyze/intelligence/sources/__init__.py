"""Market-intelligence source adapters."""

from .base import FetchBatch, SourceAdapter
from .official import OfficialHtmlAdapter, TushareAnnouncementAdapter

__all__ = ["FetchBatch", "OfficialHtmlAdapter", "SourceAdapter", "TushareAnnouncementAdapter"]
