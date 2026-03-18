"""
Phase 3 Step 1：学习进度统计。
根据 attempts 与 grammar_points 聚合为教师端概览数据，不依赖 FastAPI，不入库。
"""
from datetime import datetime, timezone
from typing import Any, Optional


def build_progress_summary(
    attempts: list[dict],
    grammar_points: list[dict],
    grammar_point_id: Optional[str] = None,
    time_range: str = "all",
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    recent_n: int = 10,
) -> dict[str, Any]:
    """
    将作答记录聚合为学习进度概览。
    - attempts：作答记录列表，单条含 correct, is_remedial, grammar_point_id, error_type, error_label 等
    - grammar_points：语法点列表，每项含 id, label
    返回 overall + by_grammar_point + diagnostics 结构，供 GET /api/progress_summary 使用。
    Phase 3 Step 2 新增 diagnostics：最近 N 次作答、错误趋势、最薄弱 Top N 语法点。
    """
    # ----- attempts 过滤（Phase 3 后续增强 Step 1） -----
    filtered_attempts = attempts
    if time_range != "all":
        now = datetime.now(timezone.utc)
        today = now.date()
        # 以 UTC 周一为一周起点
        week_start = today.fromordinal(today.toordinal() - today.weekday())

        tmp = []
        for a in filtered_attempts:
            created_at = a.get("created_at")
            if not created_at:
                continue
            try:
                dt = datetime.strptime(str(created_at), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            d = dt.date()
            if time_range == "today":
                if d == today:
                    tmp.append(a)
            elif time_range == "this_week":
                if d >= week_start and d <= today:
                    tmp.append(a)
        filtered_attempts = tmp

    if grammar_point_id:
        filtered_attempts = [a for a in filtered_attempts if a.get("grammar_point_id") == grammar_point_id]

    grammar_points_iter = grammar_points
    if grammar_point_id:
        grammar_points_iter = [gp for gp in grammar_points if gp.get("id") == grammar_point_id]

    # ----- overall -----
    total_attempts = len(filtered_attempts)
    correct_attempts = sum(1 for a in filtered_attempts if a.get("correct") is True)
    accuracy = (correct_attempts / total_attempts) if total_attempts else 0.0

    normal_attempts = sum(1 for a in filtered_attempts if not a.get("is_remedial"))
    remedial_attempts = sum(1 for a in filtered_attempts if a.get("is_remedial"))
    normal_correct_attempts = sum(
        1 for a in filtered_attempts if not a.get("is_remedial") and a.get("correct") is True
    )
    remedial_correct_attempts = sum(
        1 for a in filtered_attempts if a.get("is_remedial") and a.get("correct") is True
    )
    normal_accuracy = (normal_correct_attempts / normal_attempts) if normal_attempts else 0.0
    remedial_accuracy = (remedial_correct_attempts / remedial_attempts) if remedial_attempts else 0.0

    overall = {
        "total_attempts": total_attempts,
        "correct_attempts": correct_attempts,
        "accuracy": round(accuracy, 4),
        "normal_attempts": normal_attempts,
        "remedial_attempts": remedial_attempts,
        "normal_correct_attempts": normal_correct_attempts,
        "remedial_correct_attempts": remedial_correct_attempts,
        "normal_accuracy": round(normal_accuracy, 4),
        "remedial_accuracy": round(remedial_accuracy, 4),
    }

    # ----- by_grammar_point -----
    by_grammar_point = []
    for gp in grammar_points_iter:
        gp_id = gp["id"]
        gp_label = gp.get("label") or gp_id
        gp_attempts = [a for a in filtered_attempts if a.get("grammar_point_id") == gp_id]
        gp_total = len(gp_attempts)
        gp_correct = sum(1 for a in gp_attempts if a.get("correct") is True)
        gp_accuracy = (gp_correct / gp_total) if gp_total else 0.0
        wrong_attempts = gp_total - gp_correct

        normal_attempts = sum(1 for a in gp_attempts if not a.get("is_remedial"))
        remedial_attempts = sum(1 for a in gp_attempts if a.get("is_remedial"))
        normal_correct_attempts = sum(
            1 for a in gp_attempts if not a.get("is_remedial") and a.get("correct") is True
        )
        remedial_correct_attempts = sum(
            1 for a in gp_attempts if a.get("is_remedial") and a.get("correct") is True
        )
        normal_accuracy = (normal_correct_attempts / normal_attempts) if normal_attempts else 0.0
        remedial_accuracy = (remedial_correct_attempts / remedial_attempts) if remedial_attempts else 0.0

        # top_error_types：仅统计答错且 error_type 非空的记录；error_label 缺失或为空时回退用 error_type 作为 error_label，按 count 降序，最多 3 个
        error_counts: dict[tuple[str, str], int] = {}
        for a in gp_attempts:
            if a.get("correct") is True:
                continue
            et = a.get("error_type")
            if et is None:
                continue
            et = str(et).strip()
            if not et:
                continue
            el = a.get("error_label")
            error_label = (str(el).strip() if el is not None else "") or et
            key = (et, error_label)
            error_counts[key] = error_counts.get(key, 0) + 1
        sorted_errors = sorted(
            [{"error_type": k[0], "error_label": k[1], "count": v} for k, v in error_counts.items()],
            key=lambda x: -x["count"],
        )[:3]

        if gp_total == 0:
            continue
        by_grammar_point.append({
            "grammar_point_id": gp_id,
            "grammar_point_label": gp_label,
            "total_attempts": gp_total,
            "correct_attempts": gp_correct,
            "accuracy": round(gp_accuracy, 4),
            "wrong_attempts": wrong_attempts,
            "normal_attempts": normal_attempts,
            "remedial_attempts": remedial_attempts,
            "normal_correct_attempts": normal_correct_attempts,
            "remedial_correct_attempts": remedial_correct_attempts,
            "normal_accuracy": round(normal_accuracy, 4),
            "remedial_accuracy": round(remedial_accuracy, 4),
            "top_error_types": sorted_errors,
        })

    # 保留未做通用排序的基础列表，确保 weakest_grammar_points 不受 sort_by/sort_order 间接影响
    by_grammar_point_base = list(by_grammar_point)

    # sort_by / sort_order 只影响 by_grammar_point 返回顺序
    if sort_by in ("accuracy", "wrong_attempts"):
        reverse = (sort_order == "desc")
        by_grammar_point = sorted(by_grammar_point, key=lambda x: x[sort_by], reverse=reverse)

    # ----- diagnostics（Phase 3 Step 2：趋势与诊断增强） -----
    # recent_n 只影响“最近表现”相关部分，不影响 overall / by_grammar_point 的统计口径
    if not isinstance(recent_n, int) or recent_n <= 0:
        recent_n = 10
    recent_window_size = recent_n
    weakest_top_n = 3
    recent = filtered_attempts[-recent_window_size:] if len(filtered_attempts) >= recent_window_size else filtered_attempts
    recent_total = len(recent)
    recent_correct = sum(1 for a in recent if a.get("correct") is True)
    recent_wrong = recent_total - recent_correct
    recent_accuracy = (recent_correct / recent_total) if recent_total else 0.0
    recent_attempts = {
        "total_attempts": recent_total,
        "correct_attempts": recent_correct,
        "wrong_attempts": recent_wrong,
        "accuracy": round(recent_accuracy, 4),
    }
    recent_error_trend = [
        {
            "index": i + 1,
            "attempt_id": a.get("id", ""),
            "correct": bool(a.get("correct")),
            "is_error": not a.get("correct"),
        }
        for i, a in enumerate(recent)
    ]
    # 正确率趋势：按窗口内顺序计算「累计正确率」
    accuracy_trend = []
    running_correct = 0
    for i, a in enumerate(recent):
        if a.get("correct") is True:
            running_correct += 1
        running_accuracy = running_correct / (i + 1)
        accuracy_trend.append({"index": i + 1, "accuracy": round(running_accuracy, 4)})

    # 补救题效果趋势：仅基于 recent 窗口内的补救题，按顺序计算「累计补救题正确率」
    remedial_effect_trend = []
    remedial_correct = 0
    remedial_seen = 0
    for a in recent:
        if a.get("is_remedial") is not True:
            continue
        remedial_seen += 1
        correct = bool(a.get("correct"))
        if correct:
            remedial_correct += 1
        remedial_accuracy = remedial_correct / remedial_seen
        remedial_effect_trend.append(
            {"index": remedial_seen, "correct": correct, "accuracy": round(remedial_accuracy, 4)}
        )
    # 薄弱优先：accuracy 升序，wrong_attempts 降序，total_attempts 降序
    # 注意：这里使用固定规则，不受 sort_by/sort_order 的影响
    weakest_grammar_points = sorted(
        by_grammar_point_base,
        key=lambda x: (x["accuracy"], -x["wrong_attempts"], -x["total_attempts"]),
    )[:weakest_top_n]
    diagnostics = {
        "recent_window_size": recent_window_size,
        "recent_attempts": recent_attempts,
        "recent_error_trend": recent_error_trend,
        "accuracy_trend": accuracy_trend,
        "remedial_effect_trend": remedial_effect_trend,
        "weakest_grammar_points": weakest_grammar_points,
    }

    return {
        "overall": overall,
        "by_grammar_point": by_grammar_point,
        "diagnostics": diagnostics,
    }
