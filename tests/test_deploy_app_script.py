from __future__ import annotations

import unittest
from pathlib import Path


class DeployAppScriptTests(unittest.TestCase):
    def test_build_script_creates_the_react_artifact(self) -> None:
        script = Path("scripts/build-dashboard-app.sh").read_text(encoding="utf-8")

        self.assertIn("npm ci", script)
        self.assertIn("npm run build", script)
        self.assertIn("npm audit --omit=dev", script)
        self.assertIn("reports/app/index.html", script)

    def test_deploy_script_is_ordered_and_path_preserving(self) -> None:
        script = Path("scripts/deploy-app-to-ecs.sh").read_text(encoding="utf-8")

        required = [
            "build-dashboard-app.sh",
            "rsync",
            "--relative",
            "DEPLOY_VERSION",
            "python -m unittest",
            "systemctl daemon-reload",
            "systemctl enable --now stock-analyze-codex-cn-qdii-etf-daily.timer",
            "systemctl enable --now stock-analyze-codex-cn-qdii-etf-weekly.timer",
            "systemctl restart stock-analyze-dashboard.service",
        ]
        for token in required:
            self.assertIn(token, script)
        self.assertLess(script.index("build-dashboard-app.sh"), script.index("rsync"))
        self.assertLess(script.index("python -m unittest"), script.index("systemctl enable --now"))
        self.assertIn("/var/lib/systemd/timers/stamp-$timer", script)
        self.assertIn('if [[ ! -e "$stamp" ]]', script)
        self.assertIn("tests.test_dashboard_finance", script)
        self.assertIn("tests.test_archived_markets", script)
        self.assertIn("systemctl disable --now", script)
        self.assertIn("stock-analyze-codex-hk-daily.timer", script)
        self.assertIn("stock-analyze-codex-us-weekly.timer", script)
        self.assertNotIn("--delete data/", script)


if __name__ == "__main__":
    unittest.main()
