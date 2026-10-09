#!/usr/bin/env python3
"""
server.py — VoxaChat (chat + TEV/JEV only).

Serves the static frontend from web/ and proxies chat to Ollama / Jev.
No TTS, music, or voice-training routes.

Run: python bin/server.py [--port 8766]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apply_snippet as apply_snippet_mod  # noqa: E402
import chat_agent  # noqa: E402
import chat_sessions  # noqa: E402
import folder_picker  # noqa: E402
import fs_tools as _ft  # noqa: E402
import jev_picker as jp  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"

_ft.load_dotenv(REPO_ROOT / ".env")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")

app = FastAPI(title="VoxaChat", version="0.1.0")


@app.post("/api/chat")
async def chat(request: Request):
    """Agentic chat with optional filesystem tools. Typed SSE events."""
    body = await request.json()
    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(400, "messages required")

    async def event_stream():
        cancel = asyncio.Event()

        async def _watch_disconnect():
            try:
                while not cancel.is_set():
                    if await request.is_disconnected():
                        cancel.set()
                        return
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                return

        watcher = asyncio.create_task(_watch_disconnect())
        try:
            async for ev in chat_agent.iter_chat_events(
                body, OLLAMA_URL, cancel=cancel
            ):
                if await request.is_disconnected():
                    cancel.set()
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            cancel.set()
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/chats")
def api_list_chats():
    return {"chats": chat_sessions.list_chats()}


@app.post("/api/chats")
async def api_create_chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    return chat_sessions.create_chat(body)


@app.get("/api/chats/{chat_id}")
def api_get_chat(chat_id: str):
    try:
        return chat_sessions.get_chat(chat_id)
    except ValueError:
        raise HTTPException(400, "invalid chat id")
    except KeyError:
        raise HTTPException(404, "chat not found")


@app.put("/api/chats/{chat_id}")
async def api_put_chat(chat_id: str, request: Request):
    body = await request.json()
    try:
        return chat_sessions.put_chat(chat_id, body if isinstance(body, dict) else {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "chat not found")


@app.patch("/api/chats/{chat_id}")
async def api_patch_chat(chat_id: str, request: Request):
    body = await request.json()
    try:
        return chat_sessions.patch_chat(chat_id, body if isinstance(body, dict) else {})
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "chat not found")


@app.delete("/api/chats/{chat_id}")
def api_delete_chat(chat_id: str):
    try:
        chat_sessions.delete_chat(chat_id)
    except ValueError:
        raise HTTPException(400, "invalid chat id")
    except KeyError:
        raise HTTPException(404, "chat not found")
    return Response(status_code=204)


@app.post("/api/chat/write-decision")
async def chat_write_decision(request: Request):
    body = await request.json()
    pid = body.get("id")
    if not pid:
        raise HTTPException(400, "id required")
    ok = chat_agent.resolve_write_decision(pid, bool(body.get("approve")))
    if not ok:
        raise HTTPException(404, "unknown or expired pending write")
    return {"ok": True}


@app.post("/api/chat/writes-batch-decision")
async def chat_writes_batch_decision(request: Request):
    body = await request.json()
    pid = body.get("id")
    if not pid:
        raise HTTPException(400, "id required")
    approved_ids = body.get("approved_ids") or []
    if not isinstance(approved_ids, list):
        raise HTTPException(400, "approved_ids must be a list")
    ok = chat_agent.resolve_writes_batch_decision(pid, [str(i) for i in approved_ids])
    if not ok:
        raise HTTPException(404, "unknown or expired pending writes batch")
    return {"ok": True}


@app.post("/api/chat/apply-snippet")
async def chat_apply_snippet(request: Request):
    body = await request.json()
    path = body.get("path")
    content = body.get("content")
    roots = body.get("workspace_roots") or []
    if content is None:
        content = ""
    if not isinstance(roots, list):
        raise HTTPException(400, "workspace_roots must be a list")
    try:
        written = await asyncio.to_thread(
            apply_snippet_mod.apply_snippet, path, content, [str(r) for r in roots]
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except _ft.FsError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "path": written}


@app.post("/api/chat/plan-decision")
async def chat_plan_decision(request: Request):
    body = await request.json()
    pid = body.get("id")
    action = body.get("action")
    if not pid or not action:
        raise HTTPException(400, "id and action required")
    ok = chat_agent.resolve_plan_decision(pid, str(action), body.get("note"))
    if not ok:
        raise HTTPException(404, "unknown or expired pending plan")
    return {"ok": True}


@app.post("/api/chat/approve-shell")
async def chat_approve_shell(request: Request):
    body = await request.json()
    pid = body.get("id")
    if not pid:
        raise HTTPException(400, "id required")
    ok = await asyncio.to_thread(
        chat_agent.resolve_shell_decision, pid, bool(body.get("approve"))
    )
    if not ok:
        raise HTTPException(404, "unknown or expired pending shell")
    return {"ok": True}


@app.post("/api/chat/tool-permission")
async def chat_tool_permission(request: Request):
    body = await request.json()
    pid = body.get("id")
    if not pid:
        raise HTTPException(400, "id required")
    ok = chat_agent.resolve_tool_permission(pid, bool(body.get("approve")))
    if not ok:
        raise HTTPException(404, "unknown or expired pending tool permission")
    return {"ok": True}


@app.post("/api/chat/pick-folder")
async def chat_pick_folder(request: Request):
    """Open a native directory dialog and return an absolute filesystem path."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    initial = body.get("initial_dir") if isinstance(body, dict) else None
    try:
        path = await asyncio.to_thread(folder_picker.pick_folder, initial)
    except folder_picker.FolderPickerError as e:
        raise HTTPException(503, str(e)) from e
    if not path:
        return {"cancelled": True, "path": None}
    return {"cancelled": False, "path": path}


@app.post("/api/chat/workspace-grant")
async def chat_workspace_grant(request: Request):
    body = await request.json()
    pid = body.get("id")
    if not pid:
        raise HTTPException(400, "id required")
    ok = chat_agent.resolve_workspace_grant(
        pid,
        root=body.get("root"),
        deny=bool(body.get("deny")),
        workspace_roots=body.get("workspace_roots"),
    )
    if not ok:
        raise HTTPException(404, "unknown or expired pending grant")
    return {"ok": True}


@app.get("/api/models")
async def list_models():
    """List Ollama models."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            raise HTTPException(503, f"Ollama unreachable: {e}") from e
    return {"models": []}


_OLLAMA_MODEL_FALLBACKS = (
    "mistral-nemo:latest",
    "mistral-nemo",
    "qwen2.5:7b",
    "qwen2.5-coder:7b",
    "qwen2.5-coder:latest",
    "mistral-small3.2:latest",
    "mistral-small3.2",
    "qwen3.5:9b",
    "qwen2.5:14b-instruct-q4_K_M",
    "qwen2.5:14b",
    "qwen3.8:latest",
    "qwen3:30b-a3b",
)


def pick_ollama_model(preferred: str, available: set[str]) -> str | None:
    """Pick an installed Ollama model for generate helpers. None if none usable."""
    if not available:
        return None

    def _match(candidate: str) -> str | None:
        if candidate in available:
            return candidate
        bare = candidate.split(":")[0]
        for name in available:
            if name == bare or name.split(":")[0] == bare:
                return name
        return None

    for candidate in (preferred, *_OLLAMA_MODEL_FALLBACKS):
        if not candidate:
            continue
        hit = _match(candidate)
        if hit:
            return hit

    def _size_key(name: str) -> tuple:
        lower = name.lower()
        for label, rank in (
            ("70b", 70),
            ("32b", 32),
            ("30b", 30),
            ("14b", 14),
            ("9b", 9),
            ("8b", 8),
            ("7b", 7),
            ("3b", 3),
            ("1b", 1),
        ):
            if label in lower:
                return (rank, name)
        return (50, name)

    return sorted(available, key=_size_key)[0]


@app.get("/api/health")
async def health():
    """Ollama reachability + repo metadata (no tts/music keys)."""
    ollama = {"enabled": True, "url": OLLAMA_URL, "ok": False, "status": "unknown"}
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            ollama["ok"] = r.status_code == 200
            ollama["status"] = "up" if ollama["ok"] else "down"
            if not ollama["ok"]:
                ollama["error"] = f"HTTP {r.status_code}"
    except Exception as e:
        ollama["status"] = "down"
        ollama["error"] = str(e)
    return {
        "ok": bool(ollama["ok"]),
        "ollama": ollama,
        "repo_root": str(REPO_ROOT),
        "windows": chat_agent.windows_mode(),
        "chat_backend": "jev",
    }


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
async def root():
    index = WEB_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        {
            "error": "frontend not found",
            "expected": str(index),
        },
        status_code=500,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--windows",
        action="store_true",
        help="Enable Windows mode for chat agent paths",
    )
    args = parser.parse_args()

    if args.windows:
        os.environ["VOXA_WINDOWS"] = "1"

    print(f"VoxaChat on http://{args.host}:{args.port}")
    print(f"  Windows mode:   {'on' if chat_agent.windows_mode() else 'off'}")
    print(f"  Jev upstream:   {jp.typesafe_base_url()}")
    print(f"  Ollama upstream: {OLLAMA_URL}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
