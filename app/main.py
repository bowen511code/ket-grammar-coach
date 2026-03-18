"""
KET Grammar Coach — FastAPI 后端入口。
业务接口见 docs/api-spec.md。MVP 使用内存数据，不接数据库。
Phase 1 从 resources/ 加载语法点与 A2 词库子集，供后续动态出题使用。
Step 2：按 grammar_point 动态出题（generate_question）。
Step 3：题目注册表 + hint/submit/attempts 支持动态题。
Phase 1.5：受控 LLM 出题（优先）+ 模板 fallback。
Phase 2 Step 1：错因分类（classify_error），后续将接入 POST /api/submit 返回 error_type / error_label。
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.error_classification import classify_error
from services.question_generation import (
    generate_question as _generate_question_impl,
    generate_remedial_question as _generate_remedial_question_impl,
)

# 项目根目录（app/ 的上一级），用于读取 resources/
_BASE_DIR = Path(__file__).resolve().parent.parent
_GRAMMAR_POINTS_PATH = _BASE_DIR / "resources" / "phase1_grammar_points.json"
_VOCAB_A2_PATH = _BASE_DIR / "resources" / "phase1_vocab_a2_minimal.json"

with open(_GRAMMAR_POINTS_PATH, "r", encoding="utf-8") as f:
    GRAMMAR_POINTS = json.load(f)
with open(_VOCAB_A2_PATH, "r", encoding="utf-8") as f:
    VOCAB_A2 = json.load(f)

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


def _mask_api_key(key: str) -> str:
    """返回掩码形式，例如 sk-...abcd，不暴露完整 key。"""
    if not key or len(key) < 8:
        return "(empty or too short)"
    return key[:3] + "..." + key[-4:]


@app.on_event("startup")
def _log_startup() -> None:
    """启动完成后打印，便于确认服务已就绪、LLM 是否启用。"""
    api_key = os.environ.get("OPENAI_API_KEY") or ""
    llm_enabled = bool(api_key.strip())
    base_url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    print(
        "KET Grammar Coach API started, listening for requests. "
        f"Loaded {len(GRAMMAR_POINTS)} grammar points, {len(VOCAB_A2)} vocab entries.",
        flush=True,
    )
    print(
        f"[Phase 1.5] llm_enabled={llm_enabled}, OPENAI_BASE_URL={base_url}, OPENAI_MODEL={model}, "
        f"api_key={_mask_api_key(api_key) if api_key else '(not set)'}",
        flush=True,
    )
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
# 动态题唯一 id 计数，每次 generate_question 递增，避免 QUESTION_REGISTRY 覆盖
question_counter = 0
# Phase 2 Step 3：补救题独立计数，id 形如 q_rem_001
remedial_question_counter = 0

# 动态题注册表：question_id -> 题目对象（含 expected_answer 等，供 hint/submit 查用）
QUESTION_REGISTRY: dict[str, dict[str, Any]] = {}

# 按 grammar_point_id 保留最近题目文本，用于去重与多样性（最多 5 条）
RECENT_QUESTION_TEXTS: dict[str, list[str]] = {}
_RECENT_TEXTS_MAX = 5

# 兼容旧前端：将固定题 q_001 预先注册，保证 hint/submit 仍能处理
def _register_fixed_question() -> None:
    q = {
        "id": QUESTION_ID,
        "type": "multiple_choice",
        "text": FIXED_QUESTION["text"],
        "options": FIXED_QUESTION["options"],
        "grammar_point": FIXED_QUESTION["grammar_point"],
        "grammar_point_id": "gp_3rd_person_s",
        "expected_answer": EXPECTED_ANSWER,
        "option_forms": {"go": "base", "goes": "third_person", "went": "past", "going": "ing"},
    }
    QUESTION_REGISTRY[QUESTION_ID] = q


_register_fixed_question()


# 题目对象中仅后端保留、不返回前端的字段（供 get_question / remedial_question 过滤）
# Phase 2 Step 3.5：补救题内部元数据 is_remedial_question / parent_question_id 不暴露给前端
# Phase 2 错因增强：option_forms 为选项→形式映射，仅后端分类用，不返回前端
_QUESTION_INTERNAL_KEYS = frozenset({
    "expected_answer", "source", "_debug_reason",
    "is_remedial_question", "parent_question_id",
    "option_forms",
})


def generate_question(grammar_point_id: str) -> dict[str, Any]:
    """
    优先走 LLM 受控出题；失败或未配置 API 时 fallback 到模板出题。
    返回 dict 含 id, type, text, options, grammar_point, grammar_point_id, expected_answer，
    以及仅后端使用的 source（"llm"|"template"）与 _debug_reason（fallback 原因）。
    """
    global question_counter
    question_counter += 1
    next_id = f"q_dyn_{question_counter:03d}"
    has_llm_key = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    return _generate_question_impl(
        grammar_point_id,
        GRAMMAR_POINTS,
        VOCAB_A2,
        RECENT_QUESTION_TEXTS,
        next_id,
        _RECENT_TEXTS_MAX,
        has_llm_key,
    )


def generate_remedial_question(grammar_point_id: str, error_type: str, parent_question_id: str) -> dict[str, Any]:
    """Phase 2 Step 3：生成一道补救题，写入 QUESTION_REGISTRY 前由调用方过滤内部字段。"""
    global remedial_question_counter
    remedial_question_counter += 1
    next_id = f"q_rem_{remedial_question_counter:03d}"
    return _generate_remedial_question_impl(
        grammar_point_id, error_type, parent_question_id,
        next_id, GRAMMAR_POINTS, VOCAB_A2,
    )


def generate_hint(question: dict[str, Any]) -> str:
    """按 grammar_point_id 返回模板化 hint，不使用 LLM。"""
    gp_id = question.get("grammar_point_id") or ""
    hints = {
        "gp_3rd_person_s": "注意主语是第三人称单数，谓语要用什么形式？",
        "gp_past_simple": "注意时态是过去，要用动词的什么形式？",
        "gp_there_is_are": "注意主语是单数还是复数，there is 和 there are 怎么选？",
    }
    return hints.get(gp_id, "注意题干中的主语和时态，选出正确的形式。")


def evaluate_answer(question: dict[str, Any], answer: str) -> tuple[bool, str]:
    """根据题目的 expected_answer 评估学生答案。返回 (correct, expected_answer)。"""
    expected = question.get("expected_answer") or ""
    correct = answer.strip().lower() == expected.lower()
    return correct, expected


def generate_explanation(question: dict[str, Any], correct: bool, answer: str) -> str:
    """按 grammar_point_id 返回模板化 explanation；答对可简短肯定或空，答错必须返回讲解。"""
    if correct:
        return "答对了！"
    gp_id = question.get("grammar_point_id") or ""
    explanations = {
        "gp_3rd_person_s": "主语是第三人称单数（如 he/she/it）时，谓语动词要加 -s 或 -es。",
        "gp_past_simple": "表示过去发生的动作要用过去式，规则动词加 -ed，不规则动词用过去式形式。",
        "gp_there_is_are": "单数名词前用 there is，复数名词前用 there are。",
    }
    return explanations.get(gp_id, "请根据语法规则检查主语和时态，选出正确答案。")


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
    grammar_point_id: Optional[str] = None  # Phase 1 扩展，前端可选展示


class HintResponse(BaseModel):
    hint: str


class SubmitResponse(BaseModel):
    question_id: str
    correct: bool
    feedback: str
    expected_answer: str
    explanation: str = ""
    grammar_point_id: Optional[str] = None
    # Phase 2 Step 2：答错时返回错因，答对时为 None
    error_type: Optional[str] = None
    error_label: Optional[str] = None
    # Phase 2 Step 3：答错时返回一道补救题，答对时为 None；题目结构同 GET /api/question
    remedial_question: Optional[dict] = None


class AttemptRecord(BaseModel):
    id: str
    question_id: str
    answer: str
    correct: bool
    created_at: str
    grammar_point_id: Optional[str] = None
    question_text: Optional[str] = None
    explanation: Optional[str] = None
    # Phase 2 Step 2：错因与补救相关字段
    error_type: Optional[str] = None
    error_label: Optional[str] = None
    is_remedial: bool = False
    parent_question_id: Optional[str] = None


class GrammarPointSummary(BaseModel):
    """GET /api/grammar_points 单条返回，供前端语法点选择器使用。"""

    id: str
    label: str
    description: str


# ----- 路由 -----


@app.get("/health")
def health() -> dict[str, str]:
    """存活检查，部署与健康探测用。"""
    return {"status": "ok"}


@app.get("/api/grammar_points", response_model=list[GrammarPointSummary])
def get_grammar_points() -> list[dict]:
    """返回语法点列表（id、label、description），供学生端语法点选择器使用。"""
    return [
        {"id": gp["id"], "label": gp["label"], "description": gp["description"]}
        for gp in GRAMMAR_POINTS
    ]


@app.get("/api/question", response_model=QuestionResponse)
def get_question(
    level: Optional[str] = None,
    grammar_point: Optional[str] = None,
) -> JSONResponse:
    """返回一道题目。若传 grammar_point 则按该语法点动态生成；否则用默认语法点生成。生成后写入注册表供 hint/submit 使用。"""
    gp_id = grammar_point if grammar_point else GRAMMAR_POINTS[0]["id"]
    gp_ids = [gp["id"] for gp in GRAMMAR_POINTS]
    if gp_id not in gp_ids:
        gp_id = GRAMMAR_POINTS[0]["id"]
    q = generate_question(gp_id)
    QUESTION_REGISTRY[q["id"]] = q
    payload = {k: v for k, v in q.items() if k not in _QUESTION_INTERNAL_KEYS}
    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


@app.get("/api/debug/llm_status")
def get_llm_status() -> dict:
    """仅用于本地调试：查看当前运行中的 LLM 配置与是否启用。不返回完整 API key。"""
    api_key = os.environ.get("OPENAI_API_KEY") or ""
    return {
        "llm_enabled": bool(api_key.strip()),
        "base_url": (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
        "model": os.environ.get("OPENAI_MODEL") or "gpt-4o-mini",
        "api_key_present": bool(api_key.strip()),
        "api_key_masked": _mask_api_key(api_key) if api_key else None,
    }


@app.post("/api/hint", response_model=HintResponse)
def post_hint(body: HintRequest) -> dict:
    """根据 question_id 从注册表查题，按 grammar_point_id 返回模板化 hint。"""
    question = QUESTION_REGISTRY.get(body.question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")
    hint = generate_hint(question)
    return {"hint": hint}


@app.post("/api/submit", response_model=SubmitResponse)
def post_submit(body: SubmitRequest) -> dict:
    """根据 question_id 从注册表查题，评估答案，写入 attempts。答错时返回 error_type / error_label；仅普通题答错时返回 remedial_question。补救题只做一层，答错不再生成第二层补救题。"""
    question = QUESTION_REGISTRY.get(body.question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    is_remedial = bool(question.get("is_remedial_question"))
    parent_question_id: Optional[str] = question.get("parent_question_id") if is_remedial else None

    correct, expected_answer = evaluate_answer(question, body.answer)
    explanation = generate_explanation(question, correct, body.answer)
    feedback = "答对了！" if correct else "答错了。"

    error_type: Optional[str] = None
    error_label: Optional[str] = None
    remedial_question: Optional[dict] = None
    if not correct:
        error_type, error_label = classify_error(
            grammar_point_id=question.get("grammar_point_id") or "",
            student_answer=body.answer,
            expected_answer=expected_answer,
            question=question,
        )
        if not is_remedial:
            gp_id = question.get("grammar_point_id") or GRAMMAR_POINTS[0]["id"]
            remedial = generate_remedial_question(gp_id, error_type, parent_question_id=body.question_id)
            QUESTION_REGISTRY[remedial["id"]] = remedial
            remedial_question = {k: v for k, v in remedial.items() if k not in _QUESTION_INTERNAL_KEYS}

    global attempt_id_counter
    attempt_id_counter += 1
    attempt = {
        "id": f"att_{attempt_id_counter:03d}",
        "question_id": body.question_id,
        "answer": body.answer,
        "correct": correct,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grammar_point_id": question.get("grammar_point_id"),
        "question_text": question.get("text"),
        "explanation": explanation,
        "error_type": error_type,
        "error_label": error_label,
        "is_remedial": is_remedial,
        "parent_question_id": parent_question_id,
    }
    attempts.append(attempt)

    return {
        "question_id": body.question_id,
        "correct": correct,
        "feedback": feedback,
        "expected_answer": expected_answer,
        "explanation": explanation,
        "grammar_point_id": question.get("grammar_point_id"),
        "error_type": error_type,
        "error_label": error_label,
        "remedial_question": remedial_question,
    }


@app.get("/api/attempts", response_model=list[AttemptRecord])
def get_attempts(limit: int = 20) -> list[dict]:
    """返回作答记录列表，按插入顺序逆序（最新在前），最多 limit 条。"""
    # 列表按 append 顺序存储，逆序即最新在前，无需解析时间
    return list(reversed(attempts))[:limit]
