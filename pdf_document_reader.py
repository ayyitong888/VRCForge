"""Read PDF text and optional page previews for the General Agent."""

from __future__ import annotations

import hashlib
from contextlib import closing
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any

PDF_MAGIC = b"%PDF-"
PDF_MAX_BYTES = 10 * 1024 * 1024
PDF_READ_PROPERTIES = {
    "startPage": {"type": "integer", "minimum": 1, "description": "First physical PDF page (1-based)."},
    "maxPages": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Maximum PDF pages in this read, default 4."},
    "pageTextOffset": {"type": "integer", "minimum": 0, "description": "Copy from nextRequest to continue a long PDF page."},
    "renderScannedPages": {"type": "boolean", "description": "Return image previews for selected PDF pages with little or no extractable text."},
}

# PDFium does not allow concurrent API calls, even on different documents.
_PDFIUM_LOCK = Lock()
_PDF_TEXT_BUDGET = 32_000
_PDF_VISUAL_TEXT_THRESHOLD = 200
_PDF_VISUAL_PAGE_LIMIT = 4


def pdf_options(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "start_page": params.get("startPage", 1),
        "max_pages": params.get("maxPages", 4),
        "page_text_offset": params.get("pageTextOffset", 0),
        "render_scanned_pages": params.get("renderScannedPages", False),
    }


def set_pdf_next_request(result: dict[str, Any], tool: str, params: dict[str, Any]) -> None:
    if result.get("hasMore") is not True:
        return
    arguments = {key: params[key] for key in (
        "path", "projectPath", "url", "payloadHash", "timeout", "maxBytes", "maxPages", "renderScannedPages",
    ) if key in params}
    if "max_bytes" in params:
        arguments.setdefault("maxBytes", params["max_bytes"])
    arguments["startPage"] = result["nextPage"]
    if "nextPageTextOffset" in result:
        arguments["pageTextOffset"] = result["nextPageTextOffset"]
    result["nextRequest"] = {"tool": tool, "arguments": arguments}


def read_pdf_path(path: Path, *, source: str, max_bytes: int = PDF_MAX_BYTES, **options: Any) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"PDF exceeds the {max_bytes}-byte read limit.")
    return read_pdf_document(data, source=source, **options)


def read_pdf_document(
    data: bytes,
    *,
    source: str,
    start_page: int = 1,
    max_pages: int = 4,
    page_text_offset: int = 0,
    render_scanned_pages: bool = False,
    image_source_id: str = "",
) -> dict[str, Any]:
    if not data.startswith(PDF_MAGIC):
        raise ValueError("Invalid PDF header.")
    if type(start_page) is not int or start_page < 1:
        raise ValueError("startPage must be a positive integer.")
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ValueError("maxPages must be an integer between 1 and 20.")
    if type(page_text_offset) is not int or page_text_offset < 0:
        raise ValueError("pageTextOffset must be a nonnegative integer.")
    if type(render_scanned_pages) is not bool:
        raise ValueError("renderScannedPages must be a boolean.")

    import pypdfium2 as pdfium

    pages: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    remaining_chars = _PDF_TEXT_BUDGET
    next_page, next_offset = start_page, 0
    if render_scanned_pages and not image_source_id:
        image_source_id = hashlib.sha256(source.encode("utf-8")).hexdigest()
    try:
        with _PDFIUM_LOCK, pdfium.PdfDocument(data) as document:
            page_count = len(document)
            if start_page > page_count:
                raise ValueError(f"startPage exceeds the PDF page count ({page_count}).")
            for number in range(start_page, min(page_count + 1, start_page + max_pages)):
                offset = page_text_offset if number == start_page else 0
                with closing(document[number - 1]) as page, closing(page.get_textpage()) as text_page:
                    text = text_page.get_text_bounded().replace("\r\n", "\n")
                    if offset > len(text):
                        raise ValueError(f"pageTextOffset exceeds the text on PDF page {number}.")
                    needs_visual = len(text.strip()) < _PDF_VISUAL_TEXT_THRESHOLD
                    if needs_visual and render_scanned_pages and len(images) == _PDF_VISUAL_PAGE_LIMIT:
                        break
                    end = min(len(text), offset + remaining_chars)
                    row = {"page": number, "text": text[offset:end], "needsVisual": needs_visual}
                    if offset:
                        row["textOffset"] = offset
                    if needs_visual and render_scanned_pages:
                        try:
                            image = _render_page(page)
                        except (pdfium.PdfiumError, ValueError, OSError):
                            row["visualUnavailable"] = True
                        else:
                            # Keep page identity in the existing tool-image provenance.
                            image["uri"] = f"vrcforge://pdf/{image_source_id}/page/{number}"
                            row["imageUri"] = image["uri"]
                            images.append(image)
                    pages.append(row)
                    remaining_chars -= end - offset
                    next_page = number + 1
                    if end < len(text):
                        row["textContinues"] = True
                        next_page, next_offset = number, end
                        break
                    if remaining_chars == 0:
                        break
    except pdfium.PdfiumError as exc:
        raise ValueError(f"PDF could not be read: {exc}") from exc

    has_more = next_page <= page_count
    result: dict[str, Any] = {
        "ok": True,
        "documentType": "pdf",
        "source": source,
        "bytes": len(data),
        "pageCount": page_count,
        "pages": pages,
        "hasMore": has_more,
        "truncated": has_more,
        "summary": f"Read PDF pages {pages[0]['page']}–{pages[-1]['page']} of {page_count} from {source}.",
    }
    if has_more:
        result["nextPage"] = next_page
        if next_offset:
            result["nextPageTextOffset"] = next_offset
    if images:
        result["content"] = images
    return result


def _render_page(page: Any) -> dict[str, Any]:
    from local_image_reader import make_image_content

    width, height = page.get_size()
    if min(width, height) <= 0:
        raise ValueError("Invalid PDF page size.")
    with closing(page.render(scale=min(2.0, 1024 / max(width, height)))) as bitmap:
        with bitmap.to_pil() as rendered:
            buffer = BytesIO()
            rendered.save(buffer, format="PNG")
    image, _, _, _ = make_image_content(buffer.getvalue(), 1024)
    return image
