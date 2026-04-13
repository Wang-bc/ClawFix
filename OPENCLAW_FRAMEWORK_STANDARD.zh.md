# OpenClaw 框架功能结构标准

版本: v1.0
状态: 作为后续多 Agent 智能助手项目的实现标准
适用范围: 基于本教程 `claw0` 演进为可持续扩展的 OpenClaw 风格 Agent Gateway / Multi-Agent Assistant

## 1. 文档目标

本文件定义一个可落地、可扩展、可交付的 OpenClaw 风格框架标准，用于约束后续项目的目录结构、模块边界、功能分层、运行流程与实现优先级。

后续基于 Codex 的具体开发，应默认遵守本文件，除非你显式提出新的业务约束并更新本标准。

本标准有两个来源:

1. 当前教程仓库 `claw0` 的 10 节渐进式实现。
2. OpenClaw 官方文档中已经稳定公开的架构理念: Gateway、Agent Loop、System Prompt、Multi-Agent Routing、Messages/Delivery。

## 2. 总体设计原则

### 2.1 核心原则

- 单一权威入口: 所有消息统一进入 Gateway，再进入 Agent Runtime。
- 单轮可追踪: 每次 agent run 必须有明确的 `run_id`、`session_key`、`agent_id`。
- 会话串行优先: 同一会话默认串行，先保证一致性，再逐步放开并发。
- Agent 隔离优先: 多 Agent 的人格、工作区、会话、认证、记忆默认隔离。
- 文件驱动配置: persona、bootstrap、heartbeat、cron、memory 等尽量文件化。
- 工具显式注册: 工具必须通过 schema + handler 注册，不允许隐式魔法调用。
- 投递可靠性优先: 先落盘，后发送；先入队，后执行；失败可重试。
- 提示词可组合: system prompt 必须由固定层拼装，而非写死在代码里。
- 演进兼容: 教程版最小实现可以平滑升级到生产版，不推翻前面抽象。

### 2.2 禁止事项

- 禁止把通道逻辑、路由逻辑、Agent Loop、工具分发写成一个大文件。
- 禁止让多个 Agent 直接共享同一会话存储目录。
- 禁止在没有队列和锁的情况下并发写同一 session。
- 禁止把长期记忆全部直接塞进每轮 prompt。
- 禁止把“消息接入”和“消息投递”混为同一同步流程。

## 3. 标准分层

本框架标准采用 10 层能力模型，对应教程的 10 节，同时补齐生产级职责边界。

| 层级 | 标准层名称 | 教程对应 | 必备职责 |
|---|---|---|---|
| L1 | Agent Loop | s01 | 单轮推理循环、stop reason 分支、最终回复收敛 |
| L2 | Tool Runtime | s02 | 工具 schema、handler 分发、工具调用内循环 |
| L3 | Sessions & Context | s03 | session 持久化、消息重放、上下文保护、压缩 |
| L4 | Channels | s04 | 外部渠道接入、统一 `InboundMessage`、通道适配 |
| L5 | Gateway & Routing | s05 | 路由绑定、Agent 选择、网关入口、会话 key 生成 |
| L6 | Intelligence Layer | s06 | system prompt 组装、bootstrap 注入、skills、memory recall |
| L7 | Heartbeat & Cron | s07 | 主动任务、定时任务、lane 互斥、前置条件检查 |
| L8 | Delivery | s08 | 出站消息队列、chunking、ack/fail、退避重试 |
| L9 | Resilience | s09 | 失败分类、模型/认证轮换、重试洋葱、超时与降级 |
| L10 | Concurrency | s10 | lane queue、命令队列、generation、恢复与串行化策略 |

## 4. 标准运行流

任何一个完整请求，标准执行流如下:

1. 渠道收到原始消息，转成统一 `InboundMessage`。
2. Gateway 做去重、去抖、基础校验和来源识别。
3. Routing 根据绑定规则确定 `agent_id`。
4. Session 层生成或恢复 `session_key`，准备会话锁和上下文。
5. Prompt 层装配 system prompt，包括 workspace bootstrap、skills、memory recall、运行时信息。
6. Command Queue / Lane Queue 接管本轮执行，保证同一 session 串行。
7. Agent Loop 进入模型调用与工具调用循环。
8. 产生的回复流、工具流、生命周期事件统一发给事件总线或流式输出接口。
9. 最终回复进入 Delivery Queue，执行分片、发送、确认或失败重试。
10. 本轮运行结束后持久化 session、统计、记忆、运行日志和状态快照。

## 5. 标准目录结构

建议后续项目按如下结构实现:

```text
project/
  app/
    main.py
    config/
      settings.py
      models.py
    runtime/
      agent_loop.py
      command_queue.py
      lane_queue.py
      events.py
      run_context.py
    tools/
      registry.py
      schemas.py
      dispatcher.py
      builtins/
        files.py
        shell.py
        memory.py
        time.py
    sessions/
      session_store.py
      context_guard.py
      compactor.py
      serializers.py
    gateway/
      server.py
      rpc.py
      inbound_pipeline.py
      routing.py
      bindings.py
    channels/
      base.py
      cli.py
      telegram.py
      feishu.py
      slack.py
      discord.py
    agents/
      models.py
      manager.py
      workspace.py
      profiles.py
    prompt/
      builder.py
      bootstrap_loader.py
      skill_loader.py
      memory_recall.py
    memory/
      store.py
      search.py
      retention.py
    scheduling/
      heartbeat.py
      cron_service.py
      predicates.py
    delivery/
      queue.py
      chunking.py
      sender.py
      retry.py
    resilience/
      classifier.py
      failover.py
      retry_runner.py
      timeouts.py
    observability/
      logging.py
      metrics.py
      traces.py
  workspace/
    AGENTS.md
    BOOTSTRAP.md
    CRON.json
    HEARTBEAT.md
    IDENTITY.md
    MEMORY.md
    SOUL.md
    TOOLS.md
    USER.md
    memory/
    skills/
    .sessions/
    .agents/
  tests/
    unit/
    integration/
    e2e/
  docs/
    architecture/
```

## 6. 模块职责标准

### 6.1 `runtime/`

- `agent_loop.py`
  - 唯一权威 agent run 实现。
  - 负责模型调用、stop_reason 分支、工具循环、回复收敛。
- `command_queue.py`
  - 处理运行请求入队、取消、等待、状态跟踪。
- `lane_queue.py`
  - 定义命名 lane、最大并发、FIFO、公平性与 generation。
- `events.py`
  - 统一 lifecycle/tool/assistant/error 事件格式。
- `run_context.py`
  - 维护本轮 `run_id`、`agent_id`、`session_key`、deadline、trace 数据。

### 6.2 `tools/`

- 工具必须通过注册中心统一暴露。
- 每个工具至少定义:
  - `name`
  - `description`
  - `input_schema`
  - `handler`
  - `safety_policy`
- 内置工具优先包含:
  - 文件读取/编辑
  - shell 命令
  - 时间
  - 记忆写入/检索
  - 目录列举

### 6.3 `sessions/`

- `session_store.py`
  - 使用 JSONL 或等价 append-only 方案保存消息。
  - 支持追加、重放、摘要快照、元数据读取。
- `context_guard.py`
  - 负责 token 预算、截断、压缩触发、摘要注入。
- `compactor.py`
  - 负责历史压缩与摘要替换策略。

### 6.4 `gateway/`

- `server.py`
  - 对外暴露 WebSocket / HTTP / CLI 入口。
- `inbound_pipeline.py`
  - 负责 inbound dedupe、debounce、消息标准化、校验。
- `routing.py`
  - 根据 bindings 输出目标 agent。
- `bindings.py`
  - 管理 peer、channel、account、team、guild 等路由规则。

### 6.5 `channels/`

- 每个 Channel 只做两件事:
  - 把外部平台消息映射为统一入站结构。
  - 把出站消息发送到外部平台。
- 不在 Channel 内实现业务路由、Agent 推理或记忆决策。

### 6.6 `agents/`

- `manager.py`
  - 管理 agent 注册、默认 agent、agent 配置加载。
- `workspace.py`
  - 解析 agent 对应工作区。
- `profiles.py`
  - 管理模型配置、认证配置、故障切换配置。

### 6.7 `prompt/`

- `builder.py`
  - 统一拼装 system prompt。
- `bootstrap_loader.py`
  - 加载 `AGENTS.md`、`SOUL.md`、`IDENTITY.md` 等文件。
- `skill_loader.py`
  - 动态注入技能元信息与装载规则。
- `memory_recall.py`
  - 根据当前消息进行自动记忆召回。

### 6.8 `memory/`

- `store.py`
  - 管理长期记忆、日记记忆、分类存储。
- `search.py`
  - 负责关键词检索、向量检索或混合检索。
- `retention.py`
  - 管理保留周期、归档、清理。

### 6.9 `scheduling/`

- `heartbeat.py`
  - 在用户不活跃时主动运行代理回合。
- `cron_service.py`
  - 支持 `cron` / `at` / `every` 三类任务。
- `predicates.py`
  - 统一判断“现在该不该跑”。

### 6.10 `delivery/`

- `queue.py`
  - 先落盘再投递。
- `chunking.py`
  - 处理平台字数限制、富文本拆分、批次控制。
- `sender.py`
  - 管理平台发送器。
- `retry.py`
  - 失败退避、最大重试次数、死信策略。

### 6.11 `resilience/`

- `classifier.py`
  - 统一把异常归类为速率限制、认证失败、超时、网络失败、上下文溢出等。
- `failover.py`
  - 负责 profile/model/provider 切换。
- `retry_runner.py`
  - 封装多层重试洋葱。
- `timeouts.py`
  - 管理 agent run 超时和工具超时。

### 6.12 `observability/`

- 所有关键事件必须可记录:
  - 入站消息
  - 路由结果
  - run 生命周期
  - 工具调用
  - delivery 成败
  - retry / failover
  - cron / heartbeat 触发

## 7. 关键数据结构标准

以下数据结构必须在后续项目中稳定存在。

### 7.1 InboundMessage

```python
class InboundMessage(TypedDict):
    message_id: str
    channel: str
    account_id: str
    peer_id: str
    parent_peer_id: str | None
    sender_id: str
    sender_name: str | None
    text: str
    raw_payload: dict
    received_at: str
    reply_to_message_id: str | None
```

### 7.2 RouteDecision

```python
class RouteDecision(TypedDict):
    agent_id: str
    rule_type: str
    session_key: str
    dm_scope: str | None
```

### 7.3 AgentRunRequest

```python
class AgentRunRequest(TypedDict):
    run_id: str
    agent_id: str
    session_key: str
    user_text: str
    inbound: InboundMessage | None
    source: str
    created_at: str
    timeout_s: int
```

### 7.4 AgentEvent

```python
class AgentEvent(TypedDict):
    run_id: str
    session_key: str
    agent_id: str
    stream: str
    phase: str | None
    payload: dict
    created_at: str
```

### 7.5 QueuedDelivery

```python
class QueuedDelivery(TypedDict):
    delivery_id: str
    run_id: str
    channel: str
    account_id: str
    peer_id: str
    chunks: list[str]
    retry_count: int
    next_attempt_at: str
    status: str
```

## 8. Agent Loop 标准契约

Agent Loop 必须满足以下行为:

1. 同一 `session_key` 下的 run 默认串行。
2. 每轮运行必须先持久化用户输入，再开始模型推理。
3. 支持 `stop_reason` 至少包括:
   - `end_turn`
   - `tool_use`
   - `max_tokens`
   - `error`
4. 工具调用采用“模型发起 -> 注册表分发 -> 结果回灌 -> 继续推理”的闭环。
5. 运行过程中应持续发出事件流:
   - `lifecycle:start`
   - `assistant:delta`
   - `tool:start`
   - `tool:end`
   - `lifecycle:end`
   - `lifecycle:error`
6. 最终输出必须经过回复整形，再进入 delivery。

## 9. 路由与多 Agent 标准

多 Agent 模式下，每个 `agent_id` 都是完整隔离的作用域，至少隔离以下内容:

- workspace 文件
- session store
- auth profiles
- model defaults
- memory store
- delivery 配置

绑定规则采用“最具体优先”原则，建议标准优先级如下:

1. `peer`
2. `parent_peer`
3. `guild + role`
4. `guild`
5. `team`
6. `channel + account`
7. `channel`
8. `default agent`

这部分与教程 s05 一致，也与官方 OpenClaw 文档中的 deterministic binding 规则保持一致。

## 10. Prompt / Workspace 标准

### 10.1 固定注入文件

以下文件属于标准 workspace bootstrap 层:

- `AGENTS.md`
- `SOUL.md`
- `TOOLS.md`
- `IDENTITY.md`
- `USER.md`
- `HEARTBEAT.md`
- `BOOTSTRAP.md`
- `MEMORY.md`

### 10.2 Prompt 组装层次

建议采用以下固定顺序:

1. Base identity
2. Safety / policy
3. Runtime environment
4. Tool list
5. Agent workspace bootstrap
6. User context
7. Relevant memory recall
8. Skills instructions
9. Current date/time
10. Session-specific injected context

### 10.3 约束

- `MEMORY.md` 必须简短，只放稳定事实。
- 每日记忆、对话转录不得默认全部注入。
- Skills 只注入“如何加载”，而不是一次性注入全部正文。

## 11. Session / Context 标准

### 11.1 Session 存储

- 默认使用 append-only JSONL。
- 至少支持:
  - `append_message`
  - `load_messages`
  - `list_sessions`
  - `compact_session`
  - `get_session_meta`

### 11.2 Context Guard

- 必须有 token 预算。
- 必须支持三级动作:
  - 预警
  - 截断或摘要
  - 强制压缩

### 11.3 Compaction 触发条件

- 上下文占用超过阈值。
- 事件流出现 `max_tokens` / context overflow。
- session 历史过长。

## 12. Delivery 标准

### 12.1 基本要求

- 所有出站消息先入队，后发送。
- 平台发送失败必须可重试。
- 重试必须带指数退避或分段退避。
- 最大重试后进入失败队列或死信区。

### 12.2 必备能力

- chunking
- ack / fail 生命周期
- stats 统计
- failed queue 查看
- 可恢复后台 runner

## 13. 弹性标准

必须具备三层容错:

1. 单次请求内部重试
2. profile / auth / model 级 failover
3. 上下文压缩后重跑

必须统一识别至少以下失败类型:

- rate limit
- auth
- timeout
- transient network
- context overflow
- tool failure
- unknown

## 14. 并发标准

### 14.1 Lane 模型

标准 lane 至少包含:

- `main`
- `heartbeat`
- `cron`
- `delivery`

允许后续业务扩展:

- `research`
- `planner`
- `executor`

### 14.2 并发规则

- 同一 `session_key` 默认 `max_concurrency = 1`
- lane 必须 FIFO
- lane 支持 generation 计数，用于重启恢复和过期任务判断
- heartbeat 在主 lane 活跃时应让步

## 15. 记忆标准

记忆分为三层:

1. Bootstrap memory: `MEMORY.md` 中的稳定事实
2. Daily memory: `workspace/memory/daily/*.jsonl` 或等价结构
3. Searchable memory: 可被工具和 recall 模块检索的结构化记忆

后续若实现向量检索，要求保持接口兼容，不改变上层 prompt / tool / agent_loop 调用方式。

## 16. 配置标准

配置分四类:

1. 环境配置: `.env`
2. Agent 配置: persona、模型、工具开关、delivery 策略
3. Channel 配置: token、账号、开关、限流、消息限制
4. Runtime 配置: timeout、queue mode、retry policy、compaction threshold

要求:

- 默认值清晰
- agent 级配置可以覆盖全局默认
- channel 级配置可以覆盖投递和消息策略

## 17. 测试标准

至少建立三层测试:

- Unit: 工具分发、路由规则、context guard、chunking、failure classifier
- Integration: session + prompt + loop；gateway + routing；delivery + retry
- E2E: 从 inbound message 到最终 outbound delivery 的全链路

必须覆盖的高风险场景:

- 同一 session 并发消息
- 消息重复投递
- 消息去抖批处理
- tool use 多轮循环
- context overflow 后压缩恢复
- 模型 key 失效后 failover
- delivery 失败重试恢复
- heartbeat 与用户消息抢占

## 18. 后续项目建议实现顺序

后续你的多 Agent 智能助手项目，建议按以下顺序让 Codex 实现:

1. 先固定目录结构与核心数据结构。
2. 再实现 L1-L3: agent loop、tools、sessions。
3. 再实现 L4-L5: channels、gateway、routing。
4. 再实现 L6: prompt、bootstrap、memory recall、skills。
5. 再实现 L7-L8: heartbeat、cron、delivery。
6. 最后实现 L9-L10: resilience、concurrency。

这样做的原因很简单: 先把“可运行”和“可持久化”打稳，再叠加“可扩展”和“可生产化”。

## 19. 本标准对应当前教程映射

| 教程章节 | 已沉淀出的标准能力 |
|---|---|
| s01 | Agent run 最小闭环 |
| s02 | 工具注册与工具调用闭环 |
| s03 | Session 持久化与上下文保护 |
| s04 | 统一消息结构与通道抽象 |
| s05 | 网关入口与绑定路由 |
| s06 | Prompt 组装、bootstrap、skills、memory recall |
| s07 | Heartbeat 与 cron 调度 |
| s08 | 可靠投递与后台 runner |
| s09 | 失败分类、profile 轮换、重试洋葱 |
| s10 | Lane queue、命令队列、generation 恢复 |

## 20. 作为后续实现标准时的使用规则

以后当你让我“基于这个框架实现某个具体功能”时，默认按以下规则执行:

1. 优先复用本标准中已有目录和模块名。
2. 新功能先确定落在哪一层，再决定写入哪个模块。
3. 如果一个功能跨多层，必须先定义主责任层。
4. 如果新增模块，必须说明它为什么不能落到现有模块中。
5. 如果业务需求与本标准冲突，应先更新本文件，再开始大规模编码。

## 参考来源

- 当前教程仓库: `sessions/zh/s01` 到 `s10` 与 `workspace/*`
- OpenClaw Gateway Architecture: https://docs.openclaw.ai/concepts/architecture
- OpenClaw Agent Loop: https://docs.openclaw.ai/concepts/agent-loop
- OpenClaw System Prompt: https://docs.openclaw.ai/concepts/system-prompt
- OpenClaw Multi-Agent Routing: https://docs.openclaw.ai/concepts/multi-agent
- OpenClaw Messages: https://docs.openclaw.ai/concepts/messages
