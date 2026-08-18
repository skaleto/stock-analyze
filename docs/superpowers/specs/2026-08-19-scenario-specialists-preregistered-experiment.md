# Scenario Specialists Preregistered Experiment

## Authorization and boundary

The operator explicitly authorized a new controlled scenario-model experiment on
2026-08-19. This is a new Challenger campaign, not a continuation of the stopped
event-interaction campaign. The machine-readable source of truth is
`configs/research/scenario_model_v1.yaml`; this document is its explanation.

The experiment is Research-only. It cannot change formal overlays, orders,
Champion pointers, Shadow registries, or account ledgers. The 2025+ historical
test remains closed. The six event datasets collected on 2026-08-18 and every
feature with an event/news/policy/announcement prefix are forbidden.

## Economic question

Does a fixed three-state, point-in-time market router make a small conditional
residual model more useful than both the current transparent rule and an
otherwise identical pooled residual model? The question is asked once for each
of HS300, ZZ500, QDII Hong Kong exposure, and QDII US exposure.

There are exactly four candidates. Each candidate contains three coefficient
sets but is one account-level model. No parameter search, scene-count search,
threshold search, or post-result subgroup selection is allowed.

## Point-in-time scenes

The router uses only information known at signal close: the daily cross-sectional
median of 60-session momentum and 200-session price distance, positive-momentum
breadth, and median 20-session realized volatility. The high-volatility boundary
is the 70th percentile of the prior 252 signal dates, shifted by one date and
requiring 60 prior observations.

Precedence is fixed:

1. `stress`: both trend measures are negative, or breadth is at most 45% while
   volatility is above its trailing boundary;
2. `expansion`: both trend measures are positive, breadth is at least 55%, and
   volatility is not above its trailing boundary;
3. `range`: every other observation, including the initial volatility warm-up.

The exposure budgets in the YAML are shared by router-only, pooled-residual, and
scenario-specialist ablations, so the conditional-model contribution is not
confused with a different risk budget. Existing transparent-rule exposure can
only be reduced, never increased.

## Model and ablations

All variants use the same scope-specific eight-feature allowlist and exact paper
cost replay. The estimator is one fixed Elastic Net (`alpha=0.001`,
`l1_ratio=0.25`). The conditional candidate fits separate coefficients inside
each scene using only the purged training part of each outer fold. A scene needs
at least 120 training dates or the complete candidate fails closed.

Four ablations are replayed on identical rows and costs:

1. current transparent rule;
2. transparent rule plus the fixed scene exposure router;
3. router plus a pooled 10% residual model;
4. router plus three scene-specific 10% residual experts.

The residual contribution is capped at 10% of the final cross-sectional rank.
No expert may reverse the transparent component or alter the risk budget.

## Windows and immutable outcome

Only the four expanding 2018-2024 outer folds declared in the YAML may be read.
Every training row must have `label_end_date` before its validation start. No
2025+ return, diagnostic, model selection, or threshold redesign is permitted.

A candidate passes only if every frozen gate passes, including positive net
excess, at least 3/4 improving folds versus each reference, aggregate net
improvements, bounded drawdown and turnover, nonnegative rank IC, all scenes
fitted, scene coverage, and point-in-time audit. Pass, failure, or insufficient
data is written unchanged. A pass remains Research and requires a later, separate
deployment-consistency review before any Shadow admission.
