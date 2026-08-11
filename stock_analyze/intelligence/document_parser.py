"""Deterministic layout, table, and OCR parsing for announcement PDFs."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import fitz
import pdfplumber
from pypdf import PdfReader


BBox = tuple[float, float, float, float]


class OcrUnavailableError(RuntimeError):
    """Raised when the configured OCR runtime cannot execute."""


@dataclass(frozen=True)
class DocumentParserConfig:
    parser_version: str = "announcement-layout-v1"
    min_text_characters_per_page: int = 20
    ocr_languages: str = "chi_sim+eng"
    ocr_render_dpi: int = 300
    extract_tables: bool = True

    def __post_init__(self) -> None:
        if not self.parser_version.strip():
            raise ValueError("document_parser_version_required")
        if self.min_text_characters_per_page < 0:
            raise ValueError("document_parser_min_text_invalid")
        if not self.ocr_languages.strip():
            raise ValueError("document_parser_ocr_languages_required")
        if self.ocr_render_dpi <= 0:
            raise ValueError("document_parser_ocr_dpi_invalid")

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "DocumentParserConfig":
        parser = value.get("parser", value)
        if not isinstance(parser, Mapping):
            raise ValueError("document_parser_config_invalid")
        return cls(
            parser_version=str(
                parser.get("version") or "announcement-layout-v1"
            ),
            min_text_characters_per_page=int(
                parser.get("min_text_characters_per_page", 20)
            ),
            ocr_languages=str(parser.get("ocr_languages") or "chi_sim+eng"),
            ocr_render_dpi=int(parser.get("ocr_render_dpi", 300)),
            extract_tables=bool(parser.get("extract_tables", True)),
        )


@dataclass(frozen=True)
class OcrWord:
    """One OCR word in rendered-image pixel coordinates."""

    text: str
    bbox: BBox
    confidence: float | None


@dataclass(frozen=True)
class OcrOutput:
    text: str
    words: tuple[OcrWord, ...]


@dataclass(frozen=True)
class OcrRequest:
    image_png: bytes
    page_number: int
    dpi: int
    languages: str
    pixel_width: int
    pixel_height: int


class OcrRunner(Protocol):
    def __call__(self, request: OcrRequest) -> OcrOutput:
        ...


@dataclass(frozen=True)
class DocumentWord:
    page_number: int
    sequence_no: int
    bbox: BBox
    text: str
    confidence: float | None
    ocr_used: bool


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    page_number: int
    sequence_no: int
    section: str
    bbox: BBox
    text: str
    text_hash: str
    ocr_used: bool
    ocr_confidence: float | None = None


@dataclass(frozen=True)
class DocumentPage:
    page_number: int
    width: float
    height: float
    text: str
    chunks: tuple[DocumentChunk, ...]
    words: tuple[DocumentWord, ...]
    ocr_used: bool
    status: str
    error: str = ""


@dataclass(frozen=True)
class DocumentTableCell:
    row_index: int
    column_index: int
    bbox: BBox
    text: str


@dataclass(frozen=True)
class DocumentTable:
    table_id: str
    page_number: int
    sequence_no: int
    bbox: BBox
    cells: tuple[DocumentTableCell, ...]


@dataclass(frozen=True)
class DocumentParseResult:
    parser_version: str
    status: str
    content_hash: str
    pages: tuple[DocumentPage, ...]
    tables: tuple[DocumentTable, ...]
    error: str = ""


class TesseractOcrRunner:
    """Lazy adapter so importing the parser never requires an OCR binary."""

    def __call__(self, request: OcrRequest) -> OcrOutput:
        try:
            from PIL import Image
            import pytesseract
            from pytesseract import Output
        except ImportError as exc:
            raise OcrUnavailableError("tesseract_python_runtime_missing") from exc

        try:
            image = Image.open(io.BytesIO(request.image_png))
            data = pytesseract.image_to_data(
                image,
                lang=request.languages,
                output_type=Output.DICT,
            )
        except pytesseract.TesseractNotFoundError as exc:
            raise OcrUnavailableError("tesseract_unavailable") from exc
        except pytesseract.TesseractError as exc:
            raise OcrUnavailableError("tesseract_execution_failed") from exc

        words: list[OcrWord] = []
        line_words: dict[tuple[int, int, int], list[str]] = {}
        for index, raw_text in enumerate(data.get("text", [])):
            text = str(raw_text).strip()
            if not text:
                continue
            confidence = _normalized_confidence(
                data.get("conf", [None] * (index + 1))[index]
            )
            left = float(data["left"][index])
            top = float(data["top"][index])
            width = float(data["width"][index])
            height = float(data["height"][index])
            words.append(
                OcrWord(
                    text=text,
                    bbox=(left, top, left + width, top + height),
                    confidence=confidence,
                )
            )
            line_key = (
                int(data.get("block_num", [0] * (index + 1))[index]),
                int(data.get("par_num", [0] * (index + 1))[index]),
                int(data.get("line_num", [0] * (index + 1))[index]),
            )
            line_words.setdefault(line_key, []).append(text)
        text = "\n".join(
            " ".join(values)
            for _, values in sorted(line_words.items())
            if values
        )
        return OcrOutput(text=text, words=tuple(words))


def _normalized_confidence(value: object) -> float | None:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    if raw < 0:
        return None
    return min(1.0, raw / 100.0)


def _rounded_bbox(value: tuple[float, float, float, float]) -> BBox:
    return tuple(round(float(item), 3) for item in value)  # type: ignore[return-value]


def _clean_text(value: object) -> str:
    lines = [
        " ".join(line.split())
        for line in str(value or "").replace("\x00", "").splitlines()
    ]
    return "\n".join(line for line in lines if line).strip()


def _text_character_count(value: str) -> int:
    return sum(not character.isspace() for character in value)


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _extract_pymupdf_blocks(
    page: fitz.Page,
) -> tuple[tuple[BBox, str], ...]:
    blocks: list[tuple[BBox, str]] = []
    for raw in page.get_text("blocks", sort=True):
        if len(raw) > 6 and int(raw[6]) != 0:
            continue
        text = _clean_text(raw[4])
        if text:
            blocks.append((_rounded_bbox(tuple(raw[:4])), text))
    return tuple(blocks)


def _extract_pymupdf_words(
    page: fitz.Page,
) -> tuple[tuple[BBox, str], ...]:
    words: list[tuple[BBox, str]] = []
    for raw in page.get_text("words", sort=True):
        text = _clean_text(raw[4])
        if text:
            words.append((_rounded_bbox(tuple(raw[:4])), text))
    return tuple(words)


def _extract_pypdf_page_text(pdf_bytes: bytes, page_index: int) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        if reader.is_encrypted or page_index >= len(reader.pages):
            return ""
        return _clean_text(reader.pages[page_index].extract_text() or "")
    except Exception:
        return ""


def _chunk_id(
    *,
    document_id: int,
    artifact_id: str,
    parser_version: str,
    page_number: int,
    sequence_no: int,
    text_hash: str,
) -> str:
    identity = "|".join(
        (
            str(document_id),
            artifact_id,
            parser_version,
            str(page_number),
            str(sequence_no),
            text_hash,
        )
    )
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return f"doc{document_id}-p{page_number}-c{sequence_no}-{suffix}"


def _table_id(
    *,
    document_id: int,
    artifact_id: str,
    parser_version: str,
    page_number: int,
    sequence_no: int,
    cells: tuple[DocumentTableCell, ...],
) -> str:
    content = "|".join(
        f"{cell.row_index}:{cell.column_index}:{cell.text}"
        for cell in cells
    )
    identity = "|".join(
        (
            str(document_id),
            artifact_id,
            parser_version,
            str(page_number),
            str(sequence_no),
            content,
        )
    )
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"doc{document_id}-p{page_number}-t{sequence_no}-{suffix}"


def _extract_pdfplumber_tables(
    pdf_bytes: bytes,
    *,
    document_id: int,
    artifact_id: str,
    parser_version: str,
) -> tuple[DocumentTable, ...]:
    tables: list[DocumentTable] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            sequence_no = 0
            for page_number, page in enumerate(pdf.pages, start=1):
                for raw_table in page.find_tables():
                    extracted_rows = raw_table.extract() or []
                    cells: list[DocumentTableCell] = []
                    for row_index, row in enumerate(extracted_rows):
                        row_boxes = (
                            raw_table.rows[row_index].cells
                            if row_index < len(raw_table.rows)
                            else ()
                        )
                        for column_index, text in enumerate(row):
                            raw_bbox = (
                                row_boxes[column_index]
                                if column_index < len(row_boxes)
                                else None
                            )
                            bbox = (
                                _rounded_bbox(tuple(raw_bbox))
                                if raw_bbox is not None
                                else (0.0, 0.0, 0.0, 0.0)
                            )
                            cells.append(
                                DocumentTableCell(
                                    row_index=row_index,
                                    column_index=column_index,
                                    bbox=bbox,
                                    text=_clean_text(text),
                                )
                            )
                    immutable_cells = tuple(cells)
                    tables.append(
                        DocumentTable(
                            table_id=_table_id(
                                document_id=document_id,
                                artifact_id=artifact_id,
                                parser_version=parser_version,
                                page_number=page_number,
                                sequence_no=sequence_no,
                                cells=immutable_cells,
                            ),
                            page_number=page_number,
                            sequence_no=sequence_no,
                            bbox=_rounded_bbox(tuple(raw_table.bbox)),
                            cells=immutable_cells,
                        )
                    )
                    sequence_no += 1
    except Exception:
        return ()
    return tuple(tables)


class AnnouncementDocumentParser:
    def __init__(
        self,
        *,
        config: DocumentParserConfig | None = None,
        ocr_runner: OcrRunner | None = None,
    ) -> None:
        self.config = config or DocumentParserConfig()
        self.ocr_runner = ocr_runner or TesseractOcrRunner()

    def parse(
        self,
        pdf_bytes: bytes,
        *,
        document_id: int,
        artifact_id: str,
    ) -> DocumentParseResult:
        content_hash = hashlib.sha256(pdf_bytes).hexdigest()
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception:
            return DocumentParseResult(
                parser_version=self.config.parser_version,
                status="corrupt",
                content_hash=content_hash,
                pages=(),
                tables=(),
                error="pdf_corrupt",
            )
        try:
            if document.needs_pass:
                return DocumentParseResult(
                    parser_version=self.config.parser_version,
                    status="password_protected",
                    content_hash=content_hash,
                    pages=(),
                    tables=(),
                    error="pdf_password_protected",
                )
            if document.page_count == 0:
                return DocumentParseResult(
                    parser_version=self.config.parser_version,
                    status="empty",
                    content_hash=content_hash,
                    pages=(),
                    tables=(),
                    error="pdf_empty",
                )
            pages = self._parse_pages(
                document,
                pdf_bytes=pdf_bytes,
                document_id=document_id,
                artifact_id=artifact_id,
            )
        finally:
            document.close()

        failed_page = next(
            (page for page in pages if page.status == "ocr_failed"),
            None,
        )
        status = "ocr_failed" if failed_page is not None else "parsed"
        error = failed_page.error if failed_page is not None else ""
        tables = (
            _extract_pdfplumber_tables(
                pdf_bytes,
                document_id=document_id,
                artifact_id=artifact_id,
                parser_version=self.config.parser_version,
            )
            if self.config.extract_tables
            else ()
        )
        return DocumentParseResult(
            parser_version=self.config.parser_version,
            status=status,
            content_hash=content_hash,
            pages=pages,
            tables=tables,
            error=error,
        )

    def _parse_pages(
        self,
        document: fitz.Document,
        *,
        pdf_bytes: bytes,
        document_id: int,
        artifact_id: str,
    ) -> tuple[DocumentPage, ...]:
        pages: list[DocumentPage] = []
        next_chunk_sequence = 0
        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            page_number = page_index + 1
            blocks = _extract_pymupdf_blocks(page)
            native_text = "\n".join(text for _, text in blocks)
            native_words = _extract_pymupdf_words(page)
            if (
                _text_character_count(native_text)
                < self.config.min_text_characters_per_page
            ):
                fallback_text = _extract_pypdf_page_text(
                    pdf_bytes,
                    page_index,
                )
                if (
                    _text_character_count(fallback_text)
                    >= self.config.min_text_characters_per_page
                ):
                    blocks = (
                        (
                            _rounded_bbox(tuple(page.rect)),
                            fallback_text,
                        ),
                    )
                    native_text = fallback_text
                    native_words = ()
                    section = "pypdf_fallback"
                else:
                    parsed_page, next_chunk_sequence = self._ocr_page(
                        page,
                        page_number=page_number,
                        document_id=document_id,
                        artifact_id=artifact_id,
                        next_chunk_sequence=next_chunk_sequence,
                        retained_text=native_text,
                    )
                    pages.append(parsed_page)
                    continue
            else:
                section = "body"

            chunks: list[DocumentChunk] = []
            for bbox, text in blocks:
                digest = _text_hash(text)
                chunks.append(
                    DocumentChunk(
                        chunk_id=_chunk_id(
                            document_id=document_id,
                            artifact_id=artifact_id,
                            parser_version=self.config.parser_version,
                            page_number=page_number,
                            sequence_no=next_chunk_sequence,
                            text_hash=digest,
                        ),
                        page_number=page_number,
                        sequence_no=next_chunk_sequence,
                        section=section,
                        bbox=bbox,
                        text=text,
                        text_hash=digest,
                        ocr_used=False,
                    )
                )
                next_chunk_sequence += 1
            words = tuple(
                DocumentWord(
                    page_number=page_number,
                    sequence_no=index,
                    bbox=bbox,
                    text=text,
                    confidence=None,
                    ocr_used=False,
                )
                for index, (bbox, text) in enumerate(native_words)
            )
            pages.append(
                DocumentPage(
                    page_number=page_number,
                    width=round(float(page.rect.width), 3),
                    height=round(float(page.rect.height), 3),
                    text=native_text,
                    chunks=tuple(chunks),
                    words=words,
                    ocr_used=False,
                    status="parsed",
                )
            )
        return tuple(pages)

    def _ocr_page(
        self,
        page: fitz.Page,
        *,
        page_number: int,
        document_id: int,
        artifact_id: str,
        next_chunk_sequence: int,
        retained_text: str,
    ) -> tuple[DocumentPage, int]:
        pixmap = page.get_pixmap(
            dpi=self.config.ocr_render_dpi,
            alpha=False,
        )
        request = OcrRequest(
            image_png=pixmap.tobytes("png"),
            page_number=page_number,
            dpi=self.config.ocr_render_dpi,
            languages=self.config.ocr_languages,
            pixel_width=pixmap.width,
            pixel_height=pixmap.height,
        )
        try:
            output = self.ocr_runner(request)
        except OcrUnavailableError as exc:
            error = str(exc) or "tesseract_unavailable"
            return (
                self._ocr_failed_page(
                    page,
                    page_number=page_number,
                    retained_text=retained_text,
                    error=error,
                ),
                next_chunk_sequence,
            )
        except Exception:
            return (
                self._ocr_failed_page(
                    page,
                    page_number=page_number,
                    retained_text=retained_text,
                    error="ocr_execution_failed",
                ),
                next_chunk_sequence,
            )
        text = _clean_text(output.text)
        if not text:
            return (
                self._ocr_failed_page(
                    page,
                    page_number=page_number,
                    retained_text=retained_text,
                    error="ocr_no_text",
                ),
                next_chunk_sequence,
            )

        scale_x = float(page.rect.width) / max(1, request.pixel_width)
        scale_y = float(page.rect.height) / max(1, request.pixel_height)
        words = tuple(
            DocumentWord(
                page_number=page_number,
                sequence_no=index,
                bbox=_rounded_bbox(
                    (
                        word.bbox[0] * scale_x,
                        word.bbox[1] * scale_y,
                        word.bbox[2] * scale_x,
                        word.bbox[3] * scale_y,
                    )
                ),
                text=_clean_text(word.text),
                confidence=word.confidence,
                ocr_used=True,
            )
            for index, word in enumerate(output.words)
            if _clean_text(word.text)
        )
        if words:
            bbox = (
                min(word.bbox[0] for word in words),
                min(word.bbox[1] for word in words),
                max(word.bbox[2] for word in words),
                max(word.bbox[3] for word in words),
            )
            confidence_values = [
                word.confidence
                for word in words
                if word.confidence is not None
            ]
            confidence = (
                sum(confidence_values) / len(confidence_values)
                if confidence_values
                else None
            )
        else:
            bbox = _rounded_bbox(tuple(page.rect))
            confidence = None
        digest = _text_hash(text)
        chunk = DocumentChunk(
            chunk_id=_chunk_id(
                document_id=document_id,
                artifact_id=artifact_id,
                parser_version=self.config.parser_version,
                page_number=page_number,
                sequence_no=next_chunk_sequence,
                text_hash=digest,
            ),
            page_number=page_number,
            sequence_no=next_chunk_sequence,
            section="ocr",
            bbox=_rounded_bbox(bbox),
            text=text,
            text_hash=digest,
            ocr_used=True,
            ocr_confidence=confidence,
        )
        return (
            DocumentPage(
                page_number=page_number,
                width=round(float(page.rect.width), 3),
                height=round(float(page.rect.height), 3),
                text=text,
                chunks=(chunk,),
                words=words,
                ocr_used=True,
                status="parsed",
            ),
            next_chunk_sequence + 1,
        )

    @staticmethod
    def _ocr_failed_page(
        page: fitz.Page,
        *,
        page_number: int,
        retained_text: str,
        error: str,
    ) -> DocumentPage:
        return DocumentPage(
            page_number=page_number,
            width=round(float(page.rect.width), 3),
            height=round(float(page.rect.height), 3),
            text=retained_text,
            chunks=(),
            words=(),
            ocr_used=True,
            status="ocr_failed",
            error=error,
        )


def parse_document(
    pdf_bytes: bytes,
    *,
    document_id: int,
    artifact_id: str,
    config: DocumentParserConfig | None = None,
    ocr_runner: OcrRunner | None = None,
) -> DocumentParseResult:
    return AnnouncementDocumentParser(
        config=config,
        ocr_runner=ocr_runner,
    ).parse(
        pdf_bytes,
        document_id=document_id,
        artifact_id=artifact_id,
    )
