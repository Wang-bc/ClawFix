# ClawFix

ClawFix 是一个面向故障分析与修复协作场景的多 Agent 智能助手项目，提供 Web 控制台与飞书接入能力，并结合 LLM、混合检索、会话记忆与案例沉淀机制，帮助团队把“问题描述 -> 证据检索 -> 诊断结论 -> 修复归档”串成一条可复用的工作流。

## 项目特点

- 多 Agent 协作诊断：内置 `coordinator`、`internal_retriever`、`external_researcher`、`evidence_judge` 四类 Agent。
- 混合检索：结合 `SQLite FTS5` 关键词检索与 `Qdrant` 向量检索，并支持增量索引。
- 多入口接入：支持 Web 控制台、飞书事件回调与飞书长连接接入。
- 案例与知识库管理：支持列出、导入、删除知识文档，并将已完成问题沉淀为案例。
- 会话持久化：会话记录以 JSONL 保存，支持摘要压缩、Durable Memory 与会话回放。
- 可靠投递：回答先落盘再发送，支持重试和死信目录。
- 本地优先部署：可直接本机运行，也可通过 Docker Compose 启动应用与 Qdrant。

## 适用场景

- 企业内部故障诊断助手
- 研发团队知识库问答与案例复盘
- 接入 LLM 的 Agent 系统原型验证
- 需要保留诊断证据链的运维/技术支持场景

## 技术栈

- Python 3.10+
- FastAPI + Uvicorn
- OpenAI-compatible LLM API
- Qdrant
- Tavily Web Search
- 原生 HTML/CSS/JavaScript 前端控制台

## 核心架构

1. `Web / Feishu Channel`
负责接收用户输入，统一进入应用层。

2. `Gateway & Runtime`
负责消息标准化、路由、Agent 执行编排、流式返回和投递。

3. `Diagnostic Engine`
由协调 Agent 驱动子 Agent 获取内部证据与外部证据，并整合出最终结构化诊断结果。

4. `Retrieval Layer`
使用 `SQLite FTS5 + Qdrant` 组成混合检索层，支持 `knowledge`、`cases`、`session_memory` 三类检索域。

5. `Workspace`
保存 Agent 提示词、知识文档、运行时会话数据、案例沉淀与记忆文件。

## 目录结构

```text
.
|-- app/                    # 后端应用
|-- docs/                   # 项目文档
|-- public/                 # Web 控制台静态资源
|-- tests/                  # 单元测试与集成测试
|-- workspace/              # Agent 工作区、知识与运行数据
|   |-- agents/             # Agent 提示词与身份定义
|   |-- knowledge/          # 知识文档
|   |-- memory/             # 长期/人工维护记忆
|   |-- .sessions/          # 运行时会话数据（已忽略）
|   |-- .delivery/          # 投递队列与死信（已忽略）
|   |-- .index/             # 检索索引（已忽略）
|   |-- .qdrant/            # 本地向量库（已忽略）
|-- Dockerfile
|-- docker-compose.yml
|-- requirements.txt
```

## 快速开始

### 1. 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，至少补充以下配置：

```env
OPENAI_API_KEY=your_api_key
ENABLE_LLM=true
ENABLE_VECTOR_SEARCH=true
QDRANT_MODE=local
```

然后启动服务：

```powershell
python -m app.main
```

访问：`http://127.0.0.1:3000`

### 2. Windows 便捷启动

如果本机已正确安装 Python，也可以直接运行：

```powershell
.\run.ps1
```

### 3. Docker Compose

```powershell
docker compose up --build
```

默认暴露端口：

- App: `3000`
- Qdrant HTTP: `6333`
- Qdrant gRPC: `6334`

## 关键配置说明

### LLM

- `ENABLE_LLM`: 是否启用 LLM 能力。
- `OPENAI_API_KEY`: LLM 服务密钥。
- `OPENAI_BASE_URL`: OpenAI 兼容接口地址。
- `OPENAI_MODEL`: 主模型。
- `OPENAI_SUMMARY_MODEL`: 会话摘要模型。
- `LLM_API_STYLE`: `auto`、`responses`、`chat_completions`。

### 检索

- `ENABLE_VECTOR_SEARCH`: 是否启用向量检索。
- `QDRANT_MODE`: `auto`、`local`、`remote`。
- `QDRANT_URL`: 远程 Qdrant 地址。
- `QDRANT_LOCAL_PATH`: 本地 Qdrant 存储路径。
- `VECTOR_TOP_K`、`LEXICAL_TOP_K`、`RERANK_TOP_N`: 召回与重排参数。

### Web Search

- `ENABLE_WEB_SEARCH`: 是否允许外部 Web 搜索。
- `TAVILY_API_KEY`: Tavily 密钥。
- `WEB_SEARCH_TIMEOUT_S`: 外部搜索超时时间。

### 飞书

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_CONNECTION_MODE`

## API 概览

### 系统与 Web

- `GET /api/health`
- `POST /api/web/chat`
- `POST /api/web/chat/stream`
- `POST /api/web/finalize`
- `GET /api/sessions`
- `GET /api/session`
- `POST /api/sessions/delete`

### 案例与知识库

- `GET /api/cases`
- `POST /api/cases/delete`
- `GET /api/knowledge`
- `POST /api/knowledge/import`
- `POST /api/knowledge/delete`

### 飞书

- `POST /api/feishu/events`

## 测试

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

## 仓库发布建议

公开到 GitHub 前，建议重点确认以下事项：

- 不要提交 `.env`、会话记录、向量索引、投递缓存、案例产物等运行时数据。
- 检查 `docs/interview/`、个人文档、截图、数据库等是否属于仅本地使用材料。
- 如果打算开源，请明确选择 `LICENSE`；如果没有许可证，默认并不是开源授权。
- 如果计划接受外部贡献，先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md) 并完善维护流程。
- 如果仓库会公开，请将漏洞反馈方式写入 [SECURITY.md](./SECURITY.md)。

更详细的发布步骤见 [docs/github-publish-checklist.md](./docs/github-publish-checklist.md)。

## 常见问题

### 没有配置 LLM 能运行吗？

可以。项目会退回到更保守的非 LLM 模式，但诊断质量和检索能力会受到明显影响。

### 可以不使用远程 Qdrant 吗？

可以。默认支持本地持久化模式，数据会落在 `workspace/.qdrant/`。

### 哪些 `workspace` 内容适合提交到 Git？

建议保留以下内容：

- `workspace/agents/`
- `workspace/BOOTSTRAP.md`、`IDENTITY.md`、`TOOLS.md` 等提示词与规范文件
- 手工维护且适合公开的知识文档

建议忽略以下内容：

- `workspace/.sessions/`
- `workspace/.delivery/`
- `workspace/.index/`
- `workspace/.qdrant/`
- `workspace/cases/`
- `workspace/knowledge/uploads/`
- `workspace/memory/daily/`

## 相关文档

- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [SECURITY.md](./SECURITY.md)
- [docs/github-publish-checklist.md](./docs/github-publish-checklist.md)
- [docs/architecture/overview.md](./docs/architecture/overview.md)

## License

当前仓库未自动附带 `LICENSE` 文件。

如果你准备公开开源，请在发布前明确选择许可证，例如 `MIT`、`Apache-2.0` 或 `GPL-3.0`。未附带许可证时，外部用户默认没有复制、修改和分发权限。
