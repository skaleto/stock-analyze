"""Shared visual tokens and helpers for all dashboard renderers.

Three dashboard renderers (``reporting.py``, ``beginner_dashboard.py``,
``dashboard_aggregator.py``) currently embed their own inline ``<style>``
blocks. This module is the single source of truth for the dark Bloomberg
theme (colors, spacing, typography) plus the cross-page top navigation,
so every page looks and behaves the same.

The HTML is rendered to disk and served by a vanilla ``http.server`` — no
template engine, no static-file pipeline. So we expose Python string
constants and tiny helper functions that the renderers can ``str.format``
or f-string directly into their templates.

Override scope:
  This module was introduced under a §7.0 source-edit override
  authorized 2026-05-24 by the human operator, scoped to dashboard
  view-layer refactor. See ``data/claude/notes/2026-05-24-dashboard-
  ia-proposal.md`` for context.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable


# ---------------------------------------------------------------------------
# Color / spacing / typography tokens — embedded as CSS custom properties.
#
# Palette: Bloomberg terminal-inspired dark theme with amber accent and
# A-share-convention up=red, down=green semantics. Two agent colors
# preserved because dual-agent comparison is the core narrative.

BASE_CSS = """\
:root {
  /* Background layers */
  --bg-base:        #0a0e1a;
  --bg-elevated:    #11151f;
  --bg-overlay:     #1a1f2e;

  /* Borders & dividers */
  --border-subtle:  #1f2433;
  --border-strong:  #2a3145;

  /* Text */
  --text-primary:   #e6edf3;
  --text-secondary: #8b95a7;
  --text-tertiary:  #5a6478;

  /* Accent (Bloomberg amber) */
  --accent:         #f59e0b;
  --accent-dim:     #b87333;

  /* P&L (A-share convention: red=up, green=down) */
  --pos:            #ef4444;
  --neg:            #22c55e;
  --pos-bg:         rgba(239, 68, 68, 0.10);
  --neg-bg:         rgba(34, 197, 94, 0.10);

  /* Agent colors */
  --claude:         #f59e0b;
  --codex:          #06b6d4;
  --tie:            #6b7280;

  /* Benchmarks */
  --bench-hs300:    #6b7280;
  --bench-zz500:    #8b95a7;

  /* Spacing scale */
  --space-xs:       4px;
  --space-sm:       8px;
  --space-md:       16px;
  --space-lg:       24px;
  --space-xl:       40px;

  /* Typography */
  --font-sans: -apple-system, BlinkMacSystemFont, "PingFang SC",
               "Helvetica Neue", "Microsoft YaHei", sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", "Menlo", "Consolas", monospace;

  /* Radii */
  --radius-sm:      4px;
  --radius-md:      6px;
  --radius-lg:      10px;
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background: var(--bg-base);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.4;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* All numeric values look better in monospace */
.num, .metric, .kpi-value, table td.num, table th.num {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

a { color: var(--accent); text-decoration: none; }
a:hover { color: var(--accent-dim); text-decoration: underline; }

.pos { color: var(--pos); }
.neg { color: var(--neg); }
"""


# ---------------------------------------------------------------------------
# Top navigation — included verbatim at the top of every page so the user
# can cross-jump between simple/pro/agent views without typing URLs.

NAV_CSS = """\
.dashboard-nav {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-lg);
  padding: var(--space-sm) var(--space-lg);
  background: var(--bg-elevated);
  border-bottom: 1px solid var(--border-strong);
  font-size: 13px;
}
.dashboard-nav .nav-group { display: flex; align-items: center; gap: var(--space-xs); }
.dashboard-nav .nav-label {
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 11px;
  margin-right: var(--space-xs);
}
.dashboard-nav .nav-link {
  color: var(--text-secondary);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  text-decoration: none;
  transition: background 0.12s, color 0.12s;
}
.dashboard-nav .nav-link:hover {
  background: var(--bg-overlay);
  color: var(--text-primary);
  text-decoration: none;
}
.dashboard-nav .nav-link[data-active="true"] {
  background: var(--bg-overlay);
  color: var(--accent);
}
.dashboard-nav .nav-meta {
  margin-left: auto;
  display: flex;
  gap: var(--space-md);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-size: 12px;
}
.dashboard-nav .nav-meta span {
  white-space: nowrap;
  cursor: help;
}
"""


# Logical (URL, label) pairs grouped into the two nav clusters.
_NAV_LINKS: tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "简化",
        (
            ("simple", "/", "合并"),
            ("simple-claude", "/simple/claude.html", "Claude"),
            ("simple-codex", "/simple/codex.html", "Codex"),
        ),
    ),
    (
        "专业",
        (
            ("pro", "/pro.html", "合并"),
            ("pro-claude", "/pro/claude.html", "Claude"),
            ("pro-codex", "/pro/codex.html", "Codex"),
        ),
    ),
)


def render_nav_html(
    active: str | None = None,
    generated_at: datetime | str | None = None,
    data_as_of: str | None = None,
) -> str:
    """Render the sticky top nav as a single HTML string.

    Parameters
    ----------
    active : str | None
        Active page key. One of: simple, simple-claude, simple-codex,
        pro, pro-claude, pro-codex. Pass ``None`` on a page not in the
        nav (e.g. legacy single-agent), no link is highlighted.
    generated_at : datetime | str | None
        When this HTML was generated. Defaults to now.
    data_as_of : str | None
        The "data freshness" date (e.g. "2026-05-22"). Optional —
        skipped if not given.
    """

    if generated_at is None:
        generated_at = datetime.now()
    if isinstance(generated_at, datetime):
        gen_iso = generated_at.isoformat(timespec="seconds")
        gen_human = generated_at.strftime("页面生成 %H:%M")
    else:
        gen_iso = str(generated_at)
        gen_human = f"页面生成 {generated_at}"

    groups: list[str] = []
    for label, links in _NAV_LINKS:
        items = [
            f'<a class="nav-link" href="{href}" data-active="{str(key == active).lower()}">{text}</a>'
            for key, href, text in links
        ]
        groups.append(
            f'<div class="nav-group"><span class="nav-label">{label}</span>{"".join(items)}</div>'
        )

    meta_items = [f'<span title="{gen_iso}">{gen_human}</span>']
    if data_as_of:
        meta_items.append(f'<span title="数据 as_of: {data_as_of}">数据截至 {data_as_of}</span>')

    return (
        '<nav class="dashboard-nav">'
        + "".join(groups)
        + f'<div class="nav-meta">{"".join(meta_items)}</div>'
        + "</nav>"
    )


# ---------------------------------------------------------------------------
# Convenience: full HTML <head> block ready for f-string embedding.
# Renderers can use this to avoid hand-rolling the same head boilerplate.

def render_head(title: str, extra_css: str = "") -> str:
    """Return a complete ``<head>`` element wired up with BASE_CSS + NAV_CSS."""

    return (
        '<head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{title}</title>'
        '<style>'
        f'{BASE_CSS}\n{NAV_CSS}\n{extra_css}'
        '</style>'
        '</head>'
    )
