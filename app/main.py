"""
KET Grammar Coach — FastAPI 后端入口。
业务接口见 docs/api-spec.md，后续按规范实现。
"""
from typing import Dict

from fastapi import FastAPI

app = FastAPI(
    title="KET Grammar Coach API",
    description="Backend API for the KET Grammar Coach demo.",
    version="0.1.0",
)


@app.get("/health")
def health() -> Dict[str, str]:
    """存活检查，部署与健康探测用。"""
    return {"status": "ok"}
