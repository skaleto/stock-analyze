# migrate-data-source-to-tushare-pro

Replace the AKShare + push2 + Baostock mixed data layer with Tushare Pro as the **single primary source**, supplemented by Baostock as a strict cache-only fallback. Removes the home-backfill workflow (no longer needed). Eliminates push2 anti-scraping IP block risk on both ECS and home machines.

Status: **DRAFT · awaiting human confirmation** (proposed by claude agent · 2026-05-23 · prompted by user goal to abandon AKShare).
