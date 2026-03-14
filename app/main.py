"""
KET Grammar Coach — FastAPI 后端入口。
业务接口见 docs/api-spec.md。MVP 使用内存数据，不接数据库。
Phase 1 从 resources/ 加载语法点与 A2 词库子集，供后续动态出题使用。
Step 2：按 grammar_point 动态出题（generate_question）。
Step 3：题目注册表 + hint/submit/attempts 支持动态题。
Phase 1.5：受控 LLM 出题（优先）+ 模板 fallback。
"""
import json
import os
import re
import random
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
    }
    QUESTION_REGISTRY[QUESTION_ID] = q


_register_fixed_question()


# ----- 语义约束的题目模板（避免“词形拼接”导致不合理句子） -----
# 每个模板为人工约定好的“名词/动词 + 场景”，保证句子语义自然。

# there be：预定义“名词短语 + 地点”，避免出现 a room on the table 等不合理组合
THERE_BE_SENTENCES = [
    {"text": "There _____ a book on the table.", "expected": "is"},
    {"text": "There _____ two books on the table.", "expected": "are"},
    {"text": "There _____ an apple in the box.", "expected": "is"},
    {"text": "There _____ two apples in the box.", "expected": "are"},
    {"text": "There _____ a bag under the chair.", "expected": "is"},
    {"text": "There _____ two bags under the chair.", "expected": "are"},
    {"text": "There _____ a book in the bag.", "expected": "is"},
    {"text": "There _____ two books in the bag.", "expected": "are"},
    {"text": "There _____ a chair in the room.", "expected": "is"},
    {"text": "There _____ two chairs in the room.", "expected": "are"},
]

# 第三人称单数：每个动词对应可搭配的句框，避免 watch to school 等
THIRD_PERSON_TEMPLATES = [
    {"lemma": "go", "sentence": "She _____ to school every day."},
    {"lemma": "go", "sentence": "He _____ to the park every day."},
    {"lemma": "watch", "sentence": "She _____ TV every day."},
    {"lemma": "play", "sentence": "He _____ football every day."},
    {"lemma": "walk", "sentence": "She _____ to the park every day."},
    {"lemma": "like", "sentence": "She _____ music."},
    {"lemma": "read", "sentence": "He _____ a book every day."},
    {"lemma": "visit", "sentence": "She _____ her friend every week."},
    {"lemma": "have", "sentence": "She _____ breakfast at 8."},
]

# 一般过去时：每个动词对应可搭配的句框，避免 watched to park 等
PAST_SIMPLE_TEMPLATES = [
    {"lemma": "go", "sentence": "Yesterday she _____ to the park."},
    {"lemma": "go", "sentence": "He _____ to school yesterday."},
    {"lemma": "watch", "sentence": "She _____ TV yesterday."},
    {"lemma": "play", "sentence": "He _____ football yesterday."},
    {"lemma": "walk", "sentence": "She _____ to the park yesterday."},
    {"lemma": "like", "sentence": "She _____ the film yesterday."},
    {"lemma": "read", "sentence": "He _____ a book yesterday."},
    {"lemma": "visit", "sentence": "She _____ her friend yesterday."},
    {"lemma": "have", "sentence": "She _____ breakfast at 8 yesterday."},
]


def _verb_by_lemma(lemma: str) -> Optional[dict]:
    """从 VOCAB_A2 中按 lemma 取动词，且需含 base/third_person/past。"""
    for v in VOCAB_A2:
        if v.get("part_of_speech") != "verb":
            continue
        if v.get("lemma") != lemma:
            continue
        forms = v.get("forms") or {}
        if "base" in forms and "third_person" in forms and "past" in forms:
            return v
    return None


def _ing_form(base: str) -> str:
    """动词原形转 -ing 形式（最小规则）。"""
    if not base:
        return base
    if base.endswith("e") and len(base) > 1 and base[-2] not in "aeiou":
        return base[:-1] + "ing"
    if len(base) >= 3 and base[-1] in "bdfgmnprt" and base[-2] in "aeiou" and base[-3] not in "aeiou":
        return base + base[-1] + "ing"
    return base + "ing"


# ----- Phase 1.5：受控 LLM 出题 + 校验 + 模板 fallback -----

LLM_QUESTION_TIMEOUT = 15
# 题目对象中仅后端保留、不返回前端的字段（供 get_question 过滤）
_QUESTION_INTERNAL_KEYS = frozenset({"expected_answer", "source", "_debug_reason"})


def _validate_llm_question(obj: Any, grammar_point_id: str) -> bool:
    """
    校验 LLM 返回的题目对象是否合法且与请求的 grammar_point_id 一致。
    不通过则返回 False，调用方应 fallback 到模板。
    加强：text 含 "_____"、options 非空且去重后唯一、expected 唯一匹配一选项。
    """
    if not isinstance(obj, dict):
        return False
    text = obj.get("text")
    if not text or not isinstance(text, str) or not text.strip():
        return False
    if "_____" not in text:
        return False
    options = obj.get("options")
    if not isinstance(options, list) or len(options) != 4:
        return False
    opts_stripped = [str(o).strip() for o in options]
    if not all(len(o) > 0 for o in opts_stripped):
        return False
    opts_lower = [o.lower() for o in opts_stripped]
    if len(set(opts_lower)) != 4:
        return False
    expected = obj.get("expected_answer")
    if expected is None or str(expected).strip() == "":
        return False
    exp_stripped = str(expected).strip()
    exp_lower = exp_stripped.lower()
    matches = [i for i, o in enumerate(opts_lower) if o == exp_lower]
    if len(matches) != 1:
        return False
    gp_id = obj.get("grammar_point_id")
    if gp_id != grammar_point_id:
        return False
    return True


# 常见功能词，vocab 软校验时允许出现，不视为“超纲”
_VOCAB_SOFT_CHECK_FUNCTION_WORDS = frozenset(
    {
        "a", "an", "the", "to", "in", "on", "at", "for", "of", "with", "by",
        "she", "he", "it", "they", "we", "i", "my", "her", "his", "their", "our",
        "yesterday", "every", "day", "week", "last", "night", "morning",
        "is", "are", "am", "be", "was", "were", "_____",
    }
)


def _get_allowed_word_set(grammar_point_id: str) -> set[str]:
    """从 VOCAB_A2 中提取该 grammar_point 对应的允许词集合（lemma + 各 form 值），小写。"""
    allowed = set()
    for item in VOCAB_A2:
        tags = item.get("tags") or []
        if grammar_point_id not in tags:
            continue
        lemma = item.get("lemma")
        if lemma:
            allowed.add(str(lemma).lower())
        forms = item.get("forms")
        if isinstance(forms, dict):
            for v in forms.values():
                if isinstance(v, str):
                    allowed.add(v.lower())
                elif isinstance(v, list):
                    for x in v:
                        if isinstance(x, str):
                            allowed.add(x.lower())
    return allowed


def _vocab_soft_check_llm_question(obj: Any, grammar_point_id: str) -> bool:
    """
    最小 vocab 软校验：text + options 中出现的“内容词”不应大量超出允许词表。
    仅作 sanity check，不因冠词/介词/代词等误杀。超出阈值则返回 False 触发 fallback。
    当前策略：允许词 = VOCAB_A2 该 gp 子集 + 常见功能词；其余词数若超过 5 个则不通过。
    """
    allowed = _get_allowed_word_set(grammar_point_id) | _VOCAB_SOFT_CHECK_FUNCTION_WORDS
    text = (obj.get("text") or "")
    options = obj.get("options") or []
    combined = text + " " + " ".join(str(o) for o in options)
    words = re.findall(r"[a-zA-Z]+", combined)
    unknown = [w for w in words if len(w) >= 2 and w.lower() not in allowed]
    return len(unknown) <= 5


def _build_vocab_summary_for_gp(grammar_point_id: str) -> str:
    """为 prompt 构建当前语法点可用的词库摘要（来自 VOCAB_A2）。"""
    parts = []
    for item in VOCAB_A2:
        tags = item.get("tags") or []
        if grammar_point_id not in tags:
            continue
        lemma = item.get("lemma", "")
        pos = item.get("part_of_speech", "")
        forms = item.get("forms") or {}
        if isinstance(forms, dict):
            form_str = ", ".join(f"{k}={v}" for k, v in forms.items())
        else:
            form_str = str(forms)
        parts.append(f"- {lemma} ({pos}): {form_str}")
    return "\n".join(parts) if parts else "(no vocab filtered by this grammar point)"


def _generate_question_llm(
    grammar_point_id: str,
    next_id: str,
    label: str,
    description: str,
    recent_texts: Optional[list[str]] = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    OpenAI-compatible API 受控出题。
    成功返回 (question_dict, None)；失败返回 (None, reason) 供调用方记录并 fallback。
    recent_texts: 最近已用题干文本，prompt 中会要求避免重复。
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        return (None, "no_api_key")
    base_url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    vocab_block = _build_vocab_summary_for_gp(grammar_point_id)
    recent_block = ""
    if recent_texts:
        recent_block = "\nAvoid repeating these recently used sentences (vary subject/object/scene):\n" + "\n".join(f"- {t}" for t in recent_texts)

    system = """You are a KET/A2 English grammar question writer. Output only valid JSON, no markdown or explanation."""
    user = f"""Generate one multiple-choice grammar question in English for KET/A2 learners.

Grammar point ID: {grammar_point_id}
Grammar point: {label}
Description: {description}

Constraints:
- The sentence must be natural and semantically correct (no nonsense like "a room on the table").
- Use mainly words from the allowed vocab list below. You may add very common words (e.g. articles, "yesterday") if needed.
- Exactly 4 options. One correct answer. The blank in the sentence should be filled by one of the options.
- Output strict JSON only, with these keys: "text", "options" (array of 4 strings), "expected_answer", "grammar_point_id".
- "text" must contain "_____" as the blank. "grammar_point_id" must be exactly "{grammar_point_id}".
- Avoid repeating recently used question texts; vary subjects, objects, and scenes when possible. Generate a new sentence rather than reusing the same safe pattern.
{recent_block}

Allowed vocab (use these where possible):
{vocab_block}

Example shape (do not copy, generate a new question):
{{"text": "She _____ to school every day.", "options": ["go", "goes", "going", "went"], "expected_answer": "goes", "grammar_point_id": "gp_3rd_person_s"}}

Output the JSON now:"""

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.65,
    }).encode("utf-8")
    url = f"{base_url}/chat/completions"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_QUESTION_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError, TypeError):
        return (None, "llm_request_failed")
    choices = data.get("choices") or []
    if not choices:
        return (None, "llm_request_failed")
    content = (choices[0] or {}).get("message") or {}
    if isinstance(content, dict):
        content = content.get("content") or ""
    if not isinstance(content, str):
        return (None, "llm_invalid_json")
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(l for l in lines if l.strip() and not l.strip().startswith("```"))
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return (None, "llm_invalid_json")
    if not _validate_llm_question(parsed, grammar_point_id):
        return (None, "llm_validation_failed")
    if not _vocab_soft_check_llm_question(parsed, grammar_point_id):
        return (None, "llm_vocab_soft_check_failed")
    return (
        {
            "id": next_id,
            "type": "multiple_choice",
            "text": parsed["text"].strip(),
            "options": [str(x).strip() for x in parsed["options"]],
            "grammar_point": label,
            "grammar_point_id": grammar_point_id,
            "expected_answer": str(parsed["expected_answer"]).strip(),
        },
        None,
    )


def _generate_question_template(grammar_point_id: str, next_id: str) -> dict[str, Any]:
    """
    模板版出题（语义约束的预定义模板），用作 LLM 失败或未配置时的 fallback。
    返回 dict 含 id, type, text, options, grammar_point, grammar_point_id, expected_answer。
    """
    gp_map = {gp["id"]: gp for gp in GRAMMAR_POINTS}
    gp = gp_map.get(grammar_point_id)
    if not gp:
        grammar_point_id = GRAMMAR_POINTS[0]["id"]
        gp = GRAMMAR_POINTS[0]

    label = gp.get("label") or grammar_point_id
    template_type = gp.get("template_type") or ""

    if grammar_point_id == "gp_3rd_person_s":
        # 只选“动词 + 句框”在词库中存在且搭配合理的模板
        valid = [t for t in THIRD_PERSON_TEMPLATES if _verb_by_lemma(t["lemma"])]
        if not valid:
            template = {"lemma": "go", "sentence": "She _____ to school every day."}
            verb = _verb_by_lemma("go") or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
        else:
            template = random.choice(valid)
            verb = _verb_by_lemma(template["lemma"])
            if not verb:
                verb = _verb_by_lemma("go") or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
        forms = verb.get("forms") or {}
        base = forms.get("base", "go")
        third = forms.get("third_person", "goes")
        past = forms.get("past", "went")
        ing = _ing_form(base)
        text = template.get("sentence", "She _____ to school every day.")
        options = [base, third, past, ing]
        random.shuffle(options)
        return {
            "id": next_id,
            "type": "multiple_choice",
            "text": text,
            "options": options,
            "grammar_point": label,
            "grammar_point_id": grammar_point_id,
            "expected_answer": third,
        }

    if grammar_point_id == "gp_past_simple":
        valid = [t for t in PAST_SIMPLE_TEMPLATES if _verb_by_lemma(t["lemma"])]
        if not valid:
            template = {"lemma": "go", "sentence": "Yesterday she _____ to the park."}
            verb = _verb_by_lemma("go") or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
        else:
            template = random.choice(valid)
            verb = _verb_by_lemma(template["lemma"])
            if not verb:
                verb = _verb_by_lemma("go") or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
        forms = verb.get("forms") or {}
        base = forms.get("base", "go")
        third = forms.get("third_person", "goes")
        past = forms.get("past", "went")
        ing = _ing_form(base)
        text = template.get("sentence", "Yesterday she _____ to the park.")
        options = [base, third, past, ing]
        random.shuffle(options)
        return {
            "id": next_id,
            "type": "multiple_choice",
            "text": text,
            "options": options,
            "grammar_point": label,
            "grammar_point_id": grammar_point_id,
            "expected_answer": past,
        }

    if grammar_point_id == "gp_there_is_are":
        entry = random.choice(THERE_BE_SENTENCES)
        text = entry["text"]
        expected = entry["expected"]
        options = ["is", "are", "am", "be"]
        random.shuffle(options)
        return {
            "id": next_id,
            "type": "multiple_choice",
            "text": text,
            "options": options,
            "grammar_point": label,
            "grammar_point_id": grammar_point_id,
            "expected_answer": expected,
        }

    # 未知语法点：退回第一项
    return _generate_question_template(GRAMMAR_POINTS[0]["id"], next_id)


def _append_recent_text(grammar_point_id: str, text: str) -> None:
    """将题目文本加入该语法点的最近列表并保持最多 _RECENT_TEXTS_MAX 条。"""
    global RECENT_QUESTION_TEXTS
    rec = RECENT_QUESTION_TEXTS.setdefault(grammar_point_id, [])
    rec.append(text.strip())
    RECENT_QUESTION_TEXTS[grammar_point_id] = rec[-_RECENT_TEXTS_MAX:]


def generate_question(grammar_point_id: str) -> dict[str, Any]:
    """
    优先走 LLM 受控出题；失败或未配置 API 时 fallback 到模板出题。
    返回 dict 含 id, type, text, options, grammar_point, grammar_point_id, expected_answer，
    以及仅后端使用的 source（"llm"|"template"）与 _debug_reason（fallback 原因）。
    同一 grammar_point 下会避免与最近若干题题干重复（重试一次或 fallback）。
    """
    global question_counter
    question_counter += 1
    next_id = f"q_dyn_{question_counter:03d}"

    gp_map = {gp["id"]: gp for gp in GRAMMAR_POINTS}
    gp = gp_map.get(grammar_point_id)
    if not gp:
        grammar_point_id = GRAMMAR_POINTS[0]["id"]
        gp = GRAMMAR_POINTS[0]

    recent = RECENT_QUESTION_TEXTS.get(grammar_point_id, [])

    if not os.environ.get("OPENAI_API_KEY") or not os.environ.get("OPENAI_API_KEY", "").strip():
        q = _generate_question_template(grammar_point_id, next_id)
        q["source"] = "template"
        q["_debug_reason"] = "no_api_key"
        _append_recent_text(grammar_point_id, q["text"])
        print(f"[Phase 1.5] question source=template reason=no_api_key id={next_id}", flush=True)
        return q

    label = gp.get("label") or grammar_point_id
    description = gp.get("description") or ""
    try:
        llm_q, reason = _generate_question_llm(
            grammar_point_id, next_id, label, description, recent_texts=recent
        )
        if llm_q is not None:
            text = llm_q["text"].strip()
            if text in recent:
                llm_q2, reason2 = _generate_question_llm(
                    grammar_point_id, next_id, label, description, recent_texts=recent
                )
                if llm_q2 is not None and llm_q2["text"].strip() not in recent:
                    llm_q = llm_q2
                    llm_q["source"] = "llm"
                    llm_q["_debug_reason"] = None
                    _append_recent_text(grammar_point_id, llm_q["text"])
                    print(f"[Phase 1.5] question source=llm id={next_id} (after duplicate retry)", flush=True)
                    return llm_q
                q = _generate_question_template(grammar_point_id, next_id)
                q["source"] = "template"
                q["_debug_reason"] = "llm_duplicate_text"
                _append_recent_text(grammar_point_id, q["text"])
                print(f"[Phase 1.5] question source=template reason=llm_duplicate_text id={next_id}", flush=True)
                return q
            llm_q["source"] = "llm"
            llm_q["_debug_reason"] = None
            _append_recent_text(grammar_point_id, llm_q["text"])
            print(f"[Phase 1.5] question source=llm id={next_id}", flush=True)
            return llm_q
        fallback_reason = reason or "llm_request_failed"
        q = _generate_question_template(grammar_point_id, next_id)
        q["source"] = "template"
        q["_debug_reason"] = fallback_reason
        _append_recent_text(grammar_point_id, q["text"])
        print(f"[Phase 1.5] question source=template reason={fallback_reason} id={next_id}", flush=True)
        return q
    except Exception as e:
        q = _generate_question_template(grammar_point_id, next_id)
        q["source"] = "template"
        q["_debug_reason"] = "llm_request_failed"
        _append_recent_text(grammar_point_id, q["text"])
        print(
            f"[Phase 1.5] question source=template reason=llm_request_failed (exception: {type(e).__name__}) id={next_id}",
            flush=True,
        )
        return q


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


class AttemptRecord(BaseModel):
    id: str
    question_id: str
    answer: str
    correct: bool
    created_at: str
    grammar_point_id: Optional[str] = None
    question_text: Optional[str] = None
    explanation: Optional[str] = None


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
    """根据 question_id 从注册表查题，评估答案，写入 attempts，返回结果与 explanation。"""
    question = QUESTION_REGISTRY.get(body.question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    correct, expected_answer = evaluate_answer(question, body.answer)
    explanation = generate_explanation(question, correct, body.answer)
    feedback = "答对了！" if correct else "答错了。"

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
    }
    attempts.append(attempt)

    return {
        "question_id": body.question_id,
        "correct": correct,
        "feedback": feedback,
        "expected_answer": expected_answer,
        "explanation": explanation,
        "grammar_point_id": question.get("grammar_point_id"),
    }


@app.get("/api/attempts", response_model=list[AttemptRecord])
def get_attempts(limit: int = 20) -> list[dict]:
    """返回作答记录列表，按插入顺序逆序（最新在前），最多 limit 条。"""
    # 列表按 append 顺序存储，逆序即最新在前，无需解析时间
    return list(reversed(attempts))[:limit]
