from __future__ import annotations

import os
import subprocess
import threading
import unittest

from stock_analyze.dashboard_runtime import (
    RUNTIME_SERVICE_UNITS,
    RUNTIME_TIMER_UNITS,
    _project_service,
    _project_timer,
    read_dashboard_runtime,
)


def _batch_show_result(
    command: list[str],
    *,
    active_state: str | None = None,
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
                        "LoadState=loaded",
                        f"ActiveState={active_state or 'active'}",
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
                        "LoadState=loaded",
                        f"ActiveState={active_state or 'inactive'}",
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
        environments: list[dict[str, str]] = []

        def runner(command, **kwargs):
            calls.append(list(command))
            environments.append(dict(kwargs["env"]))
            return _batch_show_result(list(command))

        payload = read_dashboard_runtime(runner=runner, cache={})

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call[:2] == ["systemctl", "show"] for call in calls))
        self.assertTrue(all("--all" in call for call in calls))
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
        self.assertTrue(
            all(environment["LC_ALL"] == "C" for environment in environments)
        )
        self.assertTrue(
            all(environment["LANG"] == "C" for environment in environments)
        )
        if "PATH" in os.environ:
            self.assertTrue(
                all(
                    environment["PATH"] == os.environ["PATH"]
                    for environment in environments
                )
            )
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
        self.assertEqual(
            payload["services"]["stock-analyze-intelligence.service"][
                "loadState"
            ],
            "loaded",
        )
        self.assertEqual(
            payload["timers"]["stock-analyze-market-data.timer"]["loadState"],
            "loaded",
        )

    def test_empty_properties_are_retained_and_projected(self) -> None:
        def runner(command, **_kwargs):
            result = _batch_show_result(list(command))
            stdout = result.stdout
            if any(value.endswith(".service") for value in command):
                stdout = stdout.replace(
                    "SubState=dead",
                    "SubState=",
                ).replace(
                    "ExecMainStartTimestamp=Wed 2026-07-30 12:30:00 CST",
                    "ExecMainStartTimestamp=",
                ).replace(
                    "ExecMainExitTimestamp=Wed 2026-07-30 12:31:00 CST",
                    "ExecMainExitTimestamp=",
                )
            return subprocess.CompletedProcess(command, 0, stdout, "")

        payload = read_dashboard_runtime(runner=runner, cache={})

        self.assertEqual(payload["status"], "available")
        service = payload["services"][RUNTIME_SERVICE_UNITS[0]]
        self.assertEqual(service["subState"], "unknown")
        self.assertIsNone(service["startedAt"])
        self.assertIsNone(service["finishedAt"])

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

    def test_empty_success_output_uses_stale_snapshot_without_overwriting(self) -> None:
        cache: dict = {}
        calls = 0

        def runner(command, **_kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                return _batch_show_result(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        first = read_dashboard_runtime(runner=runner, cache=cache)
        second = read_dashboard_runtime(runner=runner, cache=cache)

        self.assertEqual(second["status"], "unavailable")
        self.assertEqual(second["services"], first["services"])
        self.assertEqual(cache["last_successful"], first)

    def test_partial_or_mismatched_batch_output_uses_stale_snapshot(self) -> None:
        cache: dict = {}
        calls = 0

        def runner(command, **_kwargs):
            nonlocal calls
            calls += 1
            result = _batch_show_result(list(command))
            if calls <= 2:
                return result
            blocks = result.stdout.split("\n\n")
            blocks[0] = blocks[0].replace(
                f"Id={RUNTIME_SERVICE_UNITS[0]}",
                "Id=not-allowlisted.service",
            )
            return subprocess.CompletedProcess(
                command,
                0,
                "\n\n".join(blocks[:-1]),
                "",
            )

        first = read_dashboard_runtime(runner=runner, cache=cache)
        second = read_dashboard_runtime(runner=runner, cache=cache)

        self.assertEqual(second["status"], "unavailable")
        self.assertEqual(second["services"], first["services"])
        self.assertEqual(cache["last_successful"], first)

    def test_malformed_success_output_degrades_without_caching(self) -> None:
        cache: dict = {}

        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(
                command,
                0,
                "Id=one.service\nthis is not a property",
                "",
            )

        payload = read_dashboard_runtime(runner=runner, cache=cache)

        self.assertEqual(payload["status"], "unavailable")
        self.assertNotIn("last_successful", cache)

    def test_timeout_degrades_without_raising(self) -> None:
        def runner(command, **_kwargs):
            raise subprocess.TimeoutExpired(command, 3)

        payload = read_dashboard_runtime(runner=runner, cache={})

        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["reason"], "runtime_status_unavailable")

    def test_parser_accepts_blank_lines_containing_whitespace(self) -> None:
        def runner(command, **_kwargs):
            result = _batch_show_result(list(command))
            return subprocess.CompletedProcess(
                command,
                0,
                result.stdout.replace("\n\n", "\n \t \n"),
                "",
            )

        payload = read_dashboard_runtime(runner=runner, cache={})

        self.assertEqual(payload["status"], "available")
        self.assertEqual(set(payload["services"]), set(RUNTIME_SERVICE_UNITS))
        self.assertEqual(set(payload["timers"]), set(RUNTIME_TIMER_UNITS))

    def test_older_concurrent_sample_cannot_overwrite_newer_cache(self) -> None:
        cache: dict = {}
        older_started = threading.Event()
        release_older = threading.Event()
        older_payload: list[dict] = []

        def older_runner(command, **_kwargs):
            if not older_started.is_set():
                older_started.set()
                self.assertTrue(release_older.wait(timeout=2))
            return _batch_show_result(list(command), active_state="inactive")

        def newer_runner(command, **_kwargs):
            return _batch_show_result(list(command), active_state="active")

        thread = threading.Thread(
            target=lambda: older_payload.append(
                read_dashboard_runtime(runner=older_runner, cache=cache)
            )
        )
        thread.start()
        self.assertTrue(older_started.wait(timeout=2))

        newer_payload = read_dashboard_runtime(runner=newer_runner, cache=cache)
        release_older.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

        def failing_runner(command, **_kwargs):
            raise OSError("systemd bus unavailable")

        stale = read_dashboard_runtime(runner=failing_runner, cache=cache)
        unit = RUNTIME_SERVICE_UNITS[0]
        self.assertEqual(older_payload[0]["services"][unit]["activeState"], "inactive")
        self.assertEqual(newer_payload["services"][unit]["activeState"], "active")
        self.assertEqual(stale["services"][unit]["activeState"], "active")

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
        self.assertEqual(row["loadState"], "loaded")

    def test_projection_preserves_explicit_unavailable_load_state(self) -> None:
        row = _project_service(
            {
                "LoadState": "not-found",
                "ActiveState": "inactive",
                "SubState": "dead",
                "Result": "success",
                "ExecMainStatus": "0",
            }
        )

        self.assertEqual(row["loadState"], "not-found")
        self.assertEqual(row["reason"], "unit_load_state_not-found")

        timer = _project_timer(
            {
                "LoadState": "masked",
                "ActiveState": "inactive",
            }
        )
        self.assertEqual(timer["loadState"], "masked")
        self.assertEqual(timer["reason"], "unit_load_state_masked")


if __name__ == "__main__":
    unittest.main()
