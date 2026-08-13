"""server.py - OpenAI-compatible HTTP API in front of TeraBox's Tera AI.

Run:
    export TERABOX_COOKIE_FILE=./cookies.txt      # or TERABOX_NDUS=...
    uvicorn server:app --host 127.0.0.1 --port 8000

Then point any OpenAI client at http://127.0.0.1:8000/v1 with model "tera-ai".

UNOFFICIAL. Endpoints may change without notice. Respect TeraBox's ToS.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from terabox_ai import TeraBoxAI, TeraBoxAIError, TeraBoxAuthError

MODEL_ID = os.environ.get("TERABOX_MODEL_ID", "tera-ai")
API_KEY = os.environ.get("API_KEY")  # optional bearer token for THIS server

app = FastAPI(
    title="TeraBox AI API (unofficial)",
    version="1.0.0",
    description="OpenAI-compatible wrapper around TeraBox's Tera AI agent.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_client: Optional[TeraBoxAI] = None


def get_client() -> TeraBoxAI:
    global _client
    if _client is None:
        try:
            _client = TeraBoxAI(
                ndus=os.environ.get("TERABOX_NDUS"),
                cookie_file=os.environ.get("TERABOX_COOKIE_FILE"),
                cookie_header=os.environ.get("TERABOX_COOKIE"),
                language=os.environ.get("TERABOX_LANGUAGE", "en"),
            )
        except TeraBoxAuthError as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    return _client


def require_key(authorization: Optional[str] = Header(default=None)) -> None:
    if not API_KEY:
        return
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="invalid api key")


# --------------------------------------------------------------------- schemas
class ChatMessage(BaseModel):
    role: str = "user"
    content: Any = ""


class ChatCompletionRequest(BaseModel):
    model: str = MODEL_ID
    messages: List[ChatMessage]
    stream: bool = False
    # TeraBox-specific extras (ignored by standard OpenAI clients)
    chat_id: Optional[str] = None
    language: Optional[str] = None
    include_reasoning: bool = False
    chat_scene: int = 0


class SimpleChatRequest(BaseModel):
    prompt: str
    chat_id: Optional[str] = None
    language: Optional[str] = None
    stream: bool = False
    include_reasoning: bool = Field(default=True)


def _flatten(content: Any) -> str:
    """Accept both plain strings and OpenAI content-part arrays."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _to_terabox_messages(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
    """Fold system prompts into the first user turn; Tera AI has no system role."""
    system_bits = [_flatten(m.content) for m in messages if m.role == "system"]
    out: List[Dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        out.append({"role": m.role, "content": _flatten(m.content), "attachments": []})
    if not out:
        out = [{"role": "user", "content": "", "attachments": []}]
    if system_bits:
        for m in reversed(out):
            if m["role"] == "user":
                m["content"] = "\n\n".join(system_bits + [m["content"]]).strip()
                break
    return out


def _err(exc: Exception) -> HTTPException:
    if isinstance(exc, TeraBoxAuthError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, TeraBoxAIError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ----------------------------------------------------------------- basic routes
@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "model": MODEL_ID, "auth_required": bool(API_KEY)}


@app.get("/v1/models", dependencies=[Depends(require_key)])
def list_models() -> Dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "terabox"}],
    }


@app.get("/api/me", dependencies=[Depends(require_key)])
def me() -> Dict[str, Any]:
    try:
        return get_client().check_login()
    except Exception as exc:
        raise _err(exc)


@app.get("/api/quota", dependencies=[Depends(require_key)])
def quota() -> Dict[str, Any]:
    try:
        return get_client().quota()
    except Exception as exc:
        raise _err(exc)


@app.get("/api/records", dependencies=[Depends(require_key)])
def records(size: int = 20, cursor: Optional[str] = None) -> Dict[str, Any]:
    try:
        return get_client().records(size=size, cursor=cursor)
    except Exception as exc:
        raise _err(exc)


@app.get("/api/templates", dependencies=[Depends(require_key)])
def templates() -> Dict[str, Any]:
    try:
        return get_client().templates()
    except Exception as exc:
        raise _err(exc)


@app.post("/api/stop", dependencies=[Depends(require_key)])
def stop(chat_id: str, query_id: str) -> Dict[str, Any]:
    try:
        return get_client().stop(chat_id, query_id)
    except Exception as exc:
        raise _err(exc)


# ------------------------------------------------------------------ native chat
@app.post("/api/chat", dependencies=[Depends(require_key)])
def simple_chat(req: SimpleChatRequest):
    client = get_client()
    if not req.stream:
        try:
            return client.chat(prompt=req.prompt, chat_id=req.chat_id, language=req.language).to_dict()
        except Exception as exc:
            raise _err(exc)

    def gen() -> Iterator[str]:
        try:
            for ev in client.stream(prompt=req.prompt, chat_id=req.chat_id, language=req.language):
                if ev.obj != "content" or not ev.delta or not ev.text:
                    continue
                if ev.type == "text" or (req.include_reasoning and ev.type == "reasoning"):
                    payload = {"type": ev.type, "text": ev.text, "chat_id": ev.chat_id}
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# -------------------------------------------------------- openai compatible api
@app.post("/v1/chat/completions", dependencies=[Depends(require_key)])
def chat_completions(req: ChatCompletionRequest):
    client = get_client()
    messages = _to_terabox_messages(req.messages)
    created = int(time.time())
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]

    if not req.stream:
        try:
            res = client.chat(
                messages=messages,
                chat_id=req.chat_id,
                language=req.language,
                chat_scene=req.chat_scene,
            )
        except Exception as exc:
            raise _err(exc)
        message: Dict[str, Any] = {"role": "assistant", "content": res.text}
        if req.include_reasoning and res.reasoning:
            message["reasoning_content"] = res.reasoning
        return JSONResponse(
            {
                "id": cid,
                "object": "chat.completion",
                "created": created,
                "model": req.model or MODEL_ID,
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "terabox": {
                    "chat_id": res.chat_id,
                    "query_id": res.query_id,
                    "message_id": res.message_id,
                    "summary": res.summary,
                },
            }
        )

    def gen() -> Iterator[str]:
        def chunk(delta: Dict[str, Any], finish: Optional[str] = None) -> str:
            body = {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": req.model or MODEL_ID,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

        yield chunk({"role": "assistant", "content": ""})
        try:
            for ev in client.stream(
                messages=messages,
                chat_id=req.chat_id,
                language=req.language,
                chat_scene=req.chat_scene,
            ):
                if ev.obj != "content" or not ev.delta or not ev.text:
                    continue
                if ev.type == "text":
                    yield chunk({"content": ev.text})
                elif req.include_reasoning and ev.type == "reasoning":
                    yield chunk({"reasoning_content": ev.text})
        except Exception as exc:  # noqa: BLE001
            yield chunk({"content": f"\n[error] {exc}"}, finish="stop")
            yield "data: [DONE]\n\n"
            return
        yield chunk({}, finish="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
