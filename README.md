# KET Grammar Coach

KET Grammar Coach 是一个 AI 语法练习 Demo，用于展示一个最小可运行的语法练习产品流程：

- **学生端**：获取题目、请求提示、提交答案
- **教师端**：查看作答记录

## 技术栈

| 层级     | 技术 |
|----------|------|
| 前端     | Next.js（App Router）、TypeScript、Tailwind CSS |
| 后端     | FastAPI |
| 数据库   | PostgreSQL（可选，MVP 可先用内存数据） |

## 路由

- `/` — Demo 入口
- `/demo/student` — 学生端 Demo
- `/demo/teacher` — 教师端 Demo

## 系统架构

前端 Demo（Next.js）通过 HTTP 调用 FastAPI 后端 API，后端可连接 PostgreSQL 存储题目与作答记录。详见 [docs/architecture.md](docs/architecture.md)。

## Current MVP Status

- 本地最小闭环已验证通过（学生端题目/提示/提交 + 教师端作答记录）。
- Student / Teacher Demo 已可交互，验收标准见 [docs/mvp-validation.md](docs/mvp-validation.md)。
- 更高级能力（LangGraph、动态出题、错因分类、补救练习、指标体系等）仍在后续 roadmap 中。

## 本地运行

**环境**：Node.js、Python 3、可选 Docker（仅数据库）。

### 1. 后端（默认端口 8001）

```bash
cd app
pip install -r requirements.txt
uvicorn main:app --reload --port 8001
```

### 2. 前端（默认端口 3000）

```bash
cd web
npm install
npm run dev
```

浏览器访问 **http://localhost:3000**。前端请求后端时需保证 API 地址为 `http://localhost:8001`（可通过 `web/.env.local` 配置 `NEXT_PUBLIC_API_URL`）。

### 3. 数据库（可选）

```bash
docker compose up -d
```

随后在 `app/.env` 中配置 DB 连接（可参考 `app/.env.example`）。

---

更多说明见 [docs/](docs/)：
- PRD：docs/prd.md
- API 约定：docs/api-spec.md
- 架构说明：docs/architecture.md

该项目可以作为独立产品 Demo，被个人作品集网站外链展示。
