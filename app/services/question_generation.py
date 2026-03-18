"""
出题/补救题生成/题目校验/题目去重/词形推断。
Phase 1.5：受控 LLM 出题 + 校验 + 模板 fallback。
不依赖 FastAPI；所需数据通过参数传入。
"""
import json
import os
import re
import random
import urllib.error
import urllib.request
from typing import Any, Optional

from services.error_classification import (
    _build_option_forms_for_there_be,
    _build_option_forms_for_verb_question,
    _infer_option_forms_for_question,
)

# ----- 常量 -----

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

LLM_QUESTION_TIMEOUT = 15

_VOCAB_SOFT_CHECK_FUNCTION_WORDS = frozenset(
    {
        "a", "an", "the", "to", "in", "on", "at", "for", "of", "with", "by",
        "she", "he", "it", "they", "we", "i", "my", "her", "his", "their", "our",
        "yesterday", "every", "day", "week", "last", "night", "morning",
        "is", "are", "am", "be", "was", "were", "_____",
    }
)


# ----- 词形与词库 -----


def _ing_form(base: str) -> str:
    """动词原形转 -ing 形式（最小规则）。"""
    if not base:
        return base
    if base.endswith("e") and len(base) > 1 and base[-2] not in "aeiou":
        return base[:-1] + "ing"
    if len(base) >= 3 and base[-1] in "bdfgmnprt" and base[-2] in "aeiou" and base[-3] not in "aeiou":
        return base + base[-1] + "ing"
    return base + "ing"


def _verb_by_lemma(lemma: str, vocab_a2: list[dict]) -> Optional[dict]:
    """从 vocab_a2 中按 lemma 取动词，且需含 base/third_person/past。"""
    for v in vocab_a2:
        if v.get("part_of_speech") != "verb":
            continue
        if v.get("lemma") != lemma:
            continue
        forms = v.get("forms") or {}
        if "base" in forms and "third_person" in forms and "past" in forms:
            return v
    return None


# ----- LLM 校验与出题 -----


def _validate_there_be_expected_answer(text: str, expected_answer: str) -> bool:
    """
    对 gp_there_is_are 的题干做最小可行的单复数启发式校验，判断 expected_answer 是否合理。
    仅处理包含 "there _____" 的句子；取 blank 后第一个词作为主语线索。
    """
    normalized_text = " ".join(text.strip().lower().split())
    if "there _____" not in normalized_text:
        return False
    suffix = normalized_text.split("there _____", 1)[1].strip()
    tokens = suffix.split()
    if not tokens:
        return False
    first_word = re.sub(r"[^a-z]", "", tokens[0])
    if first_word in {"a", "an", "one"}:
        return expected_answer == "is"
    if first_word in {"two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}:
        return expected_answer == "are"
    return True


def _validate_llm_question(obj: Any, grammar_point_id: str) -> bool:
    """
    校验 LLM 返回的题目对象是否合法且与请求的 grammar_point_id 一致。
    不通过则返回 False，调用方应 fallback 到模板。
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
    # gp_there_is_are 专用：标准 there be 句型 + 选项严格 is/are/am/be + 正确答案只能是 is 或 are + 单复数一致
    if grammar_point_id == "gp_there_is_are":
        normalized_text = " ".join(text.strip().lower().split())
        if not normalized_text.startswith("there _____"):
            return False
        if set(opts_lower) != {"is", "are", "am", "be"}:
            return False
        if exp_lower not in {"is", "are"}:
            return False
        if not _validate_there_be_expected_answer(text, exp_lower):
            return False
    return True


def _get_allowed_word_set(grammar_point_id: str, vocab_a2: list[dict]) -> set[str]:
    """从 vocab_a2 中提取该 grammar_point 对应的允许词集合（lemma + 各 form 值），小写。"""
    allowed = set()
    for item in vocab_a2:
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


def _vocab_soft_check_llm_question(obj: Any, grammar_point_id: str, vocab_a2: list[dict]) -> bool:
    """
    最小 vocab 软校验：text + options 中出现的“内容词”不应大量超出允许词表。
    """
    allowed = _get_allowed_word_set(grammar_point_id, vocab_a2) | _VOCAB_SOFT_CHECK_FUNCTION_WORDS
    text = (obj.get("text") or "")
    options = obj.get("options") or []
    combined = text + " " + " ".join(str(o) for o in options)
    words = re.findall(r"[a-zA-Z]+", combined)
    unknown = [w for w in words if len(w) >= 2 and w.lower() not in allowed]
    return len(unknown) <= 5


def _build_vocab_summary_for_gp(grammar_point_id: str, vocab_a2: list[dict]) -> str:
    """为 prompt 构建当前语法点可用的词库摘要。"""
    parts = []
    for item in vocab_a2:
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
    vocab_a2: list[dict],
    recent_texts: Optional[list[str]] = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    OpenAI-compatible API 受控出题。
    成功返回 (question_dict, None)；失败返回 (None, reason)。
    返回的 question_dict 已带 option_forms。
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        return (None, "no_api_key")
    base_url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    vocab_block = _build_vocab_summary_for_gp(grammar_point_id, vocab_a2)
    recent_block = ""
    if recent_texts:
        recent_block = "\nAvoid repeating these recently used sentences (vary subject/object/scene):\n" + "\n".join(f"- {t}" for t in recent_texts)

    system = """You are a KET/A2 English grammar question writer. Output only valid JSON, no markdown or explanation."""

    # 针对不同语法点收紧选项形式约束，提升后续规则错因分类的可用性
    extra_constraints = ""
    if grammar_point_id in ("gp_3rd_person_s", "gp_past_simple"):
        extra_constraints = """
- All 4 options must come from the same main verb.
- Options must be exactly these four forms of one verb: base, third person singular, past, bare -ing form.
- Each option must be a single word (no spaces or auxiliary verbs).
- Do not use auxiliary constructions such as "is visiting" or "was playing".
- Do not mix forms from different verbs."""
    elif grammar_point_id == "gp_there_is_are":
        extra_constraints = """
- The sentence must use the existential "there be" structure explicitly.
- The text must contain the pattern: "There _____".
- The blank must appear immediately after the word "There".
- Do not rewrite the sentence as inversion such as "In the kitchen, is ..." or "On the table, are ...".
- The four options must be exactly these words (in any order): "is", "are", "am", "be".
- If the noun phrase after the blank is singular, the correct answer must be "is".
- If the noun phrase after the blank is plural or starts with a number greater than one, the correct answer must be "are".
- Do not include any other words besides these four."""

    user = f"""Generate one multiple-choice grammar question in English for KET/A2 learners.

Grammar point ID: {grammar_point_id}
Grammar point: {label}
Description: {description}

Constraints:
- The sentence must be natural and semantically correct (no nonsense like "a room on the table").
- Use mainly words from the allowed vocab list below. You may add very common words (e.g. articles, "yesterday") if needed.
- Exactly 4 options. One correct answer. The blank in the sentence should be filled by one of the options.
- The options must strictly follow the form constraints described below for this grammar point.
{extra_constraints}
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
    if not _vocab_soft_check_llm_question(parsed, grammar_point_id, vocab_a2):
        return (None, "llm_vocab_soft_check_failed")
    question_dict = {
        "id": next_id,
        "type": "multiple_choice",
        "text": parsed["text"].strip(),
        "options": [str(x).strip() for x in parsed["options"]],
        "grammar_point": label,
        "grammar_point_id": grammar_point_id,
        "expected_answer": str(parsed["expected_answer"]).strip(),
    }
    option_forms = _infer_option_forms_for_question(
        question_dict, grammar_point_id, vocab_a2, _ing_form
    )
    # 规则优先场景：若无法为关键语法点推断出 option_forms，则判定为 LLM 题失败，交由上层 fallback 到模板题
    if grammar_point_id in ("gp_3rd_person_s", "gp_past_simple", "gp_there_is_are"):
        if not option_forms:
            return (None, "llm_missing_option_forms")
    question_dict["option_forms"] = option_forms
    return (question_dict, None)


def _generate_question_template(
    grammar_point_id: str,
    next_id: str,
    grammar_points: list[dict],
    vocab_a2: list[dict],
) -> dict[str, Any]:
    """
    模板版出题（语义约束的预定义模板），用作 LLM 失败或未配置时的 fallback。
    """
    gp_map = {gp["id"]: gp for gp in grammar_points}
    gp = gp_map.get(grammar_point_id)
    if not gp:
        grammar_point_id = grammar_points[0]["id"]
        gp = grammar_points[0]

    label = gp.get("label") or grammar_point_id

    if grammar_point_id == "gp_3rd_person_s":
        valid = [t for t in THIRD_PERSON_TEMPLATES if _verb_by_lemma(t["lemma"], vocab_a2)]
        if not valid:
            template = {"lemma": "go", "sentence": "She _____ to school every day."}
            verb = _verb_by_lemma("go", vocab_a2) or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
        else:
            template = random.choice(valid)
            verb = _verb_by_lemma(template["lemma"], vocab_a2)
            if not verb:
                verb = _verb_by_lemma("go", vocab_a2) or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
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
            "option_forms": _build_option_forms_for_verb_question(base, third, past, ing),
        }

    if grammar_point_id == "gp_past_simple":
        valid = [t for t in PAST_SIMPLE_TEMPLATES if _verb_by_lemma(t["lemma"], vocab_a2)]
        if not valid:
            template = {"lemma": "go", "sentence": "Yesterday she _____ to the park."}
            verb = _verb_by_lemma("go", vocab_a2) or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
        else:
            template = random.choice(valid)
            verb = _verb_by_lemma(template["lemma"], vocab_a2)
            if not verb:
                verb = _verb_by_lemma("go", vocab_a2) or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
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
            "option_forms": _build_option_forms_for_verb_question(base, third, past, ing),
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
            "option_forms": _build_option_forms_for_there_be(),
        }

    return _generate_question_template(grammar_points[0]["id"], next_id, grammar_points, vocab_a2)


def generate_remedial_question(
    grammar_point_id: str,
    error_type: str,
    parent_question_id: str,
    next_id: str,
    grammar_points: list[dict],
    vocab_a2: list[dict],
) -> dict[str, Any]:
    """
    Phase 2 Step 3：根据语法点与错因生成一道补救题。
    Step 3.5：题目对象内增加 is_remedial_question=True、parent_question_id。
    """
    _remedial_meta = {"is_remedial_question": True, "parent_question_id": parent_question_id}
    gp_map = {gp["id"]: gp for gp in grammar_points}
    gp = gp_map.get(grammar_point_id)
    if not gp:
        grammar_point_id = grammar_points[0]["id"]
        gp = grammar_points[0]
    label = gp.get("label") or grammar_point_id

    if grammar_point_id == "gp_3rd_person_s":
        valid = [t for t in THIRD_PERSON_TEMPLATES if _verb_by_lemma(t["lemma"], vocab_a2)]
        if not valid:
            valid = [{"lemma": "go", "sentence": "She _____ to school every day."}]
        if error_type == "missing_3rd_person_s":
            pool = [t for t in valid if "every day" in t.get("sentence", "") or "every week" in t.get("sentence", "")]
            if not pool:
                pool = valid
        elif error_type == "chose_past_instead_of_present":
            pool = [t for t in valid if "every day" in t.get("sentence", "") or "every week" in t.get("sentence", "")]
            if not pool:
                pool = valid
        else:
            pool = valid
        template = random.choice(pool)
        verb = _verb_by_lemma(template["lemma"], vocab_a2) or _verb_by_lemma("go", vocab_a2) or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
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
            "option_forms": _build_option_forms_for_verb_question(base, third, past, ing),
            **_remedial_meta,
        }

    if grammar_point_id == "gp_past_simple":
        valid = [t for t in PAST_SIMPLE_TEMPLATES if _verb_by_lemma(t["lemma"], vocab_a2)]
        if not valid:
            valid = [{"lemma": "go", "sentence": "Yesterday she _____ to the park."}]
        if error_type == "used_base_instead_of_past":
            pool = [t for t in valid if "yesterday" in t.get("sentence", "") or "last" in t.get("sentence", "")]
            if not pool:
                pool = valid
        else:
            pool = valid
        template = random.choice(pool)
        verb = _verb_by_lemma(template["lemma"], vocab_a2) or _verb_by_lemma("go", vocab_a2) or {"forms": {"base": "go", "third_person": "goes", "past": "went"}}
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
            "option_forms": _build_option_forms_for_verb_question(base, third, past, ing),
            **_remedial_meta,
        }

    if grammar_point_id == "gp_there_is_are":
        if error_type == "singular_plural_mismatch":
            pool = [e for e in THERE_BE_SENTENCES if "two " in e.get("text", "") or "a " in e.get("text", "")]
            if not pool:
                pool = THERE_BE_SENTENCES
        elif error_type == "chose_am_or_be_instead_of_is_are":
            pool = THERE_BE_SENTENCES
        else:
            pool = THERE_BE_SENTENCES
        entry = random.choice(pool)
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
            "option_forms": _build_option_forms_for_there_be(),
            **_remedial_meta,
        }

    q = _generate_question_template(grammar_point_id, next_id, grammar_points, vocab_a2)
    q["is_remedial_question"] = True
    q["parent_question_id"] = parent_question_id
    return q


def _append_recent_text(
    grammar_point_id: str,
    text: str,
    recent_question_texts: dict[str, list[str]],
    max_size: int,
) -> None:
    """将题目文本加入该语法点的最近列表并保持最多 max_size 条。"""
    rec = recent_question_texts.setdefault(grammar_point_id, [])
    rec.append(text.strip())
    recent_question_texts[grammar_point_id] = rec[-max_size:]


def generate_question(
    grammar_point_id: str,
    grammar_points: list[dict],
    vocab_a2: list[dict],
    recent_question_texts: dict[str, list[str]],
    next_id: str,
    max_recent: int,
    has_llm_key: bool,
) -> dict[str, Any]:
    """
    优先走 LLM 受控出题；失败或未配置 API 时 fallback 到模板出题。
    同一 grammar_point 下会避免与最近若干题题干重复（重试一次或 fallback）。
    """
    gp_map = {gp["id"]: gp for gp in grammar_points}
    gp = gp_map.get(grammar_point_id)
    if not gp:
        grammar_point_id = grammar_points[0]["id"]
        gp = grammar_points[0]

    recent = recent_question_texts.get(grammar_point_id, [])

    if not has_llm_key:
        q = _generate_question_template(grammar_point_id, next_id, grammar_points, vocab_a2)
        q["source"] = "template"
        q["_debug_reason"] = "no_api_key"
        _append_recent_text(grammar_point_id, q["text"], recent_question_texts, max_recent)
        print(f"[Phase 1.5] question source=template reason=no_api_key id={next_id}", flush=True)
        return q

    label = gp.get("label") or grammar_point_id
    description = gp.get("description") or ""
    try:
        llm_q, reason = _generate_question_llm(
            grammar_point_id, next_id, label, description,
            vocab_a2, recent_texts=recent,
        )
        if llm_q is not None:
            text = llm_q["text"].strip()
            if text in recent:
                llm_q2, reason2 = _generate_question_llm(
                    grammar_point_id, next_id, label, description,
                    vocab_a2, recent_texts=recent,
                )
                if llm_q2 is not None and llm_q2["text"].strip() not in recent:
                    llm_q = llm_q2
                    llm_q["source"] = "llm"
                    llm_q["_debug_reason"] = None
                    _append_recent_text(grammar_point_id, llm_q["text"], recent_question_texts, max_recent)
                    print(f"[Phase 1.5] question source=llm id={next_id} (after duplicate retry)", flush=True)
                    return llm_q
                q = _generate_question_template(grammar_point_id, next_id, grammar_points, vocab_a2)
                q["source"] = "template"
                q["_debug_reason"] = "llm_duplicate_text"
                _append_recent_text(grammar_point_id, q["text"], recent_question_texts, max_recent)
                print(f"[Phase 1.5] question source=template reason=llm_duplicate_text id={next_id}", flush=True)
                return q
            llm_q["source"] = "llm"
            llm_q["_debug_reason"] = None
            _append_recent_text(grammar_point_id, llm_q["text"], recent_question_texts, max_recent)
            print(f"[Phase 1.5] question source=llm id={next_id}", flush=True)
            return llm_q
        fallback_reason = reason or "llm_request_failed"
        q = _generate_question_template(grammar_point_id, next_id, grammar_points, vocab_a2)
        q["source"] = "template"
        q["_debug_reason"] = fallback_reason
        _append_recent_text(grammar_point_id, q["text"], recent_question_texts, max_recent)
        print(f"[Phase 1.5] question source=template reason={fallback_reason} id={next_id}", flush=True)
        return q
    except Exception as e:
        q = _generate_question_template(grammar_point_id, next_id, grammar_points, vocab_a2)
        q["source"] = "template"
        q["_debug_reason"] = "llm_request_failed"
        _append_recent_text(grammar_point_id, q["text"], recent_question_texts, max_recent)
        print(
            f"[Phase 1.5] question source=template reason=llm_request_failed (exception: {type(e).__name__}) id={next_id}",
            flush=True,
        )
        return q
