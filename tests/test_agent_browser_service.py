from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
import time

import pytest

from agent_browser_service import BrowserService, _browser_executable, _is_age_confirmation, _snapshot_text


def test_browser_snapshot_hides_editable_values_and_keeps_document_content() -> None:
    nodes = [
        {"nodeId": "1", "role": {"value": "RootWebArea"}, "name": {"value": "Author page"},
         "childIds": ["2", "3"]},
        {"nodeId": "2", "parentId": "1", "role": {"value": "textField"}, "name": {"value": "Password"},
         "value": {"value": "secret"}, "childIds": ["4"]},
        {"nodeId": "3", "parentId": "1", "role": {"value": "StaticText"},
         "name": {"value": "Use the PNG as a layer."}},
        {"nodeId": "4", "parentId": "2", "role": {"value": "StaticText"}, "name": {"value": "secret"}},
    ]
    assert _snapshot_text(nodes) == ("Author page\nUse the PNG as a layer.", False)


@pytest.mark.parametrize(("question", "expected"), [
    ("Are you 18 or older?", True),
    ("Are you at least 18 years old?", True),
    ("あなたは18歳以上ですか？", True),
    ("您是否已满18岁？", True),
    ("Are you 21 or older?", False),
    ("Are you over 18?", False),
    ("Are you not 18 or older?", False),
    ("あなたは20歳以上ですか？", False),
])
def test_browser_automatic_age_action_matches_exact_18_plus_scope(question: str, expected: bool) -> None:
    nodes = [
        {"nodeId": "1", "role": {"value": "dialog"}},
        {"nodeId": "2", "parentId": "1", "role": {"value": "heading"}, "name": {"value": question}},
        {"nodeId": "3", "parentId": "1", "role": {"value": "button"}, "name": {"value": "Yes"},
         "backendDOMNodeId": 3},
    ]
    assert _is_age_confirmation(nodes, nodes[-1]) is expected


@pytest.mark.parametrize("browser_name", ["edge", "chrome"])
def test_real_browser_same_tab_resume_and_teardown(browser_name: str) -> None:
    if _browser_executable(browser_name) is None:
        pytest.skip(f"Installed {browser_name} browser is required")

    class Fixture(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            content = b"<title>Fixture age gate</title><main><button>Continue</button></main>"
            if self.path == "/instructions":
                content = ("<title>Author instructions</title><main>"
                           + "<p>Use the PNG as a layer.</p>" * 400 + "</main>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args: object) -> None:
            pass

    service = BrowserService(headless=True)

    def execute(operation: str, *, session_id: str = "chat", client_turn_id: str = "first",
                parent_turn_id: str = "", **params: object) -> dict:
        return service.execute({"operation": operation, **params}, session_id=session_id,
                               client_turn_id=client_turn_id, parent_turn_id=parent_turn_id,
                               cancelled=lambda: False)

    with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            assert execute("open", url="file:///test", browser=browser_name)["ok"] is False
            opened = execute("open", url=url, browser=browser_name)
            assert opened["ok"], opened
            assert "Fixture age gate" in opened["snapshot"]
            browser = service._browsers["chat"]
            assert browser.process is not None and browser.profile is not None
            process, profile = browser.process, browser.profile.name
            assert execute("read", session_id="other-chat")["ok"] is False
            assert execute("read", client_turn_id="other-turn")["ok"] is False
            assert not service.can_resume("chat", "second", "wrong-parent")
            assert service.can_resume("chat", "second", "first")
            assert execute("open", client_turn_id="second", parent_turn_id="first", url=url)["ok"] is False

            # The connection must survive the TCP connect timeout while the tab is idle.
            time.sleep(2.2)
            # Simulate the user navigating the existing synthetic gate tab.
            service._tab_call(browser, "Page.navigate", lambda: False, params={"url": url + "/instructions"})
            result = execute("read", client_turn_id="second", parent_turn_id="first")
            assert result["ok"], result
            assert "Use the PNG as a layer." in result["snapshot"]
            assert "Fixture age gate" not in result["snapshot"]
            assert service._browsers["chat"].process is process
            assert execute("read", client_turn_id="third", parent_turn_id="first")["ok"] is False
            assert execute("read", client_turn_id="second", parent_turn_id="first", offset=result["nextOffset"])["ok"]
            assert execute("disconnect", client_turn_id="second", parent_turn_id="first")["ok"]
            assert process.poll() is not None
            assert not service.can_resume("chat", "third", "second")
            assert not Path(profile).exists()
        finally:
            service.close()
            server.shutdown()
            thread.join()


def test_real_browser_age_action_cannot_authorize_cookies_or_replay_target() -> None:
    if _browser_executable("") is None:
        pytest.skip("Installed Edge or Chrome browser is required")

    class Fixture(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            question = "Are you 18 or older?" if self.path.startswith("/english") else "あなたは18歳以上ですか？"
            reveal = "this.closest('section').innerHTML='<p>Author instructions</p>'"
            choices = (f'<button onclick="{reveal}">Yes, I am 18 or older</button><button>No</button>'
                       if self.path.startswith("/english") else
                       f'<nav><ul><li><a href="#verified" onclick="event.preventDefault();{reveal}">[はい]</a></li>'
                       '<li><a href="#declined">[いいえ]</a></li></ul></nav>')
            content = f'''<title>Page verification</title><main>
              <section aria-label="Age gate"><h1>{question}</h1>
                {choices}
              </section>
              <aside><p>Allow optional cookies?</p>
                <button onclick="this.closest('aside').innerHTML='<p>Cookies allowed</p>'">Yes</button>
              </aside></main>'''.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_args: object) -> None:
            pass

    def ref(snapshot: str, label: str) -> str:
        match = re.search(r"^\[(?:button|link) (b[a-f0-9]+-\d+)\] " + re.escape(label) + "$", snapshot, re.MULTILINE)
        assert match, snapshot
        return match.group(1)

    service = BrowserService(headless=True)

    def execute(operation: str, *, turn: str = "first", parent: str = "", adult: bool = False,
                authority: str = "", **params: object) -> dict:
        return service.execute({"operation": operation, **params}, session_id="chat", client_turn_id=turn,
                               parent_turn_id=parent, adult_confirmed=adult, click_authority=authority,
                               cancelled=lambda: False)

    with ThreadingHTTPServer(("127.0.0.1", 0), Fixture) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            opened = execute("open", url=origin + "/english?token=fixture")
            assert opened["ok"], opened
            age = ref(opened["snapshot"], "Yes, I am 18 or older")
            denied = execute("click", target=age, adult=True, click_authority="approved")
            assert denied["code"] == "browser_click_confirmation_required"
            assert denied["site"] == origin
            assert execute("disconnect")["ok"]

            opened = execute("open", url=origin + "/japanese", adult=True)
            assert opened["ok"], opened
            age = ref(opened["snapshot"], "[はい]")
            cookie = ref(opened["snapshot"], "Yes")
            assert execute("click", target=cookie)["code"] == "browser_click_confirmation_required"
            clicked = execute("click", target=age)
            assert clicked["ok"], clicked
            assert "Author instructions" in clicked["snapshot"]
            assert "Cookies allowed" not in clicked["snapshot"]
            assert execute("click", target=age, authority="approved")["code"] == "browser_click_stale_target"
            cookie = ref(clicked["snapshot"], "Yes")
            assert execute("click", target=cookie)["code"] == "browser_click_confirmation_required"
            confirmed = execute("click", target=cookie, turn="confirmed", parent="first", authority="approved")
            assert confirmed["ok"], confirmed
            assert "Cookies allowed" in confirmed["snapshot"]
            assert execute("click", target=cookie, turn="confirmed", parent="first",
                           authority="approved")["code"] == "browser_click_stale_target"
        finally:
            service.close()
            server.shutdown()
            thread.join()
