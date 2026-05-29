"""Alt-factor support: agent-specific factors derived outside the classic
quantitative pipeline (e.g. operator-recorded LLM market sentiment).

See ``openspec/changes/add-llm-sentiment-alpha-factor/design.md`` for the
full MVP design rationale (Path 2 — broadcast factor, manual operator
recording, live-only, no historical backfill).

The module also provides a sector-level **per-stock** sentiment factor
(``record_sector_sentiment`` / ``load_latest_sector_sentiment``, factor name
``<agent>_sector_sentiment``) that flows through the normal factor pipeline
and participates in ranking; the broadcast scalar is retained as the
back-compat MVP.
"""
