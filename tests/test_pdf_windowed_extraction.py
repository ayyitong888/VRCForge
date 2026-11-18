from contextlib import closing
from io import BytesIO

import pypdfium2

import pdf_document_reader as reader
from tests.test_general_pdf_reader import _text_pdf


def test_pdf_continuation_reads_a_bounded_character_window(monkeypatch):
    data = _text_pdf(repeat=45)
    with pypdfium2.PdfDocument(data) as document:
        with closing(document[0]) as page, closing(page.get_textpage()) as text_page:
            expected = text_page.get_text_bounded()

    def whole_page_read(*args, **kwargs):
        raise AssertionError("Whole-page extraction bypasses the text budget")

    monkeypatch.setattr(pypdfium2.PdfTextPage, "get_text_bounded", whole_page_read)
    raw_get_unicode = pypdfium2.raw.FPDFText_GetUnicode
    calls = []

    def get_unicode(text_page, index):
        calls.append(index)
        return raw_get_unicode(text_page, index)

    monkeypatch.setattr(pypdfium2.raw, "FPDFText_GetUnicode", get_unicode)
    monkeypatch.setattr(reader, "_PDF_TEXT_BUDGET", 64)
    offset = 0
    pieces = []
    while True:
        calls.clear()
        result = reader.read_pdf_document(data, source="manual.pdf", page_text_offset=offset)
        [page] = result["pages"]
        assert len(page["text"]) <= 64
        assert 0 < len(calls) <= 464
        pieces.append(page["text"])
        if not result["hasMore"]:
            break
        assert result["nextPage"] == 1
        offset = result["nextPageTextOffset"]

    assert "".join(pieces) == expected


def test_window_preserves_split_newline_and_non_bmp_unicode():
    units = [ord("A"), 13, 10, 0xD83D, 0xDE00, 0x20000, ord("Z")]
    read_window = reader._read_text_window
    get_unicode = list.__getitem__

    visible = lambda index: True
    first, offset = read_window(units, get_unicode, 0, len(units), 2, visible)
    second, offset = read_window(units, get_unicode, offset, len(units), 1, visible)
    tail, offset = read_window(units, get_unicode, offset, len(units), 2, visible)

    assert first + second + tail == "A\n\U0001f600\U00020000Z"
    assert offset == len(units)


def test_window_keeps_text_inside_the_visible_page():
    output = BytesIO()
    with pypdfium2.PdfDocument(_text_pdf()) as document:
        with closing(document[0]) as page:
            page.set_cropbox(0, 0, 105, 120)
        document.save(output)

    [page] = reader.read_pdf_document(output.getvalue(), source="cropped.pdf")["pages"]
    assert page["text"].split() == ["VRCForge", "PDF", "manua"]
