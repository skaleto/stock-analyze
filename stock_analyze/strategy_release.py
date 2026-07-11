"""Apply an audited, idempotent multi-market strategy release."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import competition, overlay_guard
from .evolution_writer import write_evolution
from .strategy_registry import (
    load_strategy_registry,
    validate_strategy_pair,
)
from .utils import now_iso, read_json, write_json


class StrategyReleaseInvalid(ValueError):
    """The release manifest cannot be safely applied."""


def _pending_order_count(pending: list[Any]) -> int:
    count = 0
    for item in pending:
        if isinstance(item, dict) and isinstance(item.get("orders"), list):
            count += len(item["orders"])
        else:
            count += 1
    return count


def _archive_pending_orders(
    *,
    root: Path,
    release_id: str,
    market: str,
    agent_id: str,
    from_strategy_id: str,
    to_strategy_id: str,
) -> dict[str, Any]:
    paths = competition.resolve_market_paths(market, agent_id, root)
    pending_path = paths.data_dir / "pending_orders.json"
    archive_path = paths.data_dir / "pending_order_archive" / f"{release_id}.json"
    current = read_json(pending_path, [])
    if not isinstance(current, list):
        raise StrategyReleaseInvalid(
            f"strategy_release_pending_invalid:{market}:{agent_id}"
        )

    if archive_path.exists():
        archive = _read_json(archive_path, "pending_archive")
        archived_pending = archive.get("pending")
        if not isinstance(archived_pending, list):
            raise StrategyReleaseInvalid(
                f"strategy_release_pending_archive_invalid:{market}:{agent_id}"
            )
        # Recover a crash between writing the archive and clearing the queue.
        # Orders generated after a completed release differ and remain active.
        if current and current == archived_pending:
            write_json(pending_path, [])
        return {
            "pending_orders_archived": int(
                archive.get("order_count") or _pending_order_count(archived_pending)
            ),
            "pending_archive_path": str(archive_path),
        }

    archive = {
        "release_id": release_id,
        "archived_at": now_iso(),
        "market": market,
        "agent_id": agent_id,
        "from_strategy_id": from_strategy_id,
        "to_strategy_id": to_strategy_id,
        "order_count": _pending_order_count(current),
        "pending": current,
    }
    write_json(archive_path, archive)
    write_json(pending_path, [])
    return {
        "pending_orders_archived": archive["order_count"],
        "pending_archive_path": str(archive_path),
    }


def _read_json(path: Path, source: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StrategyReleaseInvalid(f"strategy_release_unreadable:{source}:{path}") from exc
    if not isinstance(payload, dict):
        raise StrategyReleaseInvalid(f"strategy_release_invalid:{source}:{path}")
    return payload


def apply_strategy_release(
    manifest_path: str | Path,
    repo_root: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest = _read_json(manifest_path, "manifest")
    release_id = str(manifest.get("release_id") or "")
    month = str(manifest.get("month") or "")
    reviewer = str(manifest.get("reviewer") or "")
    entries = manifest.get("entries")
    if not release_id or not month or not reviewer or not isinstance(entries, list):
        raise StrategyReleaseInvalid("strategy_release_manifest_fields")

    prepared: list[dict[str, Any]] = []
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            raise StrategyReleaseInvalid("strategy_release_entry_type")
        market = str(raw.get("market") or "")
        agent_id = str(raw.get("agent_id") or "")
        overlay_name = str(raw.get("overlay") or "")
        reasoning = str(raw.get("reasoning") or "")
        if (
            market not in competition.MARKETS
            or not agent_id
            or not overlay_name
            or not reasoning
        ):
            raise StrategyReleaseInvalid("strategy_release_entry_fields")
        identity = (market, agent_id)
        if identity in seen:
            raise StrategyReleaseInvalid(f"strategy_release_duplicate:{market}:{agent_id}")
        seen.add(identity)
        desired_path = manifest_path.parent / overlay_name
        desired = _read_json(desired_path, "overlay")
        overlay_guard.validate(
            agent_id,
            desired,
            repo_root=root,
            market=market,
        )
        live_path = competition.resolve_market_paths(market, agent_id, root).config_path
        current = _read_json(live_path, "live_overlay")
        prepared.append(
            {
                "market": market,
                "agent_id": agent_id,
                "reasoning": reasoning,
                "current": current,
                "desired": desired,
            }
        )
        pairs.setdefault(market, {})[agent_id] = desired

    registry = load_strategy_registry(root)
    floor = float(registry["factor_distance_floor"])
    pair_results = {
        market: validate_strategy_pair(overlays, factor_distance_floor=floor)
        for market, overlays in pairs.items()
    }

    # A multi-strategy release must clear every expensive gate before the
    # first live overlay is changed. Otherwise a later failure can leave a
    # mixed release on disk.
    prevalidated_backtests: dict[tuple[str, str], Any] = {}
    if not dry_run:
        from .markets.a_share.backtest import gate as backtest_gate
        from .markets.a_share.backtest.exceptions import (
            BacktestFloorBreach,
            BacktestStructuralBreach,
        )

        for entry in prepared:
            if entry["market"] != "a_share" or entry["current"] == entry["desired"]:
                continue
            identity = (entry["market"], entry["agent_id"])
            try:
                prevalidated_backtests[identity] = (
                    backtest_gate.validate_overlay_via_backtest(
                        entry["desired"],
                        agent_id=entry["agent_id"],
                    )
                )
            except BacktestFloorBreach as exc:
                raise StrategyReleaseInvalid(
                    "strategy_release_backtest_floor:"
                    f"{entry['market']}:{entry['agent_id']}:{exc.breach_type}"
                ) from exc
            except BacktestStructuralBreach as exc:
                raise StrategyReleaseInvalid(
                    "strategy_release_backtest_structural:"
                    f"{entry['market']}:{entry['agent_id']}"
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise StrategyReleaseInvalid(
                    "strategy_release_backtest_error:"
                    f"{entry['market']}:{entry['agent_id']}:{type(exc).__name__}"
                ) from exc

    results: list[dict[str, Any]] = []
    for entry in prepared:
        market = entry["market"]
        agent_id = entry["agent_id"]
        if entry["current"] == entry["desired"]:
            row = {"market": market, "agent_id": agent_id, "status": "unchanged"}
            if not dry_run:
                row.update(
                    _archive_pending_orders(
                        root=root,
                        release_id=release_id,
                        market=market,
                        agent_id=agent_id,
                        from_strategy_id=str(entry["current"].get("strategy_id") or ""),
                        to_strategy_id=str(entry["desired"].get("strategy_id") or ""),
                    )
                )
            results.append(row)
            continue
        if dry_run:
            results.append(
                {"market": market, "agent_id": agent_id, "status": "would_evolve"}
            )
            continue
        writer_kwargs: dict[str, Any] = {}
        if market == "a_share":
            writer_kwargs["validated_backtest_metrics"] = prevalidated_backtests[
                (market, agent_id)
            ]
        result = write_evolution(
            agent_id,
            entry["current"],
            entry["desired"],
            entry["reasoning"],
            repo_root=root,
            month=month,
            reviewer=reviewer,
            market=market,
            **writer_kwargs,
        )
        pending_archive = _archive_pending_orders(
            root=root,
            release_id=release_id,
            market=market,
            agent_id=agent_id,
            from_strategy_id=str(entry["current"].get("strategy_id") or ""),
            to_strategy_id=str(entry["desired"].get("strategy_id") or ""),
        )
        results.append(
            {
                "market": market,
                "agent_id": agent_id,
                "status": result["status"],
                "from_hash": result["from_hash"],
                "to_hash": result["to_hash"],
                **pending_archive,
            }
        )
    return {
        "release_id": release_id,
        "month": month,
        "dry_run": dry_run,
        "pairs": pair_results,
        "entries": results,
    }


__all__ = ["StrategyReleaseInvalid", "apply_strategy_release"]
