from __future__ import annotations

import io
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

import fitz
from pypdf import PdfWriter

from stock_analyze.intelligence import document_parser as parser_module
from stock_analyze.intelligence.document_parser import (
    AnnouncementDocumentParser,
    DocumentParserConfig,
    OcrOutput,
    OcrRequest,
    OcrUnavailableError,
    OcrWord,
)


def _text_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text(
        (72, 72),
        "证券代码：600000  回购金额：1.20亿元",
        fontname="china-s",
    )
    payload = document.tobytes()
    document.close()
    return payload


def _table_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page()
    x_positions = (72, 220, 380)
    y_positions = (72, 108, 144)
    for x_position in x_positions:
        page.draw_line(
            (x_position, y_positions[0]),
            (x_position, y_positions[-1]),
            color=(0, 0, 0),
            width=0.8,
        )
    for y_position in y_positions:
        page.draw_line(
            (x_positions[0], y_position),
            (x_positions[-1], y_position),
            color=(0, 0, 0),
            width=0.8,
        )
    for x_position, y_position, text in (
        (82, 95, "证券代码"),
        (230, 95, "回购金额"),
        (82, 131, "600000"),
        (230, 131, "1.20亿元"),
    ):
        page.insert_text(
            (x_position, y_position),
            text,
            fontname="china-s",
            fontsize=10,
        )
    payload = document.tobytes()
    document.close()
    return payload


def _scanned_pdf() -> bytes:
    source = fitz.open()
    source_page = source.new_page(width=595, height=180)
    source_page.insert_text(
        (36, 80),
        "证券代码：600000  回购金额：1.20亿元",
        fontname="china-s",
        fontsize=20,
    )
    image_payload = source_page.get_pixmap(dpi=150, alpha=False).tobytes(
        "png"
    )
    source.close()

    scanned = fitz.open()
    scanned_page = scanned.new_page(width=595, height=842)
    scanned_page.insert_image(
        fitz.Rect(0, 0, 595, 180),
        stream=image_payload,
    )
    payload = scanned.tobytes()
    scanned.close()
    return payload


def _password_protected_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.encrypt("secret")
    payload = io.BytesIO()
    writer.write(payload)
    return payload.getvalue()


def _empty_pdf() -> bytes:
    writer = PdfWriter()
    payload = io.BytesIO()
    writer.write(payload)
    return payload.getvalue()


class RecordingOcrRunner:
    def __init__(
        self,
        output: OcrOutput | None = None,
        error: Exception | None = None,
    ) -> None:
        self.output = output
        self.error = error
        self.requests: list[OcrRequest] = []

    def __call__(self, request: OcrRequest) -> OcrOutput:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if self.output is None:
            raise AssertionError("test OCR output was not configured")
        return self.output


class IntelligenceDocumentParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = DocumentParserConfig(
            parser_version="announcement-layout-v1",
            min_text_characters_per_page=20,
            ocr_languages="chi_sim+eng",
            ocr_render_dpi=300,
            extract_tables=True,
        )

    def test_extracts_native_blocks_words_and_deterministic_chunks(self) -> None:
        runner = RecordingOcrRunner(
            error=AssertionError("OCR must not run for native text")
        )
        parser = AnnouncementDocumentParser(
            config=self.config,
            ocr_runner=runner,
        )
        payload = _text_pdf()

        first = parser.parse(
            payload,
            document_id=17,
            artifact_id="pdf-17",
        )
        second = parser.parse(
            payload,
            document_id=17,
            artifact_id="pdf-17",
        )

        self.assertEqual(first.status, "parsed")
        self.assertEqual(first.parser_version, "announcement-layout-v1")
        self.assertEqual(first.pages[0].page_number, 1)
        self.assertIn("600000", first.pages[0].text)
        self.assertTrue(first.pages[0].chunks[0].bbox)
        self.assertFalse(first.pages[0].ocr_used)
        self.assertTrue(first.pages[0].words)
        self.assertEqual(first.pages[0].chunks, second.pages[0].chunks)
        self.assertEqual(first.content_hash, second.content_hash)
        self.assertEqual(runner.requests, [])

    def test_records_pdfplumber_table_cells_with_page_coordinates(self) -> None:
        parser = AnnouncementDocumentParser(
            config=DocumentParserConfig(
                parser_version="announcement-layout-v1",
                min_text_characters_per_page=1,
                ocr_languages="chi_sim+eng",
                ocr_render_dpi=300,
                extract_tables=True,
            ),
            ocr_runner=RecordingOcrRunner(
                error=AssertionError("OCR must not run for a text table")
            ),
        )

        parsed = parser.parse(
            _table_pdf(),
            document_id=18,
            artifact_id="pdf-18",
        )

        self.assertEqual(parsed.status, "parsed")
        self.assertEqual(parsed.tables[0].page_number, 1)
        self.assertTrue(parsed.tables[0].bbox)
        cells = {
            (cell.row_index, cell.column_index): cell.text
            for cell in parsed.tables[0].cells
        }
        self.assertEqual(cells[(0, 0)], "证券代码")
        self.assertEqual(cells[(1, 0)], "600000")
        self.assertEqual(cells[(1, 1)], "1.20亿元")

    def test_scanned_page_uses_injected_ocr_and_preserves_word_boxes(self) -> None:
        runner = RecordingOcrRunner(
            output=OcrOutput(
                text="证券代码 600000 回购金额 1.20亿元",
                words=(
                    OcrWord(
                        text="证券代码",
                        bbox=(300.0, 300.0, 600.0, 360.0),
                        confidence=0.98,
                    ),
                    OcrWord(
                        text="600000",
                        bbox=(630.0, 300.0, 900.0, 360.0),
                        confidence=0.96,
                    ),
                ),
            )
        )
        parser = AnnouncementDocumentParser(
            config=self.config,
            ocr_runner=runner,
        )

        parsed = parser.parse(
            _scanned_pdf(),
            document_id=19,
            artifact_id="pdf-19",
        )

        self.assertEqual(parsed.status, "parsed")
        self.assertTrue(parsed.pages[0].ocr_used)
        self.assertIn("600000", parsed.pages[0].text)
        self.assertTrue(parsed.pages[0].chunks[0].ocr_used)
        self.assertAlmostEqual(
            parsed.pages[0].words[0].bbox[0],
            72.0,
            delta=0.05,
        )
        self.assertAlmostEqual(
            parsed.pages[0].words[0].confidence or 0.0,
            0.98,
            places=4,
        )
        self.assertEqual(len(runner.requests), 1)
        self.assertEqual(runner.requests[0].page_number, 1)
        self.assertEqual(runner.requests[0].dpi, 300)
        self.assertEqual(runner.requests[0].languages, "chi_sim+eng")
        self.assertTrue(runner.requests[0].image_png.startswith(b"\x89PNG"))

    def test_uses_pypdf_only_when_native_layout_has_too_little_text(self) -> None:
        parser = AnnouncementDocumentParser(
            config=self.config,
            ocr_runner=RecordingOcrRunner(
                error=AssertionError("pypdf fallback should avoid OCR")
            ),
        )
        with (
            mock.patch.object(
                parser_module,
                "_extract_pymupdf_blocks",
                return_value=(),
            ),
            mock.patch.object(
                parser_module,
                "_extract_pymupdf_words",
                return_value=(),
            ),
            mock.patch.object(
                parser_module,
                "_extract_pypdf_page_text",
                return_value="证券代码 600000 来自 pypdf 的回退文本",
            ) as fallback,
        ):
            parsed = parser.parse(
                _text_pdf(),
                document_id=20,
                artifact_id="pdf-20",
            )

        self.assertEqual(parsed.status, "parsed")
        self.assertIn("600000", parsed.pages[0].text)
        self.assertEqual(parsed.pages[0].chunks[0].section, "pypdf_fallback")
        fallback.assert_called_once()

    def test_native_text_does_not_call_pypdf_fallback(self) -> None:
        parser = AnnouncementDocumentParser(
            config=self.config,
            ocr_runner=RecordingOcrRunner(
                error=AssertionError("OCR must not run for native text")
            ),
        )
        with mock.patch.object(
            parser_module,
            "_extract_pypdf_page_text",
            side_effect=AssertionError(
                "pypdf is a fallback, not a primary extractor"
            ),
        ):
            parsed = parser.parse(
                _text_pdf(),
                document_id=21,
                artifact_id="pdf-21",
            )

        self.assertEqual(parsed.status, "parsed")

    def test_records_are_deeply_immutable(self) -> None:
        parser = AnnouncementDocumentParser(
            config=self.config,
            ocr_runner=RecordingOcrRunner(
                error=AssertionError("OCR must not run for native text")
            ),
        )
        parsed = parser.parse(
            _table_pdf(),
            document_id=22,
            artifact_id="pdf-22",
        )

        with self.assertRaises(FrozenInstanceError):
            parsed.status = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            parsed.pages[0].text = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            parsed.pages[0].chunks[0].text = "changed"  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            parsed.tables[0].cells[0].text = "changed"  # type: ignore[misc]
        self.assertIsInstance(parsed.pages, tuple)
        self.assertIsInstance(parsed.tables, tuple)
        self.assertIsInstance(parsed.pages[0].chunks, tuple)
        self.assertIsInstance(parsed.tables[0].cells, tuple)

    def test_password_corrupt_empty_and_ocr_failure_are_distinct(self) -> None:
        parser = AnnouncementDocumentParser(
            config=self.config,
            ocr_runner=RecordingOcrRunner(
                error=OcrUnavailableError("tesseract_unavailable")
            ),
        )

        password = parser.parse(
            _password_protected_pdf(),
            document_id=23,
            artifact_id="pdf-23",
        )
        corrupt = parser.parse(
            b"%PDF-1.7\nthis is not a complete PDF",
            document_id=24,
            artifact_id="pdf-24",
        )
        empty = parser.parse(
            _empty_pdf(),
            document_id=25,
            artifact_id="pdf-25",
        )
        ocr_failed = parser.parse(
            _scanned_pdf(),
            document_id=26,
            artifact_id="pdf-26",
        )

        self.assertEqual(password.status, "password_protected")
        self.assertEqual(corrupt.status, "corrupt")
        self.assertEqual(empty.status, "empty")
        self.assertEqual(ocr_failed.status, "ocr_failed")
        self.assertEqual(ocr_failed.pages[0].status, "ocr_failed")
        self.assertTrue(ocr_failed.pages[0].ocr_used)
        self.assertNotEqual(ocr_failed.status, "no_event")
        self.assertEqual(
            {
                password.status,
                corrupt.status,
                empty.status,
                ocr_failed.status,
            },
            {"password_protected", "corrupt", "empty", "ocr_failed"},
        )

    def test_empty_ocr_output_is_ocr_failed_not_no_event(self) -> None:
        parser = AnnouncementDocumentParser(
            config=self.config,
            ocr_runner=RecordingOcrRunner(
                output=OcrOutput(text="", words=())
            ),
        )

        parsed = parser.parse(
            _scanned_pdf(),
            document_id=27,
            artifact_id="pdf-27",
        )

        self.assertEqual(parsed.status, "ocr_failed")
        self.assertEqual(parsed.error, "ocr_no_text")
        self.assertNotEqual(parsed.status, "no_event")


if __name__ == "__main__":
    unittest.main()
