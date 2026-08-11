from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from stock_analyze import cli


class QDIIEventCLITests(unittest.TestCase):
    def test_refresh_command_uses_current_universe_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            universe = root / "universe.json"
            universe.write_text(
                json.dumps(
                    {
                        "scopes": {
                            "us_exposure": [{"code": "513100.SH"}],
                            "hk_exposure": [{"code": "159920.SZ"}],
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = root / "fund_events.csv"
            with patch(
                "stock_analyze.markets.cn_qdii_etf.fund_events.refresh_event_store",
                return_value=pd.DataFrame([{"event_id": "one"}]),
            ) as refresh:
                status = cli.main(
                    [
                        "refresh-qdii-events",
                        "--universe",
                        str(universe),
                        "--output",
                        str(output),
                    ]
                )

        self.assertEqual(status, 0)
        self.assertEqual(refresh.call_args.args[0], ["159920.SZ", "513100.SH"])
        self.assertEqual(refresh.call_args.args[1], output)


if __name__ == "__main__":
    unittest.main()
