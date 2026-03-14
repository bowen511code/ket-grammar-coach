# KET Grammar Coach — API 约定

后端 Base URL 示例：`http://localhost:8000`。以下为最小可用接口与 JSON 示例。

---

## 1. GET /health

**用途**：存活与依赖检查（部署、健康探测）。

**请求**：无 body，无必填 query。

**响应**：`200 OK`

```json
{
  "status": "ok"
}
```

可选：若校验数据库，可增加字段 `"db": "ok"`。

---

## 2. GET /api/question

**用途**：获取一道题目（学生端展示）。

**请求**：无 body。Query 可选：`level`（难度/级别）。

**示例**：`GET /api/question` 或 `GET /api/question?level=A1`

**响应**：`200 OK`

```json
{
  "id": "q_001",
  "type": "multiple_choice",
  "text": "She _____ to school every day.",
  "options": ["go", "goes", "going", "went"],
  "grammar_point": "present simple, third person -s"
}
```

题目类型若为填空而非选择，可省略 `options`，用 `blank_position` 等字段；MVP 可先固定一种题型。

---

## 3. POST /api/hint

**用途**：请求提示（扣分或仅记录，由业务决定）。

**请求**：`Content-Type: application/json`

```json
{
  "question_id": "q_001"
}
```

**响应**：`200 OK`

```json
{
  "hint": "注意主语是第三人称单数，谓语要用什么形式？"
}
```

---

## 4. POST /api/submit

**用途**：提交答案并返回对错与反馈。

**请求**：`Content-Type: application/json`

```json
{
  "question_id": "q_001",
  "answer": "goes"
}
```

**响应**：`200 OK`

```json
{
  "question_id": "q_001",
  "correct": true,
  "feedback": "答对了！",
  "expected_answer": "goes"
}
```

错误示例：

```json
{
  "question_id": "q_001",
  "correct": false,
  "feedback": "正确答案是第三人称单数形式。",
  "expected_answer": "goes"
}
```

字段约定：
- **correct**：布尔，是否答对。
- **feedback**：字符串，给学生的反馈文案。
- **expected_answer**：字符串，标准答案（便于前端展示或教师端查看）。

---

## 5. GET /api/attempts

**用途**：教师端查看作答记录列表。

**请求**：无 body。Query 可选：`limit`（默认如 20）。

**示例**：`GET /api/attempts` 或 `GET /api/attempts?limit=50`

**响应**：`200 OK`

```json
[
  {
    "id": "att_001",
    "question_id": "q_001",
    "answer": "goes",
    "correct": true,
    "created_at": "2026-03-14T10:00:00Z"
  },
  {
    "id": "att_002",
    "question_id": "q_001",
    "answer": "go",
    "correct": false,
    "created_at": "2026-03-14T10:05:00Z"
  }
]
```

默认按 `created_at` 倒序返回，最新记录在前。MVP 可不做分页，仅返回最近 N 条；时间格式建议 ISO 8601。

---

## 错误响应

接口异常时建议统一格式（如 `4xx` / `5xx`）：

```json
{
  "detail": "错误描述信息"
}
```

或使用 FastAPI 默认的 `{"detail": [...]}`。业务错误（如题目不存在）可返回 `404` 或 `400` 并带 `detail`。

---

## 版本与变更

- 当前为 **MVP 初版**，题目与 attempts 可为示例或单表存储。
- 后续若增删字段或路径，请在此文档中同步更新并注明日期。

---

## 当前实现状态

- 当前后端已实现：`GET /health`
- 其余接口为 MVP 约定，后续将按本文档逐步实现
