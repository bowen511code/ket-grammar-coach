# KET Grammar Coach — 架构说明初稿

## 1. 系统概览

- **前端**：Next.js（App Router）+ TypeScript + Tailwind，部署为静态/Node 服务均可。
- **后端**：FastAPI，提供 REST API，可连接 PostgreSQL。
- **数据库**：PostgreSQL，MVP 用于存储题目与作答记录（可选；初期可用内存或硬编码数据跑通）。

数据流简要：
- 浏览器 → Next.js（前端路由、页面）→ HTTP 请求 → FastAPI → PostgreSQL（可选）。

当前后端已实现基础健康检查接口（`GET /health`），其余业务接口将按 [docs/api-spec.md](api-spec.md) 逐步实现。

## 2. 目录与职责

| 目录/文件 | 职责 |
|-----------|------|
| `web/` | Next.js 应用：demo 入口页与学生端/教师端页面（`/demo/student`、`/demo/teacher`），调用后端 API。 |
| `app/` | FastAPI 应用：实现 api-spec 中的接口，连接 DB（若启用）。 |
| `docs/` | 项目文档：PRD、API 约定、架构说明，便于展示与评审。 |
| `docker-compose.yml` | 本地开发用 PostgreSQL（可选）；可按需增加 backend 服务。 |

## 3. 技术选型理由（简述）

- **Next.js**：用于构建 demo 前端页面与路由，适合快速搭建可展示的产品原型，部署简单（如 Vercel）。
- **FastAPI**：与前端分离、接口清晰、自动文档（/docs），便于联调与讲解。
- **PostgreSQL**：关系型存储题目与 attempts，扩展性好；MVP 可最小表结构。

## 4. 数据流（MVP）

以下数据流描述为 MVP 目标链路，当前部分接口仍在逐步实现中。

- **学生做题**：前端 `GET /api/question` → 展示题目 → 用户填答；`POST /api/hint` 要提示；`POST /api/submit` 提交 → 展示 correct / feedback / expected_answer。
- **教师查看**：前端 `GET /api/attempts` → 展示列表（表格或卡片）。

## 5. 部署与运行

- 前端：`cd web && npm i && npm run dev`（开发）；`npm run build && npm run start`（生产）。
- 后端：`cd app && pip install -r requirements.txt && uvicorn main:app --reload --port 8000`。
- 数据库：`docker compose up -d` 后配置 `app/.env` 中的 DB 连接。

---

*本文档为初稿，后续可补充“环境变量清单”“部署清单”或示意图。*
