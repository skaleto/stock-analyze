from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_analyze.intelligence.ifind_transport import (
    IfindSdkError,
    IfindSdkTransport,
)


class IfindSdkTransportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.python = self.root / "venv" / "bin" / "python"
        self.gateway = self.root / "scripts" / "ifind_sdk_gateway.py"
        self.lib = self.root / "sdk" / "bin64"
        self.compat = self.root / "sdk" / "compat"
        self.user_file = self.root / "ifind_username"
        self.password_file = self.root / "ifind_password"
        for path in (
            self.python,
            self.gateway,
            self.lib / "libShellExport.so",
            self.compat / "libidn.so.11",
            self.user_file,
            self.password_file,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder", encoding="utf-8")
        self.transport = IfindSdkTransport(
            repo_root=self.root,
            python_path=self.python,
            gateway_path=self.gateway,
            sdk_lib_dirs=(self.lib, self.compat),
            username_file=self.user_file,
            password_file=self.password_file,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_batch_uses_secret_file_paths_without_reading_secret_values(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "success",
                    "login_code": 0,
                    "logout_code": 0,
                    "results": [{"id": "quota", "errorcode": 0}],
                }
            ),
            stderr="",
        )
        with patch(
            "stock_analyze.intelligence.ifind_transport.subprocess.run",
            return_value=completed,
        ) as run:
            result = self.transport.execute(
                [{"id": "quota", "op": "statistics"}]
            )

        self.assertEqual(result["status"], "success")
        kwargs = run.call_args.kwargs
        self.assertEqual(
            run.call_args.args[0],
            [str(self.python), str(self.gateway)],
        )
        request = json.loads(kwargs["input"])
        self.assertEqual(
            request,
            {"actions": [{"id": "quota", "op": "statistics"}]},
        )
        self.assertEqual(
            kwargs["env"]["IFIND_USER_FILE"],
            str(self.user_file),
        )
        self.assertEqual(
            kwargs["env"]["IFIND_PASSWORD_FILE"],
            str(self.password_file),
        )
        serialized = json.dumps(kwargs["env"])
        self.assertNotIn(
            self.password_file.read_text(encoding="utf-8"),
            serialized.replace(str(self.password_file), ""),
        )

    def test_history_action_fixes_original_currency_and_unadjusted_prices(self) -> None:
        action = self.transport.history_action(
            action_id="hq",
            codes=("300033.SZ", "513100.SH"),
            start_date="2026-07-20",
            end_date="2026-07-24",
        )

        self.assertEqual(action["op"], "hq")
        self.assertEqual(
            action["parameters"],
            "Interval:D,CPS:1,Currency:YSHB,fill:Omit",
        )
        self.assertEqual(
            action["indicators"],
            "open,high,low,close,volume,amount",
        )

    def test_unknown_operation_is_rejected_before_subprocess(self) -> None:
        with patch(
            "stock_analyze.intelligence.ifind_transport.subprocess.run"
        ) as run:
            with self.assertRaisesRegex(
                ValueError,
                "ifind_operation_not_allowed",
            ):
                self.transport.execute([{"id": "bad", "op": "shell"}])
        run.assert_not_called()

    def test_gateway_failure_is_normalized_without_stderr_leak(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stdout="",
            stderr="password=do-not-log",
        )
        with patch(
            "stock_analyze.intelligence.ifind_transport.subprocess.run",
            return_value=completed,
        ):
            with self.assertRaisesRegex(
                IfindSdkError,
                "^ifind_gateway_failed:2$",
            ):
                self.transport.execute(
                    [{"id": "quota", "op": "statistics"}]
                )

    def test_sdk_startup_banner_before_json_is_ignored(self) -> None:
        payload = {
            "status": "success",
            "login_code": 0,
            "logout_code": 0,
            "results": [{"id": "quota", "errorcode": 0}],
        }
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=(
                "/vendor/ifind/venv/site-packages/iFinDPy.pth\n"
                + json.dumps(payload)
                + "\n"
            ),
            stderr="",
        )
        with patch(
            "stock_analyze.intelligence.ifind_transport.subprocess.run",
            return_value=completed,
        ):
            result = self.transport.execute(
                [{"id": "quota", "op": "statistics"}]
            )

        self.assertEqual(result, payload)


if __name__ == "__main__":
    unittest.main()
