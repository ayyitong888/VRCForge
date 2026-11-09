import asyncio
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import pytest

import dashboard_server as dashboard
from agent_browser_service import BrowserService
from agent_gateway import AgentGateway, AgentGatewayError
from dashboard_api_models import AgentRuntimeMessageRequest
from runtime_planner_service import RuntimePlannerService
from tests.test_native_runtime_gateway import NativeModel, NativeTurn, call, runtime_state


@pytest.mark.skipif(os.name != "nt", reason="Windows Edge/Chrome acceptance")
@pytest.mark.parametrize("decision", ["manual", "age", "natural_age", "approve", "deny", "deny_open", "stop_open"])
def test_native_browser_turn_honors_user_click_decision(tmp_path, monkeypatch, decision):
    acknowledged = Event()
    rendered = Event()
    visited = Event()
    completed = Event()
    continuations = []

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/state":
                body = b"ready" if acknowledged.is_set() else b"waiting"
            elif self.path == "/":
                visited.set()
                body = b"""<!doctype html><title>Reading fixture</title><main><h1>Verification needed</h1></main>
                <script>const timer = setInterval(async () => {
                  if (await (await fetch('/state')).text() !== 'ready') return;
                  clearInterval(timer);
                  document.querySelector('main').innerHTML = '<h1>Author instructions</h1><p>Readback token lavender-934</p>';
                  await fetch('/rendered', {method: 'POST'});
                }, 50);</script>"""
                if decision != "manual":
                    gate = "Are you 18 or older?" if decision in ("age", "natural_age") else "Cookie settings"
                    label = "Yes" if decision in ("age", "natural_age") else "Accept all cookies"
                    body = f"""<!doctype html><title>Reading fixture</title><main><h1>{gate}</h1>
                    <button onclick="document.querySelector('main').innerHTML='<p>Readback token lavender-934</p>';fetch('/rendered',{{method:'POST'}})">{label}</button>
                    </main>""".encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self.send_response(204)
            self.end_headers()
            if self.path == "/rendered":
                rendered.set()

        def log_message(self, *_args):
            pass

    def on_complete(result):
        continuations.append(result)
        completed.set()

    gateway = AgentGateway(tmp_path / "gateway.json", tmp_path / "audit", runtime_turn_completed=on_complete)
    config = gateway.ensure_config()
    config.enabled = True
    gateway.save_config(config)
    gateway.desktop.browser = BrowserService(headless=True)
    monkeypatch.setattr(dashboard, "AGENT_GATEWAY", gateway)
    dashboard.register_agent_gateway_tools()
    monkeypatch.setattr(dashboard, "_RUNTIME_PROFILED_TOOL_REGISTRY", dashboard._build_runtime_profiled_tool_registry())

    catalog = dashboard._RuntimePlannerCatalog()
    assert not any(tool.runtime_name == "vrcforge_browser" for tool in catalog.read("execution").routable_tools)
    with pytest.raises(AgentGatewayError, match="unavailable"):
        gateway.call_tool("vrcforge_browser", {"operation": "read"})

    with ThreadingHTTPServer(("127.0.0.1", 0), Page) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/"

            def request_user(request):
                assert any("Verification needed" in row.get("content", "") for row in request["messages"] if row["role"] == "tool")
                return call("ask", "ask_user", {"question": "Complete the verification in the open browser tab, then tell me."})

            def read_current_tab(request):
                assert "browser" in runtime_state(request)["availableToolNames"]
                assert "agent_desktop_action" not in runtime_state(request)["availableToolNames"]
                return call("read", "browser", {"operation": "read"})

            target = ""

            def click_from_snapshot(request):
                nonlocal target
                open_call = "open-approved" if decision == "natural_age" else "open"
                receipt = next(row["content"] for row in request["messages"] if row.get("tool_call_id") == open_call)
                label = "Yes" if decision in ("age", "natural_age") else "Accept all cookies"
                target = re.search(r"\[button (b[^\]]+)\] " + label, receipt)[1]
                return call("click", "browser", {"operation": "click", "target": target})

            def repeat_exact_click(request):
                receipt = next(row["content"] for row in request["messages"] if row.get("tool_call_id") == "click")
                if decision in ("approve", "natural_age"):
                    assert f"authorized only browser click target {target}" in receipt
                else:
                    assert "did not authorize the click" in receipt
                return call("read", "browser", {"operation": "click", "target": target})

            def reply_from_receipt(request):
                call_id = "click" if decision == "age" else "read"
                receipt = next(row["content"] for row in request["messages"] if row.get("tool_call_id") == call_id)
                if decision == "deny":
                    assert "did not approve" in receipt
                    return {"role": "assistant", "content": "Click denied."}
                assert "untrusted_tool_output" in receipt and "Readback token lavender-934" in receipt
                return {"role": "assistant", "content": "The author instructions say Readback token lavender-934."}

            actions = {
                "manual": [request_user, read_current_tab],
                "age": [click_from_snapshot],
                "natural_age": [click_from_snapshot, repeat_exact_click],
                "approve": [click_from_snapshot, repeat_exact_click],
                "deny": [click_from_snapshot, repeat_exact_click],
            }
            natural = decision in ("natural_age", "deny_open", "stop_open")

            def repeat_open(request):
                receipt = next(row["content"] for row in request["messages"] if row.get("tool_call_id") == "open")
                assert ("authorized only this browser open request" if decision == "natural_age" else "did not allow opening the page") in receipt
                return call("open-approved", "browser", {"operation": "open", "url": url})

            def reply_after_denial(request):
                receipt = next(row["content"] for row in request["messages"] if row.get("tool_call_id") == "open-approved")
                assert "did not allow opening this page" in receipt
                return {"role": "assistant", "content": "Site access denied."}

            model = NativeModel([
                call("load", "load_internal_tool_block", {"block": "research/web_research", "tools": ["browser"]}),
                call("open", "browser", {"operation": "open", "url": url}),
                *([repeat_open] if natural else []),
                *actions.get(decision, []),
                reply_after_denial if decision in ("deny_open", "stop_open") else reply_from_receipt,
            ])
            gateway.bind_runtime_planner(RuntimePlannerService(
                catalog=catalog, desktop=dashboard._RuntimePlannerDesktopObservation(), model=model, turn=NativeTurn(),
            ))
            request = AgentRuntimeMessageRequest.model_validate({
                "message": (f"Open {url} and read the author instructions."
                            if natural else "/browser " + ("--18-plus " if decision != "manual" else "")
                            + "Read the author instructions from the test page."),
                "session_id": "browser-session", "clientTurnId": "browser-turn",
            })
            initial = asyncio.run(dashboard.app_agent_runtime_message(request))
            if decision == "age":
                assert "Readback token lavender-934" in initial["plan"]["reply"]
                assert gateway.questions.list(session_id="browser-session")["questions"] == []
                return
            assert initial["plan"]["nextStep"] == "needs_user_action", initial["plan"]
            question = gateway.questions.list(session_id="browser-session")["questions"][0]
            if natural:
                assert question["header"] == "Open a website"
                assert url.rstrip("/") in question["question"]
                assert not visited.is_set()
                if decision == "stop_open":
                    gateway.request_runtime_cancel({"sessionId": "browser-session", "clientTurnId": "browser-turn"})
                    assert not gateway.questions.list(session_id="browser-session")["questions"]
                    assert not visited.is_set()
                    return
                gateway.questions.answer(question["questionId"], {
                    "sessionId": "browser-session", "selectedOptionId": "deny" if decision == "deny_open" else "approve",
                })
                assert completed.wait(10), "The site decision did not resume the browser task"
                if decision == "deny_open":
                    assert "Site access denied." in continuations[0]["plan"]["reply"]
                    assert not visited.is_set()
                    return
                assert visited.is_set() and not rendered.is_set()
                completed.clear()
                continuations.clear()
                question = gateway.questions.list(session_id="browser-session")["questions"][0]
                assert question["header"] == "Confirm your age"
                assert "I am 18 or older" in question["options"][0]["label"]
            if decision == "manual":
                acknowledged.set()
                assert rendered.wait(8), "The original browser tab did not render the confirmed page"
                answer = {"answer": "Completed in the open tab."}
            elif decision != "natural_age":
                assert "Accept all cookies" in question["question"]
                assert not rendered.is_set(), "Adult attestation cannot authorize a cookie click"
                answer = {"selectedOptionId": decision}
            else:
                answer = {"selectedOptionId": "approve"}
            gateway.questions.answer(question["questionId"], {"sessionId": "browser-session", **answer})
            assert completed.wait(10), "The user answer did not resume the browser task"
            expected = "Click denied." if decision == "deny" else "Readback token lavender-934"
            assert expected in continuations[0]["plan"]["reply"]
            assert rendered.is_set() is (decision != "deny")
            assert not gateway.desktop.browser.can_resume("browser-session", "another-turn", continuations[0]["clientTurnId"])
        finally:
            gateway.shutdown_runtime_continuations(3)
            gateway.desktop.browser.close()
            server.shutdown()
            worker.join(timeout=3)
