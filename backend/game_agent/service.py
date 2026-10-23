# -*- coding: utf-8 -*-
"""拾光漫游 · 独立游戏智能体 API 服务

独立 FastAPI 应用，自带 /api/game/* 路由，不依赖主平台：
  python -m uvicorn game_agent.service:app --host 127.0.0.1 --port 8901

端点：
  POST /api/game/start     {topic, magic_school, identity_id?, talent_id?}
  POST /api/game/advance   {run_id, action, payload?}
  GET  /api/game/state     ?run_id=
  GET  /api/game/health
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from game_agent.store import GameStore  # noqa: E402
from game_agent.demo import DemoStore  # noqa: E402


class StartRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    magic_school: str = Field(default="wood")
    identity_id: str | None = Field(default=None, max_length=40)
    talent_id: str | None = Field(default=None, max_length=40)


class AdvanceRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class DemoStartRequest(BaseModel):
    role_id: str = Field(default="wandering_mage", max_length=40)
    name: str = Field(default="", max_length=12)
    topic: str = Field(default="", max_length=60)


class DemoAdvanceRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    action: str = Field(default="advance", max_length=24)
    choice: str | None = Field(default=None, max_length=200)
    answer: Any = Field(default=None)


app = FastAPI(title="拾光漫游 · 游戏智能体", version="1.0.0")
# 独立演示页（5174）与主程序跨端口访问都需要 CORS。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
_store = GameStore(persist_path=None)
_demo_store = DemoStore(persist_path=str(Path(__file__).resolve().parent / "demo_sessions.json"))


@app.get("/api/game/health")
async def health():
    return {"ok": True, "service": "shiguang-game-agent", "version": "1.0.0"}


@app.post("/api/game/start")
async def start(request: StartRequest):
    try:
        session = _store.start(request.topic, request.magic_school, request.identity_id, request.talent_id)
        return {"success": True, "data": session.snapshot()}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/game/advance")
async def advance(request: AdvanceRequest):
    try:
        return {"success": True, "data": _store.advance(request.run_id, request.action, request.payload)}
    except (ValueError, LookupError) as error:
        status = 404 if isinstance(error, LookupError) else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.get("/api/game/state")
async def state(run_id: str):
    try:
        return {"success": True, "data": _store.get(run_id).snapshot()}
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# ===== 新冒险演示（demo）：角色选择 → 四阶段闭环 =====

@app.post("/api/demo/start")
async def demo_start(request: DemoStartRequest):
    session = await _demo_store.start(request.role_id, request.name, request.topic)
    return {"success": True, "data": session.snapshot()}


@app.post("/api/demo/advance")
async def demo_advance(request: DemoAdvanceRequest):
    try:
        payload = {"choice": request.choice, "answer": request.answer}
        data = await _demo_store.advance_async(request.run_id, request.action, payload)
        return {"success": True, "data": data}
    except (ValueError, LookupError) as error:
        status = 404 if isinstance(error, LookupError) else 400
        raise HTTPException(status_code=status, detail=str(error)) from error


@app.get("/api/demo/list")
async def demo_list():
    """历史存档列表（未通关的冒险，可恢复继续）。"""
    return {"success": True, "data": {"sessions": _demo_store.list_sessions()}}


@app.post("/api/demo/resume")
async def demo_resume(request: DemoAdvanceRequest):
    try:
        session = _demo_store.resume(request.run_id)
        return {"success": True, "data": session.snapshot()}
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
