"""
KET Grammar Coach — FastAPI 后端入口。
业务接口见 docs/api-spec.md。MVP 使用内存数据，不接数据库。
"""
import sys
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="KET Grammar Coach API",
    description="Backend API for the KET Grammar Coach demo.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000", "http://127.0.0.1:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _log_startup() -> None:
    """启动完成后打印，便于确认服务已就绪、排查挂起。"""
    print("KET Grammar Coach API started, listening for requests.", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()


# ----- 内存数据（MVP 单题 + 作答列表） -----

QUESTION_ID = "q_001"
FIXED_QUESTION = {
    "id": QUESTION_ID,
    "type": "multiple_choice",
    "text": "She _____ to school every day.",
    "options": ["go", "goes", "going", "went"],
    "grammar_point": "present simple, third person -s",
}
EXPECTED_ANSWER = "goes"
HINT_TEXT = "注意主语是第三人称单数，谓语要用什么形式？"

attempts: list[dict] = []
attempt_id_counter = 0


# ----- Pydantic 请求体 / 响应体 -----


class HintRequest(BaseModel):
    question_id: str


class SubmitRequest(BaseModel):
    question_id: str
    answer: str


class QuestionResponse(BaseModel):
    id: str
    type: str
    text: str
    options: list[str]
    grammar_point: str


class HintResponse(BaseModel):
    hint: str


class SubmitResponse(BaseModel):
    question_id: str
    correct: bool
    feedback: str
    expected_answer: str


class AttemptRecord(BaseModel):
    id: str
    question_id: str
    answer: str
    correct: bool
    created_at: str


# ----- 路由 -----


@app.get("/health")
def health() -> dict[str, str]:
    """存活检查，部署与健康探测用。"""
    return {"status": "ok"}


@app.get("/api/question", response_model=QuestionResponse)
def get_question(level: Optional[str] = None) -> dict:
    """返回一道固定题目，学生端展示用。Query 可选 level，MVP 暂未使用。"""
    return FIXED_QUESTION.copy()


@app.post("/api/hint", response_model=HintResponse)
def post_hint(body: HintRequest) -> dict:
    """根据 question_id 返回提示文案。MVP 仅支持 q_001。"""
    if body.question_id != QUESTION_ID:
        raise HTTPException(status_code=404, detail="Question not found")
    return {"hint": HINT_TEXT}


@app.post("/api/submit", response_model=SubmitResponse)
def post_submit(body: SubmitRequest) -> dict:
    """校验答案，写入内存 attempts，并返回 correct / feedback / expected_answer。"""
    if body.question_id != QUESTION_ID:
        raise HTTPException(status_code=404, detail="Question not found")

    correct = body.answer.strip().lower() == EXPECTED_ANSWER.lower()
    if correct:
        feedback = "答对了！"
    else:
        feedback = "正确答案是第三人称单数形式。"

    global attempt_id_counter
    attempt_id_counter += 1
    attempt = {
        "id": f"att_{attempt_id_counter:03d}",
        "question_id": body.question_id,
        "answer": body.answer,
        "correct": correct,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    attempts.append(attempt)

    return {
        "question_id": body.question_id,
        "correct": correct,
        "feedback": feedback,
        "expected_answer": EXPECTED_ANSWER,
    }


@app.get("/api/attempts", response_model=list[AttemptRecord])
def get_attempts(limit: int = 20) -> list[dict]:
    """返回作答记录列表，按插入顺序逆序（最新在前），最多 limit 条。"""
    # 列表按 append 顺序存储，逆序即最新在前，无需解析时间
    return list(reversed(attempts))[:limit]
