from __future__ import annotations

import unittest

from stock_analyze.intelligence.semantic.document_ir import (
    DOCUMENT_IR_VERSION,
    DocumentIRPreflightError,
    build_document_ir,
    ir_nodes_by_id,
    preflight_document_ir,
    preflight_evidence_packet,
    project_document_ir,
)


def _cell(row: int, column: int, text: str) -> dict[str, object]:
    return {
        "row_index": row,
        "column_index": column,
        "bbox": [column * 100, row * 20, (column + 1) * 100, (row + 1) * 20],
        "text": text,
    }


def _table(
    *,
    table_id: str = "table-1",
    page_number: int = 2,
    cells: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "table_id": table_id,
        "page_number": page_number,
        "bbox": [0, 0, 400, 100],
        "cells": cells
        or [
            _cell(0, 0, "项目"),
            _cell(0, 1, "主要会计数据"),
            _cell(0, 2, ""),
            _cell(0, 3, ""),
            _cell(1, 0, ""),
            _cell(1, 1, "本报告期"),
            _cell(1, 2, "上年同期"),
            _cell(1, 3, "本报告期比上年同期增减"),
            _cell(2, 0, "营业收入（元）"),
            _cell(2, 1, "621,408,705.13"),
            _cell(2, 2, "415,233,872.26"),
            _cell(2, 3, "49.65%"),
        ],
    }


def _ir(*, tables: list[dict[str, object]] | None = None) -> dict[str, object]:
    return build_document_ir(
        document={
            "id": 1341091,
            "title": "2026年半年度报告",
            "name": "深城交科技集团股份有限公司",
        },
        chunks=[
            {
                "chunk_id": "body-unit",
                "page_number": 2,
                "section": "body",
                "bbox": [400, 10, 500, 20],
                "text": "单位：元",
            },
            {
                "chunk_id": "body-footnote",
                "page_number": 2,
                "section": "body",
                "bbox": [0, 110, 400, 130],
                "text": "注：本报告期数据未经审计。",
            },
        ],
        tables=tables or [_table()],
        parser_version="announcement-layout-v1",
    )


class DocumentIRTest(unittest.TestCase):
    def test_multiline_grouped_number_is_a_value_cell(self) -> None:
        table = _table(
            cells=[
                _cell(0, 0, "股东名称"),
                _cell(0, 1, "本次质押数量（股）"),
                _cell(1, 0, "测试股东"),
                _cell(1, 1, "6,000,\n000"),
            ]
        )

        document_ir = build_document_ir(
            document={"id": 1, "title": "股份质押公告", "name": "测试股份"},
            chunks=[],
            tables=[table],
            parser_version="test-v1",
        )
        value = ir_nodes_by_id(document_ir)["table-1-r1-c1"]

        self.assertEqual(value["semantic_role"], "value")
        self.assertEqual(value["unit_resolution"]["value"], "股")
        self.assertTrue(value["row_header_path"])

    def test_builds_complete_multilevel_table_semantics_deterministically(self) -> None:
        first = _ir()
        second = _ir()

        self.assertEqual(first, second)
        self.assertEqual(first["ir_version"], DOCUMENT_IR_VERSION)
        preflight_document_ir(first)

        nodes = ir_nodes_by_id(first)
        value = nodes["table-1-r2-c1"]
        self.assertEqual(value["raw_value"], "621,408,705.13")
        self.assertEqual(
            [nodes[item["node_id"]]["text"] for item in value["row_header_path"]],
            ["营业收入（元）"],
        )
        self.assertEqual(
            [nodes[item["node_id"]]["text"] for item in value["column_header_path"]],
            ["主要会计数据", "本报告期"],
        )
        self.assertEqual(value["unit_resolution"]["value"], "元")
        self.assertEqual(value["unit_resolution"]["rule"], "row_header_unit")
        self.assertEqual(value["unit_resolution"]["conflicts"], [])
        self.assertEqual(value["footnote_node_ids"], ["body-footnote"])
        self.assertIn("merged_header_expansion", value["parser_provenance"]["transformations"])
        preflight_evidence_packet(first, ["table-1-r2-c1"])

    def test_explicit_percent_value_is_not_confused_with_row_currency(self) -> None:
        ir = _ir()
        value = ir_nodes_by_id(ir)["table-1-r2-c3"]

        self.assertEqual(value["unit_resolution"]["value"], "%")
        self.assertEqual(value["unit_resolution"]["rule"], "cell_explicit_unit")
        self.assertEqual(value["unit_resolution"]["conflicts"], [])
        preflight_evidence_packet(ir, ["table-1-r2-c3"])

    def test_company_shareholder_word_does_not_override_currency_unit(self) -> None:
        table = _table()
        table["cells"].extend(
            [
                _cell(3, 0, "归属于上市公司股东的净利润（元）"),
                _cell(3, 1, "38,544,455.63"),
                _cell(3, 2, "21,001,145.88"),
                _cell(3, 3, "83.53%"),
            ]
        )

        value = ir_nodes_by_id(_ir(tables=[table]))["table-1-r3-c1"]

        self.assertEqual(value["unit_resolution"]["value"], "元")
        self.assertEqual(value["unit_resolution"]["rule"], "row_header_unit")

    def test_repeated_headers_on_adjacent_pages_share_continuation_group(self) -> None:
        first = _table(table_id="table-a", page_number=2)
        second = _table(table_id="table-b", page_number=3)
        ir = _ir(tables=[first, second])
        nodes = ir_nodes_by_id(ir)

        self.assertEqual(
            nodes["table-a-r2-c1"]["continuation_group_id"],
            nodes["table-b-r2-c1"]["continuation_group_id"],
        )

    def test_repeated_headers_on_nonadjacent_pages_do_not_form_a_continuation(self) -> None:
        first = _table(table_id="table-a", page_number=2)
        second = _table(table_id="table-b", page_number=5)
        ir = _ir(tables=[first, second])
        nodes = ir_nodes_by_id(ir)

        self.assertNotEqual(
            nodes["table-a-r2-c1"]["continuation_group_id"],
            nodes["table-b-r2-c1"]["continuation_group_id"],
        )

    def test_selected_unit_conflict_is_rejected_before_provider_call(self) -> None:
        table = _table()
        table["cells"] = [
            {**cell, "text": "营业收入（万元）"}
            if cell["row_index"] == 2 and cell["column_index"] == 0
            else cell
            for cell in table["cells"]
        ]
        ir = _ir(tables=[table])

        with self.assertRaises(DocumentIRPreflightError) as caught:
            preflight_evidence_packet(ir, ["table-1-r2-c1"])
        self.assertEqual(caught.exception.code, "evidence_packet_unit_conflict")

    def test_dangling_header_path_is_structurally_invalid(self) -> None:
        import copy

        ir = _ir()
        mutated = copy.deepcopy(ir)
        node = next(
            item
            for item in mutated["nodes"]
            if item["node_id"] == "table-1-r2-c1"
        )
        node["column_header_path"][0]["node_id"] = "missing-header"

        with self.assertRaises(DocumentIRPreflightError) as caught:
            preflight_document_ir(mutated)
        self.assertEqual(caught.exception.code, "document_ir_relation_missing")

    def test_projection_keeps_the_complete_relation_closure(self) -> None:
        source = _ir()

        projected = project_document_ir(source, ["table-1-r2-c1"])

        nodes = ir_nodes_by_id(projected)
        self.assertIn("table-1-r2-c1", nodes)
        self.assertIn("table-1-r2-c0", nodes)
        self.assertIn("table-1-r0-c1", nodes)
        self.assertIn("table-1-r1-c1", nodes)
        self.assertIn("table-1", nodes)
        self.assertIn("body-footnote", nodes)
        preflight_document_ir(projected)
        preflight_evidence_packet(projected, ["table-1-r2-c1"])


if __name__ == "__main__":
    unittest.main()
