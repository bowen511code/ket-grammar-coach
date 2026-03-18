"""
Phase 2：错因分类（规则优先 + LLM fallback）。
典型自检示例（人工验证用）：
  gp_3rd_person_s: expected=goes, answer=go   -> missing_3rd_person_s
  gp_past_simple:  expected=went, answer=goes -> used_3rd_person_instead_of_past
  gp_there_is_are: expected=are,  answer=is    -> singular_plural_mismatch
后续 Step 2 将在 POST /api/submit 答错分支中调用 classify_error，并将 error_type / error_label 返回前端。
"""
import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

ERROR_TYPE_LABELS: dict[str, str] = {
    # gp_3rd_person_s
    "missing_3rd_person_s": "漏加第三人称单数 -s/-es",
    "chose_past_instead_of_present": "误选过去式而非现在时",
    "chose_ing_instead_of_finite": "误选 -ing 形式而非谓语动词形式",
    # gp_past_simple
    "used_base_instead_of_past": "用了动词原形而非过去式",
    "used_3rd_person_instead_of_past": "用了第三人称单数而非过去式",
    "used_ing_instead_of_past": "用了 -ing 形式而非过去式",
    # gp_there_is_are
    "singular_plural_mismatch": "单复数与主语不一致",
    "chose_am_or_be_instead_of_is_are": "误选 am/be 而非 is/are",
    # fallback
    "unknown": "未分类错误",
}


def _build_option_forms_for_verb_question(base: str, third_person: str, past: str, ing: str) -> dict[str, str]:
    """构建动词题选项→形式映射，key 为小写。"""
    return {
        base.strip().lower(): "base",
        third_person.strip().lower(): "third_person",
        past.strip().lower(): "past",
        ing.strip().lower(): "ing",
    }


def _build_option_forms_for_there_be() -> dict[str, str]:
    """there is/are 题固定选项→形式映射。"""
    return {"is": "singular", "are": "plural", "am": "am", "be": "be"}


def _infer_verb_form_per_option(
    options: list[str],
    grammar_point_id: str,
    vocab_a2: list[dict],
    ing_form: Callable[[str], str],
) -> dict[str, str]:
    """
    根据题目选项与词库，推断每个选项对应的动词形式（base / third_person / past / ing）。
    用于 classify_error 时区分「选成原形/过去式/-ing」等。
    若无法匹配词库则返回空 dict。
    """
    if not options or len(options) != 4:
        return {}
    opts_lower = [str(o).strip().lower() for o in options if o is not None]
    if len(opts_lower) != 4:
        return {}
    opts_set = set(opts_lower)
    for item in vocab_a2:
        if item.get("part_of_speech") != "verb":
            continue
        tags = item.get("tags") or []
        if grammar_point_id not in tags:
            continue
        forms = item.get("forms") or {}
        base = (forms.get("base") or "").strip().lower()
        third = (forms.get("third_person") or "").strip().lower()
        past = (forms.get("past") or "").strip().lower()
        if not base or not third or not past:
            continue
        ing = ing_form(base).lower()
        if opts_set == {base, third, past, ing}:
            return {
                base: "base",
                third: "third_person",
                past: "past",
                ing: "ing",
            }
    return {}


def _infer_option_forms_for_question(
    question: dict[str, Any],
    grammar_point_id: str,
    vocab_a2: list[dict],
    ing_form: Callable[[str], str],
) -> dict[str, str]:
    """为题目推断 option_forms（如 LLM 题无现成字段时）。无法推断时返回空 dict。"""
    options = question.get("options") or []
    if not isinstance(options, list) or len(options) != 4:
        return {}
    gp = (grammar_point_id or "").strip()
    if gp == "gp_there_is_are":
        opts_set = set(str(o).strip().lower() for o in options if o is not None)
        if opts_set == {"is", "are", "am", "be"}:
            return _build_option_forms_for_there_be()
        return {}
    if gp in ("gp_3rd_person_s", "gp_past_simple"):
        return _infer_verb_form_per_option(options, gp, vocab_a2, ing_form)
    return {}


def _allowed_error_types_for_gp(grammar_point_id: str) -> frozenset[str]:
    """每个语法点允许的 error_type 集合（含 unknown）。"""
    allowed = {
        "gp_3rd_person_s": frozenset({
            "missing_3rd_person_s", "chose_past_instead_of_present", "chose_ing_instead_of_finite", "unknown",
        }),
        "gp_past_simple": frozenset({
            "used_base_instead_of_past", "used_3rd_person_instead_of_past", "used_ing_instead_of_past", "unknown",
        }),
        "gp_there_is_are": frozenset({
            "singular_plural_mismatch", "chose_am_or_be_instead_of_is_are", "unknown",
        }),
    }
    return allowed.get(grammar_point_id, frozenset({"unknown"}))


def _error_result(error_type: str) -> tuple[str, str]:
    """返回 (error_type, error_label)。"""
    label = ERROR_TYPE_LABELS.get(error_type, ERROR_TYPE_LABELS["unknown"])
    return (error_type, label)


def _classify_error_by_rules(
    grammar_point_id: str,
    student_answer: str,
    expected_answer: str,
    question: dict[str, Any],
) -> tuple[str, str]:
    """
    规则优先错因分类：使用 question.option_forms 判定。
    若 option_forms 不足或无法命中规则则返回 unknown。
    """
    gp_id = (grammar_point_id or "").strip()
    student = (student_answer or "").strip().lower()
    expected = (expected_answer or "").strip().lower()
    option_forms = question.get("option_forms") or {}
    if not isinstance(option_forms, dict):
        option_forms = {}

    if gp_id == "gp_3rd_person_s":
        expected_form = option_forms.get(expected)
        if expected_form != "third_person":
            return _error_result("unknown")
        student_form = option_forms.get(student)
        if student_form == "base":
            return _error_result("missing_3rd_person_s")
        if student_form == "past":
            return _error_result("chose_past_instead_of_present")
        if student_form == "ing":
            return _error_result("chose_ing_instead_of_finite")
        return _error_result("unknown")

    if gp_id == "gp_past_simple":
        expected_form = option_forms.get(expected)
        if expected_form != "past":
            return _error_result("unknown")
        student_form = option_forms.get(student)
        if student_form == "base":
            return _error_result("used_base_instead_of_past")
        if student_form == "third_person":
            return _error_result("used_3rd_person_instead_of_past")
        if student_form == "ing":
            return _error_result("used_ing_instead_of_past")
        return _error_result("unknown")

    if gp_id == "gp_there_is_are":
        if student in ("am", "be"):
            return _error_result("chose_am_or_be_instead_of_is_are")
        if expected in ("is", "are") and student in ("is", "are") and student != expected:
            return _error_result("singular_plural_mismatch")
        return _error_result("unknown")

    return _error_result("unknown")


LLM_ERROR_CLASSIFY_TIMEOUT = 8


def _classify_error_llm(
    grammar_point_id: str,
    question: dict[str, Any],
    expected_answer: str,
    student_answer: str,
) -> tuple[str, str]:
    """
    仅当规则返回 unknown 时调用。返回 (error_type, error_label)。
    超时、解析失败、标签不合法则返回 unknown。
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not api_key.strip():
        return _error_result("unknown")
    base_url = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    allowed = _allowed_error_types_for_gp(grammar_point_id)
    allowed_list = sorted(allowed)
    prompt = f"""You are an English grammar error classifier. Output only valid JSON.

Grammar point: {grammar_point_id}
Question text: {question.get("text", "")}
Options: {question.get("options", [])}
Correct answer: {expected_answer}
Student's wrong answer: {student_answer}

Choose exactly one error_type from this list: {allowed_list}
If the error cannot be determined reliably, return "unknown".
Output format: {{"error_type": "<one of the listed values>"}}
Do not add explanation. Output the JSON only."""

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0,
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
        with urllib.request.urlopen(req, timeout=LLM_ERROR_CLASSIFY_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError, TypeError):
        return _error_result("unknown")
    choices = data.get("choices") or []
    if not choices:
        return _error_result("unknown")
    content = (choices[0] or {}).get("message") or {}
    if isinstance(content, dict):
        content = content.get("content") or ""
    if not isinstance(content, str):
        return _error_result("unknown")
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(l for l in lines if l.strip() and not l.strip().startswith("```"))
    try:
        parsed = json.loads(content)
        et = (parsed.get("error_type") or "").strip()
        if et not in allowed:
            return _error_result("unknown")
        return _error_result(et)
    except (json.JSONDecodeError, TypeError):
        return _error_result("unknown")


def classify_error(
    grammar_point_id: str,
    student_answer: str,
    expected_answer: str,
    question: dict[str, Any],
) -> tuple[str, str]:
    """
    规则优先 + LLM fallback。先按 option_forms 规则判，仅当返回 unknown 且配置了 API key 时再调用 LLM。
    """
    error_type, error_label = _classify_error_by_rules(
        grammar_point_id, student_answer, expected_answer, question
    )
    if error_type != "unknown":
        return (error_type, error_label)
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        return _error_result("unknown")
    error_type, error_label = _classify_error_llm(
        grammar_point_id, question, expected_answer, student_answer
    )
    if error_type != "unknown":
        print(
            f"[Phase 2] error classification fallback=llm gp={grammar_point_id} question_id={question.get('id', '')}",
            flush=True,
        )
    return (error_type, error_label)
