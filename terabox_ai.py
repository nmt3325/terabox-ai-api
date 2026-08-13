"""terabox_ai.py - TeraBox AI (Tera AI) unofficial Python client.

Reverse engineered from the TeraBox web app (https://www.terabox.com/ai/agent).
Auth uses the browser session cookies of a logged-in TeraBox account
(the ``ndus`` cookie is the one that actually matters).

UNOFFICIAL. Endpoints can change without notice. Respect TeraBox's ToS.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from http.cookiejar import MozillaCookieJar
from typing import Any, Dict, Iterator, List, Optional

import requests

BASE = "https://www.terabox.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# Query params the web client sends on almost every request.
COMMON_PARAMS = {"app_id": "250528", "web": "1", "channel": "dubox", "clienttype": "0"}


class TeraBoxAIError(RuntimeError):
    """Generic TeraBox AI error."""

    def __init__(self, message: str, errno: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.errno = errno
        self.payload = payload


class TeraBoxAuthError(TeraBoxAIError):
    """Raised when the session cookie is missing, expired or rejected."""


@dataclass
class StreamEvent:
    """One decoded SSE frame from /ai/proxy/agent/stream."""

    event: str = ""          # start | generating | ping | end | error
    obj: str = ""            # response | message | content
    type: str = ""           # reasoning | text | summary | non-textblock | ...
    status: str = ""         # created | in_progress | completed
    delta: bool = False
    text: str = ""
    chat_id: str = ""
    query_id: str = ""
    message_id: str = ""
    sequence: int = -1
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    """Aggregated result of one non-streaming chat turn."""

    text: str = ""
    reasoning: str = ""
    summary: str = ""
    chat_id: str = ""
    query_id: str = ""
    message_id: str = ""
    events: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "reasoning": self.reasoning,
            "summary": self.summary,
            "chat_id": self.chat_id,
            "query_id": self.query_id,
            "message_id": self.message_id,
            "events": self.events,
        }


def _load_cookiejar(path: str) -> MozillaCookieJar:
    jar = MozillaCookieJar()
    jar.load(path, ignore_discard=True, ignore_expires=True)
    return jar


def _parse_cookie_header(raw: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


class TeraBoxAI:
    """Client for the Tera AI chat agent and related AI endpoints."""

    def __init__(
        self,
        ndus: Optional[str] = None,
        cookie_file: Optional[str] = None,
        cookies: Optional[Dict[str, str]] = None,
        cookie_header: Optional[str] = None,
        user_agent: str = DEFAULT_UA,
        language: str = "en",
        timeout: int = 180,
    ) -> None:
        self.language = language
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": BASE,
                "Referer": f"{BASE}/ai/agent",
                "X-Requested-With": "XMLHttpRequest",
            }
        )

        loaded = False
        if cookie_file:
            self.session.cookies = _load_cookiejar(os.path.expanduser(cookie_file))  # type: ignore[assignment]
            loaded = True
        if cookie_header:
            for k, v in _parse_cookie_header(cookie_header).items():
                self.session.cookies.set(k, v, domain=".terabox.com", path="/")
            loaded = True
        if cookies:
            for k, v in cookies.items():
                self.session.cookies.set(k, v, domain=".terabox.com", path="/")
            loaded = True
        ndus = ndus or os.environ.get("TERABOX_NDUS")
        if ndus:
            self.session.cookies.set("ndus", ndus, domain=".terabox.com", path="/")
            loaded = True
        if not loaded:
            raise TeraBoxAuthError(
                "No credentials. Pass ndus=, cookie_file=, cookies= or cookie_header=, "
                "or set the TERABOX_NDUS environment variable."
            )

    # ------------------------------------------------------------------ utils
    def _url(self, path: str) -> str:
        return path if path.startswith("http") else BASE + path

    def _params(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        p = dict(COMMON_PARAMS)
        if extra:
            p.update({k: v for k, v in extra.items() if v is not None})
        return p

    def _json(self, method: str, path: str, params=None, **kw) -> Dict[str, Any]:
        r = self.session.request(
            method, self._url(path), params=self._params(params), timeout=self.timeout, **kw
        )
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            raise TeraBoxAIError(f"Non-JSON response from {path}: {r.text[:200]}")
        errno = data.get("errno")
        if errno not in (0, None):
            msg = data.get("show_msg") or data.get("newno") or f"errno={errno}"
            if errno in (-6, 2, 100003):
                raise TeraBoxAuthError(f"{path}: {msg}", errno=errno, payload=data)
            raise TeraBoxAIError(f"{path}: {msg}", errno=errno, payload=data)
        return data

    # ------------------------------------------------------------- session api
    def check_login(self) -> Dict[str, Any]:
        """GET /api/check/login - verify the cookie session. Returns {'uk': ...}."""
        return self._json("GET", "/api/check/login")

    def user_info(self) -> Dict[str, Any]:
        """GET /api/user/getinfo - account profile."""
        return self._json("GET", "/api/user/getinfo")

    def quota(self) -> Dict[str, Any]:
        """GET /api/quota - storage quota (bytes used / total)."""
        return self._json("GET", "/api/quota")

    # ------------------------------------------------------------------ tera ai
    def templates(self, language: Optional[str] = None) -> Dict[str, Any]:
        """GET /ai/proxy/agent/template - suggested prompt templates.

        ``client_source`` and ``language`` are BOTH required by the server;
        omitting either returns HTTP 400 errno 2 (TemplateReqDto validation).
        """
        return self._json(
            "GET",
            "/ai/proxy/agent/template",
            params={"client_source": 1, "language": language or self.language},
        )

    def records(self, size: int = 20, cursor: Optional[str] = None) -> Dict[str, Any]:
        """GET /ai/proxy/record/list - AI history records (paginated by cursor)."""
        return self._json("GET", "/ai/proxy/record/list", params={"size": size, "cursor": cursor})

    def notes(self, page: int = 1, page_size: int = 10, sort_type: int = 0) -> Dict[str, Any]:
        """POST /ai/proxy/studyhall/notelist - AI Notebook entries."""
        return self._json(
            "POST",
            "/ai/proxy/studyhall/notelist",
            data={"page": page, "page_size": page_size, "sort_type": sort_type},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    def stop(self, chat_id: str, query_id: str) -> Dict[str, Any]:
        """POST /ai/proxy/chat/stop - abort an in-flight generation."""
        return self._json(
            "POST",
            "/ai/proxy/chat/stop",
            json={"chat_id": chat_id, "query_id": query_id},
            headers={"Content-Type": "application/json"},
        )

    # --------------------------------------------------------------- streaming
    def stream(
        self,
        prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        chat_id: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        chat_scene: int = 0,
        is_rechat: bool = False,
        language: Optional[str] = None,
    ) -> Iterator[StreamEvent]:
        """POST /ai/proxy/agent/stream - yield decoded StreamEvent objects.

        Pass ``prompt`` for a single turn, or ``messages`` (OpenAI-ish
        [{'role','content'}] list) to replay a conversation. Supply ``chat_id``
        from a previous turn to continue that conversation server-side.
        """
        payload_messages = self._build_payload_messages(
            prompt=prompt, messages=messages, chat_id=chat_id, attachments=attachments
        )
        body = {
            "messages": payload_messages,
            "is_first": chat_id is None,
            "is_rechat": is_rechat,
            "chat_id": chat_id,
            "chat_scene": chat_scene,
        }
        params = {
            "language": language or self.language,
            "client_source": "1",
            "clienttype": "0",
        }
        with self.session.post(
            self._url("/ai/proxy/agent/stream"),
            params=params,
            json=body,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
            stream=True,
            timeout=self.timeout,
        ) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("Content-Type", "")
            if "event-stream" not in ctype:
                text = resp.text[:500]
                try:
                    data = json.loads(text)
                except ValueError:
                    raise TeraBoxAIError(f"Unexpected response: {text}")
                errno = data.get("errno")
                msg = data.get("show_msg") or data.get("newno") or f"errno={errno}"
                if errno in (-6, 2, 100003):
                    raise TeraBoxAuthError(msg, errno=errno, payload=data)
                raise TeraBoxAIError(msg, errno=errno, payload=data)
            for payload in self._iter_sse(resp):
                for ev in self._decode(payload):
                    yield ev

    # ------------------------------------------------------- payload building
    # NOTE: the upstream endpoint rejects a "messages" array with more than one
    # entry (errno 2 / "params error"). Conversation state lives server-side and
    # is addressed by chat_id, so we always send exactly one message.
    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            out = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    out.append(c.get("text", ""))
                elif isinstance(c, str):
                    out.append(c)
            return "\n".join(out)
        return "" if content is None else str(content)

    @staticmethod
    def _flatten_history(msgs: List[Dict[str, Any]]) -> str:
        labels = {"user": "User", "assistant": "Assistant", "system": "System"}
        lines = []
        for m in msgs:
            text = (m.get("content") or "").strip()
            if not text:
                continue
            role = m.get("role", "user")
            lines.append(f"{labels.get(role, role.capitalize())}: {text}")
        return "\n\n".join(lines)

    @classmethod
    def _build_payload_messages(
        cls,
        prompt: Optional[str],
        messages: Optional[List[Dict[str, Any]]],
        chat_id: Optional[str],
        attachments: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        if messages is None:
            if prompt is None:
                raise ValueError("Either prompt or messages is required")
            single = {"role": "user", "content": prompt, "attachments": attachments or []}
            return [single]
        norm = [
            {
                "role": m.get("role", "user"),
                "content": cls._content_to_text(m.get("content", "")),
                "attachments": m.get("attachments") or [],
            }
            for m in messages
        ]
        if not norm:
            raise ValueError("messages must not be empty")
        if len(norm) == 1:
            out = dict(norm[0])
        elif chat_id:
            # The server already remembers the thread: send only the newest user turn.
            out = dict(next((m for m in reversed(norm) if m["role"] == "user"), norm[-1]))
        else:
            # No server-side thread yet: replay the history as one prompt.
            out = {
                "role": "user",
                "content": cls._flatten_history(norm),
                "attachments": norm[-1].get("attachments") or [],
            }
        out["role"] = "user"
        if attachments:
            out["attachments"] = attachments
        out.setdefault("attachments", [])
        return [out]

    @staticmethod
    def _iter_sse(resp) -> Iterator[str]:
        buf: List[str] = []
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.rstrip("\r")
            if line == "":
                if buf:
                    yield "\n".join(buf)
                    buf = []
                continue
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
        if buf:
            yield "\n".join(buf)

    @staticmethod
    def _decode(payload: str) -> Iterator[StreamEvent]:
        if not payload or payload == "[DONE]":
            return
        try:
            env = json.loads(payload)
        except ValueError:
            return
        name = env.get("event", "")
        data = env.get("data") or {}
        if not isinstance(data, dict):
            yield StreamEvent(event=name, raw=env)
            return
        base = dict(
            event=name,
            chat_id=data.get("echat_id", "") or "",
            query_id=data.get("equery_id", "") or "",
            message_id=data.get("message_id", "") or "",
        )
        result = data.get("result")
        if isinstance(result, str) and result.startswith("{"):
            try:
                rj = json.loads(result)
            except ValueError:
                yield StreamEvent(raw=env, **base)
                return
            yield StreamEvent(
                obj=rj.get("object", "") or "",
                type=rj.get("type", "") or "",
                status=rj.get("status", "") or "",
                delta=bool(rj.get("delta")),
                text=rj.get("text") or "",
                sequence=rj.get("sequence_number", -1),
                raw=rj,
                **base,
            )
        else:
            yield StreamEvent(raw=env, **base)

    # --------------------------------------------------------------- high level
    def iter_text(self, *a, include_reasoning: bool = False, **kw) -> Iterator[str]:
        """Yield only incremental answer text (optionally reasoning too)."""
        wanted = {"text"} | ({"reasoning"} if include_reasoning else set())
        for ev in self.stream(*a, **kw):
            if ev.obj == "content" and ev.delta and ev.type in wanted and ev.text:
                yield ev.text

    def chat(self, *a, **kw) -> ChatResult:
        """Run one turn to completion and return the aggregated ChatResult."""
        res = ChatResult()
        blocks: Dict[Any, Dict[str, Any]] = {}
        order: List[Any] = []
        for ev in self.stream(*a, **kw):
            res.events += 1
            if ev.chat_id:
                res.chat_id = ev.chat_id
            if ev.query_id:
                res.query_id = ev.query_id
            if ev.message_id:
                res.message_id = ev.message_id
            if ev.event == "error":
                raise TeraBoxAIError(f"stream error: {ev.raw}")
            if ev.obj == "message" and ev.type == "summary":
                res.summary = (ev.raw.get("metadata") or {}).get("summary", "") or res.summary
            if ev.obj != "content" or ev.type not in ("text", "reasoning"):
                continue
            key = (ev.raw.get("msg_id") or ev.raw.get("id") or "", ev.raw.get("index", 0), ev.type)
            if key not in blocks:
                blocks[key] = {"type": ev.type, "text": ""}
                order.append(key)
            if ev.delta:
                blocks[key]["text"] += ev.text
            elif ev.status == "completed" and ev.text:
                blocks[key]["text"] = ev.text
        res.text = "".join(blocks[k]["text"] for k in order if blocks[k]["type"] == "text")
        res.reasoning = "".join(blocks[k]["text"] for k in order if blocks[k]["type"] == "reasoning")
        return res


def _cli() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Tera AI CLI (unofficial)")
    ap.add_argument("prompt", nargs="*", help="prompt text")
    ap.add_argument("--cookie-file", default=os.environ.get("TERABOX_COOKIE_FILE"))
    ap.add_argument("--ndus", default=os.environ.get("TERABOX_NDUS"))
    ap.add_argument("--chat-id", default=None)
    ap.add_argument("--language", default=os.environ.get("TERABOX_LANGUAGE", "en"))
    ap.add_argument("--reasoning", action="store_true", help="also stream reasoning")
    ap.add_argument("--whoami", action="store_true", help="just check the session")
    args = ap.parse_args()

    client = TeraBoxAI(ndus=args.ndus, cookie_file=args.cookie_file, language=args.language)
    if args.whoami:
        print(json.dumps(client.check_login(), ensure_ascii=False, indent=2))
        return 0
    prompt = " ".join(args.prompt).strip() or sys.stdin.read().strip()
    if not prompt:
        ap.error("no prompt given")
    last = None
    for ev in client.stream(prompt=prompt, chat_id=args.chat_id):
        if ev.obj == "content" and ev.delta and ev.text:
            if ev.type == "text":
                sys.stdout.write(ev.text)
                sys.stdout.flush()
            elif args.reasoning and ev.type == "reasoning":
                sys.stderr.write(ev.text)
                sys.stderr.flush()
        last = ev
    print()
    if last is not None and last.chat_id:
        print(f"[chat_id={last.chat_id}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
