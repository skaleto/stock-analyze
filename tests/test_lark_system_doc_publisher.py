from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "publish_system_doc_to_lark.py"
SPEC = importlib.util.spec_from_file_location("publish_system_doc_to_lark", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MarkdownBlockTests(unittest.TestCase):
    def test_converts_headings_lists_and_paragraphs(self) -> None:
        blocks = MODULE.markdown_to_blocks(
            "# Document title\n\n## Architecture\n\n- First\n- Second\n\n1. Verify\n\nBody.\n"
        )

        self.assertEqual(blocks[0]["block_type"], 4)
        self.assertEqual(blocks[0]["heading2"]["elements"][0]["text_run"]["content"], "Architecture")
        self.assertEqual(blocks[1]["block_type"], 12)
        self.assertEqual(blocks[3]["block_type"], 13)
        self.assertEqual(blocks[4]["block_type"], 2)

    def test_skips_first_h1_because_document_has_a_title(self) -> None:
        blocks = MODULE.markdown_to_blocks("# Stock Analyze\n\n## Scope\n")
        rendered = str(blocks)
        self.assertNotIn("Stock Analyze", rendered)
        self.assertIn("Scope", rendered)

    def test_publisher_uses_docx_and_permission_endpoints(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("/docx/v1/documents", source)
        self.assertIn("/children", source)
        self.assertIn("/drive/v1/permissions/", source)
        self.assertIn('"full_access"', source)
        self.assertIn("readback", source)


if __name__ == "__main__":
    unittest.main()
