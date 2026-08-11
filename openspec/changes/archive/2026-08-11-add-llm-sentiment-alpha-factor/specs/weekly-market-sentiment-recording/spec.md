## ADDED Requirements

### Requirement: Operator records weekly market-sentiment via CLI

`stock_analyze.alt_factors.sentiment.record_market_sentiment(agent_id, week_end, score, confidence, drivers, sources, llm_model, prompt_version, repo_root, force=False) -> None` SHALL persist one durable row per (agent, week_end) of operator-supplied market-sentiment data harvested from that agent's LLM client (Claude.ai for claude, ChatGPT for codex).

The function SHALL:

- Append to `data/<agent_id>/alt_factors/market_sentiment.csv` with the schema
  `week_end_date,sentiment_score,confidence,key_drivers,sources,llm_model,prompt_version,recorded_at`.
- Raise `ValueError` when `score` is outside `[-1.0, 1.0]`, when `confidence` is outside `[0.0, 1.0]`, or when `drivers` is empty / longer than 5 entries.
- Raise `DuplicateSentimentEntry` when a row for `(agent_id, week_end)` already exists, unless `force=True` (which replaces the existing row).
- Use atomic write (write to `.tmp`, then rename) so a concurrent crash never leaves a corrupted CSV.

A CLI subcommand `python3 -m stock_analyze record-sentiment --agent <claude|codex> --week-end YYYY-MM-DD --score F --confidence F --drivers C,S,V --llm-model NAME [--sources URL1|URL2] [--prompt-version v1] [--force]` SHALL wrap this function, returning exit code 0 on success and 1 on validation / duplicate errors.

#### Scenario: Happy path appends one row

- **GIVEN** an empty `data/claude/alt_factors/market_sentiment.csv`
- **WHEN** `record_market_sentiment(agent_id="claude", week_end=date(2026, 5, 22), score=0.32, confidence=0.78, drivers=["AI 算力链回暖","央行 MLF 偏鸽","地产新政预期反复"], sources=["https://www.cls.cn/x"], llm_model="claude-sonnet-4.5", prompt_version="v1", repo_root=tmp)` is called
- **THEN** the CSV contains a header line plus exactly one data row
- **AND** the row's `week_end_date` is `2026-05-22`
- **AND** the row's `sentiment_score` is `0.3200`
- **AND** the row's `llm_model` is `claude-sonnet-4.5`

#### Scenario: Score outside [-1, 1] is rejected

- **WHEN** `record_market_sentiment(..., score=1.5, ...)` is called
- **THEN** the function raises `ValueError` mentioning `score`
- **AND** no file is written

#### Scenario: Duplicate week_end without --force is rejected

- **GIVEN** a CSV already contains a row for `(claude, 2026-05-22)`
- **WHEN** `record_market_sentiment(..., week_end=date(2026, 5, 22), force=False)` is called again
- **THEN** the function raises `DuplicateSentimentEntry`
- **AND** the existing row is unchanged

#### Scenario: --force overwrites an existing row

- **GIVEN** a CSV already contains `(claude, 2026-05-22, score=0.32)`
- **WHEN** `record_market_sentiment(..., week_end=date(2026, 5, 22), score=0.40, force=True)` is called
- **THEN** the CSV has exactly one row for `2026-05-22`
- **AND** that row's `sentiment_score` is `0.4000`

#### Scenario: CLI exit codes

- **WHEN** the operator runs `python3 -m stock_analyze record-sentiment --agent claude --week-end 2026-05-22 --score 0.32 --confidence 0.78 --drivers "x,y,z" --llm-model claude-sonnet-4.5`
- **THEN** the process exits with code 0
- **AND** the CSV contains a new row

- **WHEN** the operator immediately re-runs the same command without `--force`
- **THEN** the process exits with code 1
- **AND** stderr contains a `DuplicateSentimentEntry`-style message

### Requirement: Sentiment history retrieval respects point-in-time

`stock_analyze.alt_factors.sentiment.load_latest_market_sentiment(agent_id, as_of, repo_root) -> float | None` SHALL return the `sentiment_score` of the most recent row whose `week_end_date <= as_of`, or `None` when no row qualifies.

#### Scenario: Most recent row before as_of is returned

- **GIVEN** rows for week_end ∈ `{2026-05-08 (0.1), 2026-05-15 (0.2), 2026-05-22 (0.3)}`
- **WHEN** `load_latest_market_sentiment("claude", as_of=date(2026, 5, 20), repo_root=tmp)` is called
- **THEN** the returned value is `0.2` (from the 2026-05-15 row; 2026-05-22 is future)

#### Scenario: No data yet returns None

- **GIVEN** `data/claude/alt_factors/market_sentiment.csv` does not exist
- **WHEN** `load_latest_market_sentiment("claude", as_of=date(2026, 5, 22), repo_root=tmp)` is called
- **THEN** the returned value is `None`
