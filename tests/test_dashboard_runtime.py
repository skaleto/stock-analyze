from __future__ import annotations

import subprocess
import unittest

from stock_analyze.dashboard_runtime import (
    RUNTIME_SERVICE_UNITS,
    RUNTIME_TIMER_UNITS,
    _project_service,
    read_dashboard_runtime,
)


def _batch_show_result(
    command: list[str],
) -> subprocess.CompletedProcess[str]:
    units = [
        value
        for value in command
        if value.endswith(".service") or value.endswith(".timer")
    ]
    blocks: list[str] = []
    for unit in units:
        if unit.endswith(".timer"):
            blocks.append(
                "\n".join(
                    [
                        f"Id={unit}",
                        "ActiveState=active",
                        "LastTriggerUSec=Wed 2026-07-30 12:30:00 CST",
                        "NextElapseUSecRealtime=Wed 2026-07-30 16:30:00 CST",
                    ]
                )
            )
        else:
            blocks.append(
                "\n".join(
                    [
                        f"Id={unit}",
                        "ActiveState=inactive",
                        "SubState=dead",
                        "Result=success",
                        "ExecMainStatus=0",
                        "ExecMainStartTimestamp=Wed 2026-07-30 12:30:00 CST",
                        "ExecMainExitTimestamp=Wed 2026-07-30 12:31:00 CST",
                    ]
                )
            )
    return subprocess.CompletedProcess(command, 0, "\n\n".join(blocks), "")


class DashboardRuntimeTests(unittest.TestCase):
    def test_reads_only_fixed_allowlisted_units_in_two_batch_calls(self) -> None:
        calls: list[list[str]] = []

        def runner(command, **_kwargs):
            calls.append(list(command))
            return _batch_show_result(list(command))

        payload = read_dashboard_runtime(runner=runner, cache={})

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[:2] == ["systemctl", "show"] for call in calls))
        requested = {
            value
            for call in calls
            for value in call
            if value.endswith(".service") or value.endswith(".timer")
        }
        self.assertEqual(
            requested,
            set(RUNTIME_SERVICE_UNITS) | set(RUNTIME_TIMER_UNITS),
        )
        self.assertEqual(payload["status"], "available")
        self.assertEqual(
            set(payload["services"]),
            set(RUNTIME_SERVICE_UNITS),
        )
        self.assertEqual(
            set(payload["timers"]),
            set(RUNTIME_TIMER_UNITS),
        )
        self.assertEqual(
            payload["services"]["stock-analyze-intelligence.service"]["result"],
            "success",
        )

    def test_missing_systemctl_degrades_without_raising(self) -> None:
        def runner(_command, **_kwargs):
            raise FileNotFoundError("systemctl")

        payload = read_dashboard_runtime(runner=runner, cache={})

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason"], "runtime_status_unavailable")
        self.assertEqual(payload["services"], {})
        self.assertEqual(payload["timers"], {})

    def test_failure_returns_complete_last_successful_snapshot_as_stale(self) -> None:
        cache: dict = {}
        calls = 0

        def runner(command, **_kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                return _batch_show_result(list(command))
            raise OSError("systemd bus unavailable")

        first = read_dashboard_runtime(runner=runner, cache=cache)
        second = read_dashboard_runtime(runner=runner, cache=cache)

        self.assertEqual(calls, 3)
        self.assertEqual(first["status"], "available")
        self.assertEqual(second["status"], "unavailable")
        self.assertEqual(second["last_known_at"], first["generated_at"])
        self.assertEqual(second["services"], first["services"])
        self.assertEqual(second["timers"], first["timers"])

    def test_nonzero_systemctl_result_uses_stale_snapshot(self) -> None:
        cache: dict = {}
        successful_calls = 0

        def runner(command, **_kwargs):
            nonlocal successful_calls
            if successful_calls < 2:
                successful_calls += 1
                return _batch_show_result(list(command))
            return subprocess.CompletedProcess(command, 1, "", "bus unavailable")

        first = read_dashboard_runtime(runner=runner, cache=cache)
        second = read_dashboard_runtime(runner=runner, cache=cache)

        self.assertEqual(first["status"], "available")
        self.assertEqual(second["status"], "unavailable")
        self.assertEqual(second["services"], first["services"])

    def test_artifact_worker_exit_75_is_preserved_for_status_mapping(self) -> None:
        row = _project_service(
            {
                "ActiveState": "inactive",
                "SubState": "dead",
                "Result": "success",
                "ExecMainStatus": "75",
            }
        )

        self.assertEqual(row["result"], "success")
        self.assertEqual(row["exitStatus"], 75)


if __name__ == "__main__":
    unittest.main()
