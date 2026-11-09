"""An app-owned Edge/Chrome tab, connected over local Chromium DevTools Protocol."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from websockets.sync.client import ClientConnection, connect


LOGGER = logging.getLogger(__name__)
_PAGE_CHARS = 8_000
_SNAPSHOT_CHARS = 96_000
_MAX_BROWSERS = 4
_BROWSER_FILES = {
    "edge": Path("Microsoft/Edge/Application/msedge.exe"),
    "chrome": Path("Google/Chrome/Application/chrome.exe"),
}
_CONTROL_ROLES = {"button", "checkbox", "combobox", "heading", "link", "radio", "switch", "tab"}
_CLICK_ROLES = {"button", "link"}
_EDITABLE_ROLES = {"searchbox", "textfield", "textfieldwithcombobox", "textbox", "textarea", "passwordtext"}
_SNAPSHOT_ROLES = _CONTROL_ROLES | {"rootwebarea", "statictext", "image"}
_AGE_THRESHOLD = r"(?<!\d)18(?!\d)"
_AGE_QUESTION = re.compile(
    rf"\bare you\s+(?:currently\s+)?(?:at least\s+{_AGE_THRESHOLD}\b|"
    rf"{_AGE_THRESHOLD}\s*(?:\+|(?:years?(?: old| of age)?\s+)?(?:or|and)\s+(?:older|over|above)))"
    rf"|{_AGE_THRESHOLD}\s*(?:歳|才)\s*以上(?:で(?:す|しょう)か|\s*\?)"
    rf"|(?:是否|已[满滿]|年[满滿]|至少|不低于)\s*(?:已[满滿]|年[满滿])?\s*{_AGE_THRESHOLD}\s*(?:周?[岁歲])?\s*(?:[吗嗎?]|以上)"
)
_AGE_POSITIVE = re.compile(
    rf"(?:yes,?\s+)?i am\s+(?:at least\s+{_AGE_THRESHOLD}|"
    rf"{_AGE_THRESHOLD}\s*(?:\+|(?:years?(?: old| of age)?\s+)?(?:or|and)\s+(?:older|over|above)))"
    rf"|(?:はい[、, ]*)?{_AGE_THRESHOLD}\s*(?:歳|才)\s*以上です"
    rf"|(?:我|确认|確認)?(?:已[满滿]|年[满滿]|已年[满滿])\s*{_AGE_THRESHOLD}\s*周?[岁歲]"
)
_OTHER_CONSENT = re.compile(r"\bcookies?\b|\bprivacy\b|\boauth\b|authoriz|log[ -]?in|sign[ -]?in|"
                           r"隐私|隱私|授权|授權|登录|登入|クッキー|プライバシー|ログイン|アカウント認証")
_PAGE_CONTAINER_ROLES = {"alertdialog", "dialog", "form", "main", "group", "region"}
_UNRELATED_CONTAINER_ROLES = {"banner", "complementary", "contentinfo"}
_YES_LABELS = {"yes", "はい", "是", "是的"}
_NO_LABELS = {"no", "いいえ", "否", "不是"}
_CLICK_ELEMENT = """function(label, ageOnly) {
    if (!this.isConnected || !(this instanceof HTMLElement) || this.matches(':disabled') ||
        this.closest('[inert],[aria-hidden="true"],[aria-disabled="true"]')) return false;
    const normalize = value => String(value || '').trim().replace(/\\s+/g, ' ');
    const ids = normalize(this.getAttribute('aria-labelledby')).split(' ');
    const labelled = ids.map(id => this.ownerDocument.getElementById(id)?.textContent || '').join(' ');
    const inputLabel = this.matches('input[type=button],input[type=submit]') ? this.value : '';
    const names = [this.getAttribute('aria-label'), labelled, this.innerText, inputLabel, this.title];
    if (!names.some(name => normalize(name) === label)) return false;
    if (ageOnly) {
        const destination = [this.getAttribute('href'), this.getAttribute('formaction'),
                             this.closest('form')?.getAttribute('action')].join(' ');
        if (/cookie|privacy|oauth|authoriz|login|sign.?in|javascript:/i.test(destination)) return false;
    }
    this.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
    const rect = this.getBoundingClientRect();
    const visible = this.ownerDocument.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    if (!rect.width || !rect.height || !visible || !this.contains(visible)) return false;
    HTMLElement.prototype.click.call(this);
    return true;
}"""


def _browser_executable(preferred: str) -> tuple[str, Path] | None:
    roots = [Path(value) for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")
             if (value := os.environ.get(key)) and Path(value).is_absolute()]
    for name in (preferred,) if preferred else _BROWSER_FILES:
        for root in roots:
            executable = root / _BROWSER_FILES[name]
            if executable.is_file():
                return name, executable
    return None


def _http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return False
    if any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        return False
    try:
        url = urlsplit(value)
        return bool(url.scheme in {"http", "https"} and url.hostname and url.username is None
                    and url.password is None and (url.port is None or url.port > 0))
    except ValueError:
        return False


class _BrowserError(Exception):
    pass


class _Cancelled(_BrowserError):
    pass


class _StaleTarget(_BrowserError):
    pass


def _check_cancelled(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise _Cancelled("Browser operation cancelled.")


class _CDP:
    def __init__(self, connection: ClientConnection) -> None:
        self.connection = connection
        self.sequence = 0

    def call(self, method: str, cancelled: Callable[[], bool], *, params: dict[str, Any] | None = None,
             session: str = "", timeout: float = 8.0) -> dict[str, Any]:
        _check_cancelled(cancelled)
        self.sequence += 1
        payload: dict[str, Any] = {"id": self.sequence, "method": method, "params": params or {}}
        if session:
            payload["sessionId"] = session
        self.connection.send(json.dumps(payload))
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            _check_cancelled(cancelled)
            try:
                response = json.loads(self.connection.recv(timeout=min(remaining, 0.2)))
            except TimeoutError:
                continue
            if response.get("id") == self.sequence:
                if "error" in response:
                    raise _BrowserError(f"Browser rejected {method}.")
                return response.get("result", {})
        raise _BrowserError(f"Browser timed out during {method}.")


@dataclass
class _Target:
    backend_id: int
    role: str
    label: str


@dataclass
class _Browser:
    client_turn_id: str
    adult_confirmed: bool = False
    parent_turn_id: str = ""
    last_used: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancelled: threading.Event = field(default_factory=threading.Event)
    profile: tempfile.TemporaryDirectory[str] | None = None
    process: subprocess.Popen[bytes] | None = None
    cdp: _CDP | None = None
    session: str = ""
    snapshot: str = ""
    snapshot_page: tuple[str, str] | None = None
    snapshot_truncated: bool = False
    targets: dict[str, _Target] = field(default_factory=dict)
    age_click_sites: set[str] = field(default_factory=set)


def _node_role(node: dict[str, Any]) -> str:
    return str(node.get("role", {}).get("value", "")).lower()


def _node_label(node: dict[str, Any]) -> str:
    return " ".join(str(node.get("name", {}).get("value", "")).split())


def _normalized_label(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip(" .!。！")


def _node_target(node: dict[str, Any]) -> _Target | None:
    backend_id, role, label = node.get("backendDOMNodeId"), _node_role(node), _node_label(node)
    if (type(backend_id) is int and backend_id > 0 and role in _CLICK_ROLES and 0 < len(label) <= 300
            and not node.get("ignored") and not any(prop.get("name") == "disabled"
                and prop.get("value", {}).get("value") is True for prop in node.get("properties", []))):
        return _Target(backend_id, role, label)
    return None


def _snapshot_text(nodes: list[dict[str, Any]], targets: dict[str, _Target] | None = None) -> tuple[str, bool]:
    by_id = {node["nodeId"]: node for node in nodes if isinstance(node.get("nodeId"), str)}
    stack = [node for node in reversed(nodes) if not node.get("parentId")]
    lines: list[str] = []
    seen: set[str] = set()
    size = 0
    nonce = secrets.token_hex(4) if targets is not None else ""
    while stack:
        node = stack.pop()
        node_id = node.get("nodeId")
        if node_id in seen:
            continue
        seen.add(node_id)
        role = _node_role(node)
        if role in _EDITABLE_ROLES or any(
            prop.get("name") == "editable" for prop in node.get("properties", [])
        ):
            continue
        stack.extend(by_id[child] for child in reversed(node.get("childIds", [])) if child in by_id)
        if node.get("ignored") or role not in _SNAPSHOT_ROLES:
            continue
        name = _node_label(node)
        if not name:
            continue
        line = f"[{role}] {name}" if role in _CONTROL_ROLES else name
        target = _node_target(node) if targets is not None else None
        if target is not None and targets is not None:
            ref = f"b{nonce}-{len(targets) + 1}"
            line = f"[{role} {ref}] {name}"
        else:
            ref = ""
        available = _SNAPSHOT_CHARS - size
        if len(line) + 1 > available:
            lines.append(line[:available])
            return "\n".join(lines), True
        lines.append(line)
        if target is not None and targets is not None:
            targets[ref] = target
        size += len(line) + 1
    return "\n".join(lines), False


def _site_origin(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")


def _is_age_confirmation(nodes: list[dict[str, Any]], target_node: dict[str, Any]) -> bool:
    def choice(node: dict[str, Any]) -> str:
        label = _normalized_label(_node_label(node))
        if (label.startswith("[") and label.endswith("]")) or (label.startswith("【") and label.endswith("】")):
            return label[1:-1].strip()
        return label

    def positive(node: dict[str, Any]) -> bool:
        label = choice(node)
        return label in _YES_LABELS or _AGE_POSITIVE.fullmatch(label) is not None

    if not positive(target_node):
        return False
    by_id = {node["nodeId"]: node for node in nodes if isinstance(node.get("nodeId"), str)}

    def ancestors(node: dict[str, Any]) -> list[str]:
        lineage = []
        for _ in range(6):
            parent = node.get("parentId")
            if parent not in by_id or parent in lineage:
                break
            lineage.append(parent)
            node = by_id[parent]
        return lineage

    target_parents = ancestors(target_node)
    if any(_node_role(by_id[parent]) in _UNRELATED_CONTAINER_ROLES for parent in target_parents):
        return False
    for navigation in (parent for parent in target_parents if _node_role(by_id[parent]) == "navigation"):
        # Some gates present their two choices as a link list. A general
        # navigation menu must not gain authority from nearby page text.
        choices = [item for item in nodes if _node_target(item) is not None and navigation in ancestors(item)]
        if len(choices) != 2 or not any(item is not target_node and choice(item) in _NO_LABELS for item in choices):
            return False
    for node in nodes:
        if (node.get("ignored") or _node_role(node) not in {"heading", "statictext"}
                or not _AGE_QUESTION.search(_normalized_label(_node_label(node)))):
            continue
        question_parents = ancestors(node)
        for depth, shared in enumerate(target_parents):
            if shared not in question_parents or depth + question_parents.index(shared) > 5:
                continue
            scope = by_id[shared]
            role = _node_role(scope)
            outer_role = _node_role(by_id.get(scope.get("parentId"), {}))
            if role == "rootwebarea" or (outer_role == "rootwebarea" and role not in _PAGE_CONTAINER_ROLES):
                break
            if any(not item.get("ignored") and shared in ancestors(item)
                   and (_OTHER_CONSENT.search(_normalized_label(_node_label(item)))
                        or (item is not target_node and _node_target(item) is not None and positive(item)))
                   for item in nodes):
                break
            return True
    return False


class BrowserService:
    """Own browser lifetimes and authorize the exact turn or its trusted continuation."""

    def __init__(self, *, headless: bool = False, idle_seconds: float = 900.0) -> None:
        self._headless = headless
        self._idle_seconds = idle_seconds
        self._lock = threading.Lock()
        self._browsers: dict[str, _Browser] = {}
        self._closed = threading.Event()
        self._janitor: threading.Thread | None = None

    @staticmethod
    def open_site(params: dict[str, Any]) -> str:
        url = params.get("url")
        return _site_origin(url) if _http_url(url) and params.get("browser", "") in ("", "edge", "chrome") else ""

    @staticmethod
    def _allows(browser: _Browser, client_turn_id: str, parent_turn_id: str) -> bool:
        if browser.client_turn_id == client_turn_id:
            return not parent_turn_id or browser.parent_turn_id == parent_turn_id
        return bool(parent_turn_id and browser.client_turn_id == parent_turn_id)

    def has_tab(self, session_id: str, client_turn_id: str) -> bool:
        with self._lock:
            browser = self._browsers.get(session_id)
            return browser is not None and browser.client_turn_id == client_turn_id

    def can_resume(self, session_id: str, client_turn_id: str, parent_turn_id: str) -> bool:
        if not session_id or not client_turn_id or not parent_turn_id:
            return False
        with self._lock:
            browser = self._browsers.get(session_id)
            return bool(browser and browser.session and not browser.cancelled.is_set()
                        and time.monotonic() - browser.last_used < self._idle_seconds
                        and browser.process is not None and browser.process.poll() is None
                        and self._allows(browser, client_turn_id, parent_turn_id))

    def execute(self, params: dict[str, Any], *, session_id: str, client_turn_id: str,
                parent_turn_id: str = "", cancelled: Callable[[], bool], adult_confirmed: bool = False,
                click_authority: str = "") -> dict[str, Any]:
        operation = params.get("operation")
        if operation not in ("open", "read", "click", "disconnect"):
            return self._failure("Choose browser operation open, read, click, or disconnect.")
        if not session_id or not client_turn_id or self._closed.is_set():
            return self._failure("Browser requires an active authorized turn.")
        if cancelled():
            return self._failure("Browser operation cancelled.")
        if operation == "open":
            if parent_turn_id:
                return self._failure("A continuation can use the existing tab. Opening a new tab needs a browser turn.")
            if not _http_url(params.get("url")):
                return self._failure("Opening a browser page requires an HTTP(S) URL without credentials.")
            preferred = params.get("browser", "")
            if preferred not in ("", "edge", "chrome"):
                return self._failure("Choose edge or chrome as the browser.")
        elif operation == "read":
            offset = params.get("offset", 0)
            if type(offset) is not int or not 0 <= offset <= _SNAPSHOT_CHARS:
                return self._failure("Read offset must come from the browser's nextOffset.")
        elif operation == "click":
            target_ref = params.get("target")
            if not isinstance(target_ref, str) or not re.fullmatch(r"b[a-f0-9]{8}-[1-9][0-9]{0,4}", target_ref):
                return self._failure("Choose a button or link reference from the current browser snapshot.",
                                     "browser_click_stale_target")

        with self._lock:
            browser = self._browsers.get(session_id)
            if operation == "open":
                if browser:
                    return self._failure("A browser tab is already assigned to this chat. Read or disconnect it first.")
                if self._closed.is_set() or len(self._browsers) >= _MAX_BROWSERS:
                    return self._failure("Browser service is closed or at capacity.")
                browser = _Browser(client_turn_id, adult_confirmed=adult_confirmed is True)
                self._browsers[session_id] = browser
                if self._janitor is None:
                    self._janitor = threading.Thread(target=self._expire_idle, name="vrcforge-browser-idle", daemon=True)
                    self._janitor.start()
            if (browser is None or browser.cancelled.is_set()
                    or time.monotonic() - browser.last_used >= self._idle_seconds
                    or not self._allows(browser, client_turn_id, parent_turn_id)):
                return self._failure("No browser tab is owned by this turn.")
            if not browser.lock.acquire(blocking=False):
                return self._failure("The browser tab is busy. Wait for its current operation.")
            if browser.client_turn_id != client_turn_id:
                browser.parent_turn_id = parent_turn_id
                browser.client_turn_id = client_turn_id

        def is_cancelled() -> bool:
            return self._closed.is_set() or browser.cancelled.is_set() or cancelled()

        dispose = False
        try:
            _check_cancelled(is_cancelled)
            if operation == "disconnect":
                dispose = True
                return {"ok": True, "status": "ok", "summary": "Closed the app-owned browser window."}
            if operation == "click":
                return self._click(browser, target_ref, click_authority, is_cancelled)
            if operation == "open":
                self._open(browser, params["url"], preferred, is_cancelled)
                offset = 0
            return self._read(browser, offset, is_cancelled)
        except _BrowserError as exc:
            dispose = operation == "open" or isinstance(exc, _Cancelled) or not browser.process or browser.process.poll() is not None
            return self._failure(str(exc), "browser_click_stale_target" if isinstance(exc, _StaleTarget) else "")
        except Exception:
            dispose = True
            LOGGER.exception("App-owned browser connection failed")
            return self._failure("The app-owned browser connection failed. Open a new tab in an authorized turn.")
        finally:
            try:
                if dispose or is_cancelled():
                    self._remove(session_id, browser)
                else:
                    browser.last_used = time.monotonic()
            finally:
                browser.lock.release()

    def _open(self, browser: _Browser, url: str, preferred: str, cancelled: Callable[[], bool]) -> None:
        installed = _browser_executable(preferred)
        if installed is None:
            raise _BrowserError("No matching installed Edge or Chrome browser was found.")
        _, executable = installed
        browser.profile = tempfile.TemporaryDirectory(prefix="vrcforge-browser-", ignore_cleanup_errors=True)
        profile = Path(browser.profile.name)
        arguments = [str(executable), f"--user-data-dir={profile}", "--remote-debugging-port=0",
                     "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
                     "--disable-extensions", "--disable-sync", "--disable-background-mode", "about:blank"]
        if self._headless:
            arguments.insert(-1, "--headless=new")
        browser.process = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL)
        endpoint = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and browser.process.poll() is None:
            _check_cancelled(cancelled)
            try:
                lines = endpoint.read_text(encoding="ascii").splitlines()
            except (FileNotFoundError, PermissionError):
                # Windows can momentarily deny reads while Chromium writes this file.
                lines = []
            if len(lines) >= 2:
                break
            time.sleep(0.05)
        else:
            raise _BrowserError("The app-owned browser did not start its local debugging connection.")
        port, path = lines[:2]
        if not port.isdecimal() or not 0 < int(port) < 65536 or not re.fullmatch(r"/devtools/browser/[a-f0-9-]{36}", path):
            raise _BrowserError("The app-owned browser returned an invalid debugging endpoint.")
        _check_cancelled(cancelled)
        sock = socket.create_connection(("127.0.0.1", int(port)), timeout=2)
        # websockets leaves caller-supplied sockets unchanged. Its receive
        # thread must survive an idle tab while the user handles a site prompt.
        sock.settimeout(None)
        try:
            connection = connect(f"ws://127.0.0.1:{port}{path}", sock=sock, open_timeout=2, close_timeout=0.5,
                                 max_size=8 * 1024 * 1024)
        except Exception:
            sock.close()
            raise
        browser.cdp = _CDP(connection)
        targets = browser.cdp.call("Target.getTargets", cancelled)["targetInfos"]
        target_id = next((target["targetId"] for target in targets
                          if target.get("type") == "page" and target.get("url") == "about:blank"), None)
        if not target_id:
            target_id = browser.cdp.call("Target.createTarget", cancelled, params={"url": "about:blank"})["targetId"]
        browser.session = browser.cdp.call("Target.attachToTarget", cancelled,
                                           params={"targetId": target_id, "flatten": True})["sessionId"]
        self._tab_call(browser, "Page.enable", cancelled)
        loaded = self._tab_call(browser, "Page.navigate", cancelled, params={"url": url})
        if loaded.get("errorText") or loaded.get("isDownload"):
            raise _BrowserError("The browser could not open that page as a document.")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            _check_cancelled(cancelled)
            frame = self._tab_call(browser, "Page.getFrameTree", cancelled)["frameTree"]["frame"]
            if _http_url(frame.get("url")) and frame.get("loaderId") == loaded.get("loaderId"):
                break
            time.sleep(0.05)
        else:
            raise _BrowserError("The browser did not reach an HTTP(S) document.")

    @staticmethod
    def _tab_call(browser: _Browser, method: str, cancelled: Callable[[], bool],
                  *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if browser.cdp is None or not browser.session:
            raise _BrowserError("The browser tab is no longer connected.")
        return browser.cdp.call(method, cancelled, params=params, session=browser.session)

    def _page_frame(self, browser: _Browser, cancelled: Callable[[], bool]) -> dict[str, Any]:
        frame = self._tab_call(browser, "Page.getFrameTree", cancelled)["frameTree"]["frame"]
        if not _http_url(frame.get("url")):
            raise _BrowserError("The browser tab is not showing an HTTP(S) page.")
        return frame

    def _read(self, browser: _Browser, offset: int, cancelled: Callable[[], bool]) -> dict[str, Any]:
        frame = self._page_frame(browser, cancelled)
        page = (frame["url"], frame["loaderId"])
        if not offset:
            browser.targets.clear()
            browser.snapshot_page = None
            nodes = self._tab_call(browser, "Accessibility.getFullAXTree", cancelled)["nodes"]
            current = self._page_frame(browser, cancelled)
            if (current.get("url"), current.get("loaderId")) != page:
                raise _BrowserError("The page navigated during capture. Read the current page again.")
            browser.snapshot, browser.snapshot_truncated = _snapshot_text(nodes, browser.targets)
            browser.snapshot_page = page
        elif browser.snapshot_page != page or offset >= len(browser.snapshot):
            raise _BrowserError("The browser snapshot changed. Read the current page from the beginning.")
        end = offset + _PAGE_CHARS
        return {"ok": True, "status": "ok", "summary": "Read the app-owned browser tab.",
                "snapshot": browser.snapshot[offset:end],
                **({"nextOffset": end} if end < len(browser.snapshot) else {}),
                **({"truncated": True} if browser.snapshot_truncated else {})}

    def _click(self, browser: _Browser, target_ref: str, authority: str, cancelled: Callable[[], bool]) -> dict[str, Any]:
        stale = "The browser target changed or was already used. Read the current page for a fresh reference."
        target = browser.targets.get(target_ref)
        if target is None:
            raise _StaleTarget(stale)
        frame = self._page_frame(browser, cancelled)
        page = (frame["url"], frame["loaderId"])
        if browser.snapshot_page != page:
            raise _StaleTarget(stale)
        nodes = self._tab_call(browser, "Accessibility.getFullAXTree", cancelled)["nodes"]
        current = next((node for node in nodes if _node_target(node) == target), None)
        if current is None:
            raise _StaleTarget(stale)

        site = _site_origin(frame["url"])
        age_confirmation = _is_age_confirmation(nodes, current)
        age_only = (authority != "approved" and browser.adult_confirmed and site not in browser.age_click_sites
                    and age_confirmation)
        if authority != "approved" and not age_only:
            return {"ok": False, "status": "needs_user_action", "code": "browser_click_confirmation_required",
                    "target": target_ref, "label": target.label, "site": site, "ageConfirmation": age_confirmation,
                    "summary": "This browser control requires user approval before clicking."}

        fresh_frame = self._page_frame(browser, cancelled)
        if (fresh_frame["url"], fresh_frame["loaderId"], fresh_frame["id"]) != (*page, frame["id"]):
            raise _StaleTarget(stale)
        context = self._tab_call(browser, "Page.createIsolatedWorld", cancelled,
                                 params={"frameId": frame["id"], "worldName": "vrcforge-click"})["executionContextId"]
        object_id = self._tab_call(browser, "DOM.resolveNode", cancelled, params={"backendNodeId": target.backend_id,
                                   "executionContextId": context, "objectGroup": "vrcforge-click"})["object"]["objectId"]
        browser.targets.clear()
        browser.snapshot_page = None
        if age_only:
            browser.age_click_sites.add(site)
        try:
            result = self._tab_call(browser, "Runtime.callFunctionOn", cancelled,
                                    params={"objectId": object_id, "functionDeclaration": _CLICK_ELEMENT,
                                            "arguments": [{"value": target.label}, {"value": age_only}],
                                            "returnByValue": True})
        finally:
            try:
                self._tab_call(browser, "Runtime.releaseObjectGroup", cancelled, params={"objectGroup": "vrcforge-click"})
            except _BrowserError:
                pass  # The click can navigate and destroy this execution context.
        if result.get("exceptionDetails"):
            raise _BrowserError("The browser could not verify the click. Read the current tab before retrying.")
        if result.get("result", {}).get("value") is not True:
            raise _StaleTarget("The control changed, was blocked, or could not be safely clicked. Read the tab again.")
        receipt = {"ok": True, "status": "ok", "summary": "Clicked the approved browser control.",
                   "clicked": True, "target": target_ref, "label": target.label, "site": site}
        try:
            return {**self._read(browser, 0, cancelled), **receipt}
        except _Cancelled:
            raise
        except _BrowserError:
            return receipt

    @staticmethod
    def _failure(summary: str, code: str = "") -> dict[str, Any]:
        return {"ok": False, "status": "failed", "summary": summary, **({"code": code} if code else {})}

    def _remove(self, session_id: str, browser: _Browser) -> None:
        with self._lock:
            if self._browsers.get(session_id) is browser:
                del self._browsers[session_id]
        if browser.cdp is not None:
            try:
                browser.cdp.call("Browser.close", lambda: False, timeout=0.5)
            except Exception:
                pass  # Chromium can close its socket before replying to Browser.close.
            finally:
                try:
                    browser.cdp.connection.close()
                except OSError:
                    LOGGER.warning("App-owned browser connection did not close cleanly")
        process = browser.process
        if process is not None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    LOGGER.warning("App-owned browser process did not exit after termination")
        if browser.profile is not None:
            browser.profile.cleanup()

    def _expire_idle(self) -> None:
        while not self._closed.wait(min(self._idle_seconds, 10.0)):
            with self._lock:
                browsers = list(self._browsers.items())
            for session_id, browser in browsers:
                if browser.lock.acquire(blocking=False):
                    try:
                        if (browser.cancelled.is_set()
                                or (browser.process is not None and browser.process.poll() is not None)
                                or time.monotonic() - browser.last_used >= self._idle_seconds):
                            self._remove(session_id, browser)
                    finally:
                        browser.lock.release()

    def cancel_owner(self, session_id: str, client_turn_id: str) -> None:
        with self._lock:
            browser = self._browsers.get(session_id)
            if browser is None or browser.client_turn_id != client_turn_id:
                return
            browser.cancelled.set()
        if browser.lock.acquire(blocking=False):
            try:
                self._remove(session_id, browser)
            finally:
                browser.lock.release()

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            owners = [(session_id, browser.client_turn_id) for session_id, browser in self._browsers.items()]
        for session_id, client_turn_id in owners:
            self.cancel_owner(session_id, client_turn_id)
