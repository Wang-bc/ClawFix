# ClawFix 架构说明

当前版本已经从最初的规则型 MVP 升级为“可接入真实 LLM、支持多 Agent 子会话、支持可靠投递和可选向量检索”的实现。

## 已实现模块与功能

### 1. `config/`

- `settings.py`
  - 支持自动加载 `.env`
  - 支持 Web / 飞书 / LLM / Embedding / Qdrant / Delivery / Session 压缩等配置
  - 会对 OpenAI 兼容 `base_url` 自动补齐 `/v1`
  - 支持 `QDRANT_MODE=auto|local|remote`
  - `auto` 模式下，本机直跑会优先使用本地嵌入式 Qdrant；容器内运行才优先使用远端 `qdrant` 服务
  - 支持通过 `LLM_API_STYLE=auto|responses|chat_completions` 选择协议风格
  - 支持无 `.env` 时的默认值回退

### 2. `gateway/`

- `server.py`
  - 统一提供 HTTP 入口
  - 提供 Web 页面托管、`/api/web/chat`、`/api/web/chat/stream`、`/api/web/finalize`、`/api/cases`、`/api/knowledge`、`/api/knowledge/import`、`/api/knowledge/delete`、`/api/sessions`、`/api/session`、`/api/health`
  - 接收飞书事件回调并路由到统一会话
  - 启动时初始化检索索引、LLM 客户端和 Delivery runner
- `routing.py`
  - 当前采用最简默认路由，所有入口先路由到 `coordinator`
- `inbound_pipeline.py`
  - 负责入站消息标准化、消息 ID 去重和统一 `InboundMessage` 构造

### 3. `agents/`

- `manager.py`
  - 注册 `coordinator`、`internal_retriever`、`external_researcher`
  - 每个 Agent 拥有独立的提示词工作区目录
- `workspace.py`
  - 同时支持共享 bootstrap 文件和 agent 专属 bootstrap 文件

### 4. `prompt/`

- `bootstrap_loader.py`
  - 同时加载共享提示词和 agent 专属提示词
- `builder.py`
  - 组装 system prompt、会话摘要、当前主会话记忆召回结果、技能信息和额外上下文
- `memory_recall.py`
  - 仅召回当前主会话的 `session_memory`

### 5. `llm/`

- `client.py`
  - 支持 OpenAI 兼容调用
  - 优先使用官方 Python SDK
  - 若未安装 SDK，则回退为直接 HTTP 调用
  - 支持结构化 JSON 输出和 Embedding 生成
  - 当兼容厂商不支持 `/responses` 时，会自动回退到 `/chat/completions`
  - 当配置的 embedding 模型不可用时，会自动尝试回退 embedding 模型
- `schemas.py`
  - 定义子 Agent 报告、会话摘要、最终诊断结果的 JSON Schema

### 6. `memory/`

- `search.py`
  - 支持 Markdown 文档切块
  - 支持关键词检索
  - 在配置了 LLM + Qdrant 时支持混合检索
  - 关键词索引落在 `workspace/.index/retrieval.db`，使用 `SQLite FTS5`
  - 向量索引仍使用 `Qdrant` 持久化，默认目录为 `workspace/.qdrant`
  - 检索路由为 `exact match`、`BM25(title)`、`BM25(body)`、`dense vector`
  - 多路结果使用加权 RRF 融合，再执行轻量 rerank，避免旧版线性加权的分值漂移
  - chunking 升级为结构感知切分：优先按 Markdown 标题分段，再按段落和代码块打包
  - 检索域已拆分为 `session_memory`、`cases`、`knowledge`
  - `session_memory` 只允许按当前主会话 `session_key` 检索
  - `session_memory` chunk id 绑定 `memory_id`，避免 durable memory 顺序变化导致 point 抖动
  - `cases` 与 `knowledge` 作为共享域提供给内部检索 Agent
  - `workspace/memory/daily/` 不再进入共享检索
  - `workspace/knowledge/` 支持离线知识导入，索引改为按 checksum 增量同步，不再在每次启动时全量重建
  - 支持本地 Qdrant 路径模式和远端 Qdrant 服务模式
  - 已增加相关性筛选，低质量泛匹配结果不会进入主参考依据
  - 当远端 Qdrant 不可用时，会自动回退到本地 Qdrant 存储模式
- `store.py`
  - 支持分析笔记写入、最终案例沉淀和案例列表读取
  - 支持 `workspace/knowledge/` 下知识文档的显式导入、删除与列表管理
  - daily memory 只记录主会话摘要，不再写入子 Agent 原始报告

### 7. `sessions/`

- `session_store.py`
  - 使用 JSONL 保存完整会话
  - 保存会话 meta、摘要、预览信息和 durable memory
  - 会话列表默认过滤子 Agent session，只暴露主会话给 Web / 飞书端
- `context_guard.py`
  - 控制上下文大小
  - 基于字符预算做上下文裁剪和压缩触发
- `summarizer.py`
  - 使用真实 LLM 生成稳定会话摘要
  - 未启用 LLM 时退回到启发式摘要
- `compactor.py`
  - 在阈值触发后，将旧消息压缩成结构化摘要写入 meta

### 8. `tools/`

- `memory_search` / `memory_get`
  - `memory_search` 只检索共享域 `cases + knowledge`
  - `memory_get` 提供工作区文档读取能力
- `web_search` / `web_fetch`
  - 提供轻量 Web 搜索和网页正文抓取能力
- `workspace_list` / `workspace_read`
  - 提供工作区查看能力
- `time_now`
  - 提供当前时间

### 9. `runtime/`

- `agent_loop.py`
  - 作为单轮主执行闭环
  - 负责消息先落盘、上下文装配、诊断结果收敛和事件流记录
  - 已支持把主回复按增量 chunk 流式推送给前端
  - 每轮结束后会把结构化结论提炼为当前主会话的 durable memory，并增量同步到 `session_memory`
- `sub_agents.py`
  - 已实现 `internal_retriever` 与 `external_researcher` 独立子会话运行
  - 每个子 Agent 都会把自己的用户输入和报告写入独立 session
  - `internal_retriever` 只通过共享检索工具访问 `cases + knowledge`
- `diagnostic_engine.py`
  - 已用真实 LLM 协调逻辑替代旧规则引擎
  - coordinator 负责整合两个子 Agent 报告并输出结构化诊断
  - 最终 references 只允许绑定到真实命中的内部/外部证据
  - 在未配置 LLM 时，自动退回到保守的 fallback 诊断模式

### 10. `delivery/`

- `queue.py`
  - 所有投递先落盘再发送
  - 已实现后台 Delivery runner
  - 失败后进入指数退避重试
  - 超过最大重试次数后进入死信目录 `workspace/.delivery/dead-letter/`
- `sender.py`
  - 统一管理 Web / 飞书发送器
- `chunking.py`
  - 支持消息分片发送

### 11. `channels/`

- `feishu.py`
  - 支持飞书 `challenge` 验证
  - 支持飞书文本消息事件解析
  - 支持基于 `chat_id` 的文本回发
  - 凭证来自 `.env`

### 12. `public/`

- 提供可直接访问的 Web 控制台
- 已改为聊天式界面，一个主会话对应一个聊天窗口
- 支持新建会话、加载已有主会话、流式回复、文本附件上传、案例沉淀与历史案例查看
- 助手消息优先展示结构化诊断卡片，不再额外重复渲染一份原始 Markdown
- 会话列表标题已压缩为简短名称，避免左侧窗口被长文本占满
- 发送消息后输入框会立即清空，并在首个流式 chunk 返回前展示“正在思考中”

### 13. 部署与配置

- `.env.example`
  - 提供所有关键配置项示例
- `Dockerfile`
  - 提供 Python 3.10 容器化运行环境
- `docker-compose.yml`
  - 提供 `app + qdrant` 一键启动方案

## 已完成的重点扩展

以下此前列为“扩展建议”的内容，当前均已实现：

1. 已用真实 LLM 替换 `runtime/diagnostic_engine.py` 中的规则引擎
2. 已为 Delivery 增加后台重试 runner 和死信队列
3. 已为 Session 增加更稳定的摘要压缩策略
4. 已将 `internal_retriever` / `external_researcher` 升级成独立子 Agent 会话
5. 已为 Web 端补齐会话加载、流式输出和主会话去重展示

## 当前已知问题与限制

1. 某些 OpenAI 兼容服务只支持 `responses`，但不支持 `/embeddings` 或严格 `json_schema` 输出；当前代码已增加宽松 JSON 回退和向量检索熔断，但如果仍不稳定，建议显式关闭 `ENABLE_VECTOR_SEARCH`，或切换到完整兼容的模型供应商。
2. 飞书加密事件解密尚未实现，测试时建议先用未加密事件模式。
3. 当前 Delivery runner 采用本地文件轮询实现，适合 MVP 和单机部署；若后续并发和吞吐上升，建议迁移到更专业的队列系统。
4. 当前索引仍采用“启动时扫描 source 清单 + 运行期显式同步”的策略，但索引写入已经改为 checksum 驱动的增量更新，不再在每次启动时全量重建。若文档规模继续增长，建议继续补齐知识文件变更监听或独立导入任务。
5. 当前 HTTP 服务仍基于 Python 标准库，已支持最简 NDJSON 流式输出；若后续要引入更复杂的鉴权、中间件或更稳定的 SSE / WebSocket 生命周期，建议再迁移到 FastAPI。

## 推荐部署模式

### 模式 A：本地直接运行

适合开发阶段：

1. 复制 `.env.example` 为 `.env`
2. 填写 `OPENAI_API_KEY`
3. 安装 `requirements.txt`
4. 运行 `python -m app.main`

### 模式 B：Docker 一键部署

适合本地联调或小规模部署：

1. 复制 `.env.example` 为 `.env`
2. 填写至少 `OPENAI_API_KEY`
3. 运行 `docker compose up --build`
4. 浏览器访问 `http://127.0.0.1:3000`
