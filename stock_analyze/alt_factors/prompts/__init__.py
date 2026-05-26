"""Prompt templates for operator-driven LLM sentiment analysis.

Operators copy these markdown files into their LLM client (Claude.ai for
``claude``, ChatGPT for ``codex``), substitute the ``{agent_id}`` /
``{week_start_date}`` / ``{week_end_date}`` placeholders, and paste the
LLM's JSON response into ``record-sentiment``.

Versioning: bumps when prompt wording changes. Pass via
``--prompt-version`` to ``record-sentiment`` so downstream analytics can
attribute scores to the right prompt version.
"""
