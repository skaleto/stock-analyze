# enable-llm-direct-strategy-evolution

Let each LLM agent (claude / codex) directly modify its own `configs/agents/<agent>.yaml` during monthly strategy evolution, replacing the `proposal → referee → apply` three-step flow with `LLM rewrites yaml + writes audit log → guard checks lock-fields only → done`. The referee retreats to a pure schema/lock-field validator with no opinion on strategy quality.

Status: **DRAFT · awaiting human confirmation** (proposed by claude agent · 2026-05-23 · user authorised LLM full agency).
