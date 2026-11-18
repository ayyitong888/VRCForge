import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Thread

from fastapi.testclient import TestClient
from PIL import Image
import pypdfium2
import pytest

import dashboard_server as dashboard
from chat_attachment_vault import ChatAttachmentVault, bind_attachment_inspection_chat
from native_tool_images import collect_tool_images
from runtime_planner_service import EXPOSURE_LAYER_PLANNING, planner_read_output_evidence
from tests.test_native_runtime_gateway import call, finish as finish_native_turn, setup_gateway


def _text_pdf():
    words = b"VRCForge PDF manual. " * 12
    stream = b"BT /F1 9 Tf 15 100 Td (" + words + b") Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1400 120] /Resources "
        b"<< /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(data)
    data += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return data + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


@pytest.fixture
def mixed_pdf():
    scan = BytesIO()
    Image.new("RGB", (300, 160), "#651a98").save(scan, "PDF")
    result = BytesIO()
    with pypdfium2.PdfDocument(_text_pdf()) as text, pypdfium2.PdfDocument(scan.getvalue()) as raster:
        text.import_pages(raster, [0])
        text.save(result)
    return result.getvalue()


@contextmanager
def serve_pdf(data, content_type="application/pdf"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/manual"
        finally:
            server.shutdown()
            thread.join()


def test_authorized_local_pdf_returns_document_pages(tmp_path, mixed_pdf):
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(mixed_pdf)
    handler = dashboard.AGENT_GATEWAY._tools["vrcforge_read_text_file"].handler
    authorized = {"_generalAllowedRoots": [str(pdf)]}
    result = handler({"path": str(pdf), **authorized})
    assert result["source"] == str(pdf)
    assert "VRCForge PDF manual" in result["pages"][0]["text"]
    assert result["pages"][1]["needsVisual"] is True
    assert "content" not in result
    with pytest.raises(PermissionError):
        handler({"path": str(pdf), "_generalAllowedRoots": [str(tmp_path / "unrelated.pdf")]})
    result = handler({"path": str(pdf), "startPage": 2, "renderScannedPages": True, **authorized})
    [page] = result["pages"]
    [image] = result["content"]
    assert page["page"] == 2 and page["imageUri"] == image["uri"]
    carrier = collect_tool_images([{
        "tool": "vrcforge_read_text_file", "actionId": "pdf-2", "status": "executed", "result": result,
    }], resource_reader=None, project_path="")
    assert page["imageUri"] in carrier[0]["text"]
    inline = carrier[1]["image_url"]["url"]
    assert inline.startswith("data:image/png;base64,")
    with Image.open(BytesIO(base64.b64decode(inline.partition(",")[2], validate=True))) as rendered:
        actual_pixel = rendered.getpixel((rendered.width // 2, rendered.height // 2))[:3]
        assert actual_pixel == pytest.approx((101, 26, 153), abs=2)
    evidence = planner_read_output_evidence("vrcforge_read_text_file", result)
    assert evidence["authority"] == "untrusted_tool_output" and evidence["source"] == str(pdf)
    assert evidence["pages"] == [page]
    assert "content" not in evidence


@pytest.mark.parametrize("content_type", ["application/pdf", "application/octet-stream"])
def test_http_pdf_returns_pages(mixed_pdf, content_type):
    handler = dashboard.AGENT_GATEWAY._tools["vrcforge_web_fetch"].handler
    with serve_pdf(mixed_pdf, content_type) as url:
        result = handler({"url": url, "maxPages": 1})
        evidence = planner_read_output_evidence("vrcforge_web_fetch", result)
        assert evidence["nextRequest"]["arguments"] == {"url": url, "maxPages": 1, "startPage": 2}
        tail = handler({**evidence["nextRequest"]["arguments"], "renderScannedPages": True})
    assert result["source"] == url
    assert "VRCForge PDF manual" in result["pages"][0]["text"]
    assert tail["pages"][0]["page"] == 2
    assert tail["pages"][0]["needsVisual"] is True
    assert tail["pages"][0]["imageUri"] == tail["content"][0]["uri"]


def test_chat_pdf_uses_existing_vault_attachment_reference(tmp_path, monkeypatch, mixed_pdf):
    vault = ChatAttachmentVault(tmp_path / "vault")
    monkeypatch.setattr(dashboard, "chat_attachment_vault_store", lambda: vault)
    monkeypatch.setattr(dashboard, "_chat_attachment_upload_path", lambda upload_id: tmp_path / upload_id)
    with TestClient(dashboard.app) as client:
        begin = client.post("/api/app/chat-attachments/uploads", json={
            "name": "manual.pdf", "chatId": "pdf-chat", "declaredType": "application/pdf", "size": len(mixed_pdf),
        })
        assert begin.status_code == 200
        upload_id = begin.json()["uploadId"]
        appended = client.post(f"/api/app/chat-attachments/uploads/{upload_id}/chunks?offset=0",
                               content=mixed_pdf, headers={"Content-Type": "application/octet-stream"})
        assert appended.status_code == 200
        finish = client.post("/api/app/chat-attachments/uploads/finish", json={"uploadId": upload_id})
        assert finish.status_code == 200, finish.text
        attachment = finish.json()["attachment"]
    handler = dashboard.AGENT_GATEWAY._tools["vrcforge_inspect_chat_attachment"].handler
    with bind_attachment_inspection_chat(("pdf-chat",)):
        result = handler({"payloadHash": attachment["payloadHash"], "renderScannedPages": True})
    assert result["source"] == "manual.pdf"
    assert result["attachment"]["payloadHash"] == attachment["payloadHash"]
    assert "VRCForge PDF manual" in result["pages"][0]["text"]
    assert result["pages"][1]["needsVisual"] is True
    assert result["pages"][1]["imageUri"] == result["content"][0]["uri"]
    assert f"/{attachment['payloadHash']}/page/2" in result["content"][0]["uri"]
    with bind_attachment_inspection_chat(("pdf-chat",)):
        with pytest.raises(dashboard.AgentGatewayError, match="not found"):
            handler({"payloadHash": "f" * 64})

    name = "vrcforge_inspect_chat_attachment"
    gateway, model, _ = setup_gateway(tmp_path, [
        call("read-owned-pdf", name, {"payloadHash": attachment["payloadHash"], "maxPages": 1}),
        call("read-next-pdf-page", name, {"payloadHash": attachment["payloadHash"], "startPage": 2}),
        finish_native_turn,
    ])
    gateway.register_tool(name, "When to use: inspect a chat attachment. When NOT to use: read other files.",
                          "plan/preview", handler)
    monkeypatch.setattr(dashboard, "AGENT_GATEWAY", gateway)
    with TestClient(dashboard.app) as client:
        response = client.post("/api/app/agent/message", json={
            "message": "Read the uploaded PDF.", "chatId": "pdf-chat", "clientTurnId": "read-owned-pdf",
            "attachments": [attachment], "maxAgenticTurns": 4,
        })
    assert response.status_code == 200, response.text
    evidence = next(message for message in model.requests[1]["messages"] if message.get("tool_call_id") == "read-owned-pdf")
    assert "VRCForge PDF manual" in evidence["content"]
    tail = next(message for message in model.requests[2]["messages"] if message.get("tool_call_id") == "read-next-pdf-page")
    assert r'\"page\":2' in tail["content"].replace(" ", "")


def test_scoped_vault_lookup_preserves_shared_uploads(tmp_path, mixed_pdf):
    vault = ChatAttachmentVault(tmp_path / "vault")
    info = vault.ingest(data=mixed_pdf, name="manual.pdf", declared_type="application/pdf", chat_id="chat-alpha")
    payload_hash = info["payloadHash"]
    assert vault.resolve_for_inspection(payload_hash) is None
    with bind_attachment_inspection_chat(("chat-alpha",)):
        assert vault.resolve_for_inspection(payload_hash)["documentBytes"] == mixed_pdf
    vault.ingest(data=mixed_pdf, name="manual.pdf", declared_type="application/pdf", chat_id="chat-beta")
    with bind_attachment_inspection_chat(("chat-beta",)):
        assert vault.resolve_for_inspection(payload_hash)["documentBytes"] == mixed_pdf
    assert vault.resolve_for_inspection(payload_hash) is None


def test_invalid_pdfs_and_pdf_parameters_fail_explicitly(tmp_path, mixed_pdf):
    tools = dashboard.AGENT_GATEWAY._tools
    local = tools["vrcforge_read_text_file"].handler
    pdf = tmp_path / "corrupt.pdf"
    pdf.write_bytes(b"%PDF-1.7\nNot a valid document.")
    arguments = {"path": str(pdf), "_generalAllowedRoots": [str(pdf)]}
    with pytest.raises(ValueError, match="PDF could not be read"):
        local(arguments)
    with serve_pdf(b"not a PDF") as url:
        with pytest.raises(ValueError, match="Invalid PDF header"):
            tools["vrcforge_web_fetch"].handler({"url": url})
    pdf.write_bytes(mixed_pdf)
    with pytest.raises(ValueError, match="PDF page count"):
        local({**arguments, "startPage": 3})
    with pytest.raises(ValueError, match="byte read limit"):
        local({**arguments, "maxBytes": 128})
    with pytest.raises(ValueError, match="Use startPage"):
        local({**arguments, "startLine": 1})


def test_pdf_long_page_continues_with_original_physical_page(tmp_path, mixed_pdf, monkeypatch):
    import pdf_document_reader

    monkeypatch.setattr(pdf_document_reader, "_PDF_TEXT_BUDGET", 64)
    pdf = tmp_path / "manual.pdf"
    pdf.write_bytes(mixed_pdf)
    handler = dashboard.AGENT_GATEWAY._tools["vrcforge_read_text_file"].handler
    authorization = {"_generalAllowedRoots": [str(pdf)]}
    first = handler({"path": str(pdf), **authorization})
    assert first["pages"][0]["page"] == 1
    assert first["pages"][0]["textContinues"] is True
    args = first["nextRequest"]["arguments"]
    assert args["startPage"] == 1 and args["pageTextOffset"] == 64
    later = handler({**args, **authorization})
    assert later["pages"][0]["page"] == 1 and later["pages"][0]["textOffset"] == 64


@pytest.mark.parametrize("project_context_active", [False, True])
def test_pdf_options_are_available_in_both_planner_catalogs(project_context_active):
    catalog = dashboard._RuntimePlannerCatalog().read(EXPOSURE_LAYER_PLANNING,
                                                       project_context_active=project_context_active)
    exposed_tools = {tool.runtime_name: tool for tool in catalog.visible_tools}
    for name in ("vrcforge_read_text_file", "vrcforge_web_fetch", "vrcforge_inspect_chat_attachment"):
        assert {"startPage", "maxPages", "renderScannedPages"} <= exposed_tools[name].input_schema["properties"].keys()
