# main.py - AI-OSTIAS V4 Orchestrator（M3）
"""FastAPI 后端服务：任务理解 / Job 编排 / 状态查询 / 人工干预 / 结果导出。

运行：
    python main.py [--port 8000]
    或 uvicorn main:app --host 127.0.0.1 --port 8000
"""
import argparse

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import VERSION
from routers import jobs, results, system, tasks
from services import db_service

app = FastAPI(title="AI-OSTIAS V4 Orchestrator", version=VERSION)

# 本地演示：CORS 全开
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks.router)
app.include_router(jobs.router)
app.include_router(results.router)
app.include_router(system.router)


@app.on_event("startup")
def startup() -> None:
    db_service.init_db()


@app.get("/")
def root():
    return {"name": "AI-OSTIAS V4 Orchestrator", "version": VERSION,
            "docs": "/docs"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI-OSTIAS V4 Orchestrator")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
