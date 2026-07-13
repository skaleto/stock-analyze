import unittest
from pathlib import Path


UNIT_DIR = Path("deploy/systemd")


class PredictionSystemdTest(unittest.TestCase):
    def test_daily_research_runs_after_market_data_with_lock_and_without_secrets(self):
        service = (UNIT_DIR / "stock-analyze-research.service").read_text(encoding="utf-8")
        market_data = (UNIT_DIR / "stock-analyze-market-data.service").read_text(encoding="utf-8")

        self.assertIn("After=stock-analyze-market-data.service", service)
        self.assertIn("flock", service)
        self.assertIn("prepare-research-data --offline", service)
        self.assertIn("run-prediction-research --offline", service)
        self.assertIn(" predict --offline", service)
        self.assertNotIn("EnvironmentFile=/etc/stock-analyze/secrets.env", service)
        self.assertIn("EnvironmentFile=/etc/stock-analyze/secrets.env", market_data)
        self.assertIn("stock-analyze-research.service", market_data)
        self.assertIn("OnFailure=stock-analyze-pipeline-failure@%n.service", service)

    def test_monthly_training_only_registers_challengers(self):
        service = (UNIT_DIR / "stock-analyze-model-training.service").read_text(encoding="utf-8")
        timer = (UNIT_DIR / "stock-analyze-model-training.timer").read_text(encoding="utf-8")

        self.assertIn("train-prediction-models --offline", service)
        self.assertNotIn("active", service.lower())
        self.assertIn("flock", service)
        self.assertIn("OnCalendar=*-*-01 02:30:00 Asia/Shanghai", timer)
        self.assertIn("Persistent=true", timer)


if __name__ == "__main__":
    unittest.main()
