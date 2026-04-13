const state = {
  sessionKey: "",
  attachments: [],
  knowledgeUploads: [],
  messages: [],
  sessions: [],
  cases: [],
  knowledge: [],
  currentMeta: {},
  pendingUserText: "",
  pendingAssistantText: "",
  pendingAssistantThinking: false,
  streaming: false,
};

const els = {
  chatTitle: document.getElementById("chat-title"),
  chatMeta: document.getElementById("chat-meta"),
  sessionKey: document.getElementById("session-key"),
  statusText: document.getElementById("status-text"),
  messages: document.getElementById("messages"),
  emptyChat: document.getElementById("empty-chat"),
  question: document.getElementById("question-input"),
  fileInput: document.getElementById("file-input"),
  fileList: document.getElementById("file-list"),
  composerForm: document.getElementById("composer-form"),
  sendBtn: document.getElementById("send-btn"),
  newSessionBtn: document.getElementById("new-session-btn"),
  sessionsList: document.getElementById("sessions-list"),
  casesList: document.getElementById("cases-list"),
  knowledgeList: document.getElementById("knowledge-list"),
  refreshSessions: document.getElementById("refresh-sessions"),
  refreshCases: document.getElementById("refresh-cases"),
  refreshKnowledge: document.getElementById("refresh-knowledge"),
  toggleKnowledgeBtn: document.getElementById("toggle-knowledge-btn"),
  toggleFinalizeBtn: document.getElementById("toggle-finalize-btn"),
  finalizePanel: document.getElementById("finalize-panel"),
  knowledgePanel: document.getElementById("knowledge-panel"),
  caseTitle: document.getElementById("case-title"),
  finalRootCause: document.getElementById("final-root-cause"),
  actualFix: document.getElementById("actual-fix"),
  finalizeBtn: document.getElementById("finalize-btn"),
  refreshKnowledgePanel: document.getElementById("refresh-knowledge-panel"),
  knowledgeImportBtn: document.getElementById("knowledge-import-btn"),
  knowledgeFileInput: document.getElementById("knowledge-file-input"),
  knowledgeFileList: document.getElementById("knowledge-file-list"),
  knowledgeCount: document.getElementById("knowledge-count"),
  knowledgeManageList: document.getElementById("knowledge-manage-list"),
};

function setStatus(text) {
  els.statusText.textContent = text;
}

function createNewSession() {
  state.sessionKey = "";
  state.attachments = [];
  state.messages = [];
  state.currentMeta = {};
  state.pendingUserText = "";
  state.pendingAssistantText = "";
  state.pendingAssistantThinking = false;
  state.streaming = false;

  els.question.value = "";
  els.fileInput.value = "";
  els.caseTitle.value = "";
  els.finalRootCause.value = "";
  els.actualFix.value = "";
  els.finalizePanel.classList.add("hidden");
  els.knowledgePanel.classList.add("hidden");

  renderFiles();
  updateHeader();
  renderMessages();
  renderSessions();
  renderCases();
  renderKnowledge();
  setStatus("空闲");
}

function updateHeader() {
  const title = state.currentMeta.title || "新会话";
  const updatedAt = state.currentMeta.updated_at || "";
  const summary = state.currentMeta.summary?.overview || "";
  const metaParts = [];

  if (updatedAt) {
    metaParts.push(`最近更新时间：${updatedAt}`);
  }
  if (summary) {
    metaParts.push(`摘要：${summary}`);
  }

  els.chatTitle.textContent = title;
  els.chatMeta.textContent = metaParts.join(" | ") || "还没有消息，输入问题后会自动创建主会话。";
  els.sessionKey.textContent = state.sessionKey ? `会话键：${state.sessionKey}` : "未创建会话";
}

async function readFiles(fileList, targetKey) {
  const files = Array.from(fileList || []);
  const loaded = await Promise.all(
    files.map(
      (file) =>
        new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () =>
            resolve({
              name: file.name,
              content: String(reader.result || ""),
              size: file.size,
            });
          reader.onerror = () => reject(reader.error || new Error("文件读取失败"));
          reader.readAsText(file, "utf-8");
        }),
    ),
  );
  state[targetKey] = loaded;
}

function renderFiles() {
  renderFileChipList(els.fileList, state.attachments, (index) => {
    state.attachments.splice(index, 1);
    renderFiles();
  });
}

function renderKnowledgeUploads() {
  renderFileChipList(els.knowledgeFileList, state.knowledgeUploads, (index) => {
    state.knowledgeUploads.splice(index, 1);
    renderKnowledgeUploads();
  });
}

function renderFileChipList(container, items, onRemove) {
  container.innerHTML = "";
  items.forEach((file, index) => {
    const item = document.createElement("li");
    item.className = "file-chip";

    const label = document.createElement("span");
    const length = typeof file.size === "number" && file.size > 0 ? file.size : file.content.length;
    label.textContent = `${file.name} (${length} bytes)`;
    item.appendChild(label);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "移除";
    removeBtn.addEventListener("click", () => onRemove(index));
    item.appendChild(removeBtn);

    container.appendChild(item);
  });
}

function renderSessions() {
  els.sessionsList.innerHTML = "";
  if (!state.sessions.length) {
    els.sessionsList.appendChild(buildEmptySidebarItem("暂无主会话"));
    return;
  }

  state.sessions.forEach((session) => {
    const item = document.createElement("li");
    item.className = `sidebar-item${session.session_key === state.sessionKey ? " active" : ""}`;

    const main = document.createElement("button");
    main.type = "button";
    main.className = "sidebar-entry-main";
    main.addEventListener("click", () => {
      loadSession(session.session_key).catch((error) => setStatus(`会话加载失败：${error.message}`));
    });

    const title = document.createElement("strong");
    title.textContent = session.title || session.session_key;
    main.appendChild(title);

    const meta = document.createElement("small");
    meta.textContent = `${session.updated_at || ""} | ${session.total_messages || 0} 条消息`;
    main.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "sidebar-entry-actions";

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger inline-danger";
    deleteBtn.textContent = "删除";
    deleteBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteSession(session.session_key).catch((error) => setStatus(`会话删除失败：${error.message}`));
    });
    actions.appendChild(deleteBtn);

    item.appendChild(main);
    item.appendChild(actions);
    els.sessionsList.appendChild(item);
  });
}

function renderCases() {
  els.casesList.innerHTML = "";
  if (!state.cases.length) {
    els.casesList.appendChild(buildEmptySidebarItem("暂无案例"));
    return;
  }

  state.cases.forEach((item) => {
    const row = document.createElement("li");
    row.className = "sidebar-item";

    const main = document.createElement("div");
    main.className = "sidebar-entry-main";

    const title = document.createElement("strong");
    title.textContent = item.title || item.path || "未命名案例";
    main.appendChild(title);

    const meta = document.createElement("span");
    meta.textContent = `${item.category || "未分类"} | ${item.created_at || ""}`;
    main.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "sidebar-entry-actions";

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger inline-danger";
    deleteBtn.textContent = "删除";
    deleteBtn.addEventListener("click", () => {
      deleteCase(item.path).catch((error) => setStatus(`案例删除失败：${error.message}`));
    });
    actions.appendChild(deleteBtn);

    row.appendChild(main);
    row.appendChild(actions);
    els.casesList.appendChild(row);
  });
}

function renderKnowledge() {
  renderKnowledgeSidebar();
  renderKnowledgeManager();
  renderKnowledgeUploads();
}

function renderKnowledgeSidebar() {
  els.knowledgeList.innerHTML = "";
  if (!state.knowledge.length) {
    els.knowledgeList.appendChild(buildEmptySidebarItem("暂无知识文档"));
    return;
  }

  state.knowledge.forEach((item) => {
    const row = document.createElement("li");
    row.className = "sidebar-item";

    const main = document.createElement("button");
    main.type = "button";
    main.className = "sidebar-entry-main";
    main.addEventListener("click", () => {
      els.knowledgePanel.classList.remove("hidden");
      els.finalizePanel.classList.add("hidden");
      setStatus(`已选中知识：${item.path}`);
    });

    const title = document.createElement("strong");
    title.textContent = item.title || item.path || "未命名知识";
    main.appendChild(title);

    const meta = document.createElement("span");
    meta.textContent = `${item.path || ""} | ${item.updated_at || ""}`;
    main.appendChild(meta);

    row.appendChild(main);
    els.knowledgeList.appendChild(row);
  });
}

function renderKnowledgeManager() {
  els.knowledgeCount.textContent = `${state.knowledge.length} 篇`;
  els.knowledgeManageList.innerHTML = "";

  if (!state.knowledge.length) {
    const empty = document.createElement("li");
    empty.className = "management-item";
    empty.textContent = "暂无知识文档，可先选择本地文件后导入。";
    els.knowledgeManageList.appendChild(empty);
    return;
  }

  state.knowledge.forEach((item) => {
    const row = document.createElement("li");
    row.className = "management-item";

    const info = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title || item.path || "未命名知识";
    info.appendChild(title);

    const path = document.createElement("span");
    path.textContent = item.path || "";
    info.appendChild(path);

    const meta = document.createElement("small");
    meta.textContent = `格式：${item.format || "md"} | 更新时间：${item.updated_at || ""}`;
    info.appendChild(meta);

    const actions = document.createElement("div");
    actions.className = "panel-actions";

    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "danger";
    deleteBtn.textContent = "删除";
    deleteBtn.addEventListener("click", () => {
      deleteKnowledge(item.path).catch((error) => setStatus(`知识删除失败：${error.message}`));
    });
    actions.appendChild(deleteBtn);

    row.appendChild(info);
    row.appendChild(actions);
    els.knowledgeManageList.appendChild(row);
  });
}

function buildEmptySidebarItem(text) {
  const empty = document.createElement("li");
  empty.className = "sidebar-item";
  empty.textContent = text;
  return empty;
}

function renderMessages() {
  els.messages.innerHTML = "";

  const rows = [...state.messages];
  if (state.pendingUserText) {
    rows.push({
      role: "user",
      text: state.pendingUserText,
      metadata: { transient: true },
    });
  }
  if (state.streaming && (state.pendingAssistantThinking || state.pendingAssistantText)) {
    rows.push({
      role: "assistant",
      text: state.pendingAssistantText,
      metadata: {
        transient: true,
        streaming: true,
        thinking: state.pendingAssistantThinking,
      },
    });
  }

  els.emptyChat.classList.toggle("hidden", rows.length > 0);
  if (!rows.length) {
    els.messages.appendChild(els.emptyChat);
    return;
  }

  rows.forEach((message) => {
    const row = document.createElement("div");
    row.className = `message-row ${message.role || "assistant"}`;

    const bubble = document.createElement("article");
    bubble.className = "message-bubble";

    const role = document.createElement("p");
    role.className = "message-role";
    role.textContent = roleLabel(message.role, message.metadata);
    bubble.appendChild(role);

    const result = message.metadata?.diagnostic_result;
    if (result) {
      bubble.appendChild(renderDiagnosticCard(result));
    } else {
      const text = document.createElement("pre");
      text.className = "message-text";
      text.textContent = message.text || (message.metadata?.thinking ? "正在思考中..." : "");
      bubble.appendChild(text);
    }

    const meta = buildMessageMeta(message);
    if (meta) {
      const metaBlock = document.createElement("div");
      metaBlock.className = "message-meta";
      metaBlock.textContent = meta;
      bubble.appendChild(metaBlock);
    }

    if (message.metadata?.streaming) {
      const draft = document.createElement("div");
      draft.className = "draft-indicator";
      draft.textContent = message.metadata?.thinking ? "正在思考中..." : "流式生成中...";
      bubble.appendChild(draft);
    }

    row.appendChild(bubble);
    els.messages.appendChild(row);
  });

  els.messages.scrollTop = els.messages.scrollHeight;
}

function roleLabel(role, metadata) {
  if (role === "user") {
    return "用户";
  }
  if (role === "system") {
    return "系统记录";
  }
  if (metadata?.streaming) {
    return "助手（生成中）";
  }
  return "助手";
}

function buildMessageMeta(message) {
  const bits = [];
  if (message.created_at) {
    bits.push(message.created_at);
  }
  if (message.metadata?.case_path) {
    bits.push(`已沉淀案例：${message.metadata.case_path}`);
  }
  return bits.join(" | ");
}

function renderDiagnosticCard(result) {
  const card = document.createElement("div");
  card.className = "diagnostic-card";

  const header = document.createElement("div");
  header.className = "diagnostic-header";

  const title = document.createElement("h3");
  title.textContent = "结构化诊断";
  header.appendChild(title);

  const tag = document.createElement("span");
  tag.className = "diagnostic-tag";
  tag.textContent = result.problem_category || "未分类";
  header.appendChild(tag);
  card.appendChild(header);

  appendIf(card, buildTextSection("诊断摘要", result.summary || ""), Boolean(result.summary));

  const rootCauseItems = (result.candidate_root_causes || []).map((item) => {
    const confidence = item.confidence ? `（置信度：${item.confidence}）` : "";
    return `${item.title}${confidence}：${item.reasoning}`;
  });
  appendIf(card, buildOrderedSection("候选根因", rootCauseItems), rootCauseItems.length > 0);

  const steps = result.troubleshooting_steps || [];
  appendIf(card, buildOrderedSection("排查建议", steps), steps.length > 0);

  const references = result.references || [];
  appendIf(card, buildReferenceSection("参考依据", references), references.length > 0);

  const missingInformation = result.missing_information || [];
  appendIf(card, buildUnorderedSection("建议补充的信息", missingInformation), missingInformation.length > 0);

  return card;
}

function appendIf(parent, node, condition) {
  if (condition) {
    parent.appendChild(node);
  }
}

function buildTextSection(title, text) {
  const section = document.createElement("section");
  section.className = "diagnostic-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  section.appendChild(heading);
  const body = document.createElement("p");
  body.textContent = text;
  section.appendChild(body);
  return section;
}

function buildOrderedSection(title, items) {
  const section = document.createElement("section");
  section.className = "diagnostic-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  section.appendChild(heading);
  const list = document.createElement("ol");
  fillList(list, items);
  section.appendChild(list);
  return section;
}

function buildUnorderedSection(title, items) {
  const section = document.createElement("section");
  section.className = "diagnostic-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  section.appendChild(heading);
  const list = document.createElement("ul");
  fillList(list, items);
  section.appendChild(list);
  return section;
}

function buildReferenceSection(title, items) {
  const section = document.createElement("section");
  section.className = "diagnostic-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  section.appendChild(heading);

  const list = document.createElement("div");
  list.className = "reference-list";
  items.forEach((item) => {
    const ref = document.createElement("div");
    ref.className = "reference-item";

    const titleBlock = document.createElement("strong");
    titleBlock.textContent = `[${item.type || "资料"}] ${item.title || "未命名"}`;
    ref.appendChild(titleBlock);

    const location = document.createElement("span");
    location.textContent = item.url || item.location || "未提供链接";
    ref.appendChild(location);

    if (item.snippet) {
      const snippet = document.createElement("span");
      snippet.textContent = item.snippet;
      ref.appendChild(snippet);
    }

    list.appendChild(ref);
  });

  section.appendChild(list);
  return section;
}

function fillList(target, items) {
  target.innerHTML = "";
  items.forEach((item) => {
    const row = document.createElement("li");
    row.textContent = item;
    target.appendChild(row);
  });
}

async function loadSessions() {
  const response = await fetch("/api/sessions");
  const data = await response.json();
  state.sessions = data.items || [];
  renderSessions();
}

async function loadCases() {
  const response = await fetch("/api/cases");
  const data = await response.json();
  state.cases = data.items || [];
  renderCases();
}

async function loadKnowledge() {
  const response = await fetch("/api/knowledge");
  const data = await response.json();
  state.knowledge = data.items || [];
  renderKnowledge();
}

async function loadSession(sessionKey) {
  setStatus("加载会话中...");
  const response = await fetch(`/api/session?session_key=${encodeURIComponent(sessionKey)}`);
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "会话加载失败");
  }

  state.sessionKey = data.session_key;
  state.currentMeta = data.meta || {};
  state.messages = data.messages || [];
  state.pendingUserText = "";
  state.pendingAssistantText = "";
  state.pendingAssistantThinking = false;

  updateHeader();
  renderMessages();
  renderSessions();
  setStatus("会话已加载");
}

async function sendMessage(event) {
  if (event) {
    event.preventDefault();
  }

  const text = els.question.value.trim();
  if (!text && !state.attachments.length) {
    setStatus("请输入内容或选择附件");
    return;
  }
  if (state.streaming) {
    return;
  }

  state.streaming = true;
  els.sendBtn.disabled = true;

  const outgoingAttachments = [...state.attachments];
  state.pendingUserText = buildOutgoingPreview(text, outgoingAttachments);
  state.pendingAssistantText = "";
  state.pendingAssistantThinking = true;

  els.question.value = "";
  els.fileInput.value = "";
  state.attachments = [];

  renderFiles();
  renderMessages();
  setStatus("分析中...");

  try {
    const response = await fetch("/api/web/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        attachments: outgoingAttachments,
        session_key: state.sessionKey || undefined,
        user_id: "browser-user",
      }),
    });

    if (!response.ok || !response.body) {
      throw new Error("流式请求失败");
    }

    let finalPayload = null;
    await readNdjson(response, (item) => {
      if (item.type === "meta" && item.session_key) {
        state.sessionKey = item.session_key;
        updateHeader();
        renderSessions();
      }

      if (item.type === "event" && item.event) {
        const evt = item.event;
        if (evt.stream === "assistant" && evt.phase === "delta" && evt.payload?.chunk) {
          state.pendingAssistantThinking = false;
          state.pendingAssistantText += evt.payload.chunk;
          renderMessages();
        } else if (evt.payload?.message) {
          setStatus(evt.payload.message);
        }
      }

      if (item.type === "result") {
        finalPayload = item;
      }

      if (item.type === "error") {
        throw new Error(item.error || "流式请求失败");
      }
    });

    if (!finalPayload?.ok) {
      throw new Error(finalPayload?.error || "分析失败");
    }
    if (finalPayload.session_key) {
      state.sessionKey = finalPayload.session_key;
    }

    await Promise.all([loadSessions(), loadCases()]);
    await loadSession(state.sessionKey);
    setStatus("分析完成");
  } catch (error) {
    state.pendingAssistantText = "";
    state.pendingAssistantThinking = false;
    renderMessages();
    setStatus(`失败：${error.message}`);
  } finally {
    state.pendingUserText = "";
    state.pendingAssistantText = "";
    state.pendingAssistantThinking = false;
    state.streaming = false;
    els.sendBtn.disabled = false;
    renderMessages();
  }
}

function buildOutgoingPreview(text, attachments) {
  const parts = [];
  if (text) {
    parts.push(text);
  }
  attachments.forEach((item) => {
    parts.push(`[附件 ${item.name}]`);
  });
  return parts.join("\n\n");
}

async function readNdjson(response, onObject) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    lines.forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed) {
        return;
      }
      onObject(JSON.parse(trimmed));
    });
  }

  const tail = buffer.trim();
  if (tail) {
    onObject(JSON.parse(tail));
  }
}

async function finalizeCase() {
  if (!state.sessionKey) {
    setStatus("请先在当前会话完成一轮分析");
    return;
  }

  setStatus("沉淀案例中...");
  els.finalizeBtn.disabled = true;

  try {
    const response = await fetch("/api/web/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_key: state.sessionKey,
        title: els.caseTitle.value.trim(),
        final_root_cause: els.finalRootCause.value.trim(),
        actual_fix: els.actualFix.value.trim(),
        source: "web",
      }),
    });

    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "案例沉淀失败");
    }

    els.caseTitle.value = "";
    els.finalRootCause.value = "";
    els.actualFix.value = "";
    els.finalizePanel.classList.add("hidden");

    await Promise.all([loadCases(), loadSession(state.sessionKey)]);
    setStatus(`案例已写入：${data.path}；${summarizeVectorSync(data.vector_sync)}`);
  } catch (error) {
    setStatus(`失败：${error.message}`);
  } finally {
    els.finalizeBtn.disabled = false;
  }
}

async function deleteSession(sessionKey) {
  if (!sessionKey) {
    return;
  }
  if (!window.confirm(`确认删除会话及其子会话？\n${sessionKey}`)) {
    return;
  }

  setStatus("删除会话中...");
  const response = await fetch("/api/sessions/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_key: sessionKey }),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "会话删除失败");
  }

  if (state.sessionKey === sessionKey) {
    createNewSession();
  }
  await loadSessions();
  setStatus(`会话已删除：${sessionKey}；${summarizeVectorSync(data.vector_sync)}`);
}

async function deleteCase(path) {
  if (!path) {
    return;
  }
  if (!window.confirm(`确认删除案例？\n${path}`)) {
    return;
  }

  setStatus("删除案例中...");
  const response = await fetch("/api/cases/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "案例删除失败");
  }

  await loadCases();
  setStatus(`案例已删除：${data.path}；${summarizeVectorSync(data.vector_sync)}`);
}

function toggleFinalizePanel() {
  els.knowledgePanel.classList.add("hidden");
  els.finalizePanel.classList.toggle("hidden");
}

function toggleKnowledgePanel() {
  els.finalizePanel.classList.add("hidden");
  els.knowledgePanel.classList.toggle("hidden");
}

async function importKnowledge() {
  if (!state.knowledgeUploads.length) {
    setStatus("请先选择知识文件");
    return;
  }

  setStatus("导入知识中...");
  els.knowledgeImportBtn.disabled = true;

  try {
    const results = [];
    for (const file of state.knowledgeUploads) {
      const response = await fetch("/api/knowledge/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: file.name,
          content: file.content,
          overwrite: true,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || `知识导入失败：${file.name}`);
      }
      results.push(data);
    }

    state.knowledgeUploads = [];
    els.knowledgeFileInput.value = "";
    renderKnowledgeUploads();

    await loadKnowledge();
    const lastResult = results[results.length - 1];
    setStatus(`已导入 ${results.length} 个知识文件；${summarizeVectorSync(lastResult.vector_sync)}`);
  } catch (error) {
    setStatus(`失败：${error.message}`);
  } finally {
    els.knowledgeImportBtn.disabled = false;
  }
}

async function deleteKnowledge(path) {
  if (!path) {
    return;
  }
  if (!window.confirm(`确认删除知识文档？\n${path}`)) {
    return;
  }

  setStatus("删除知识中...");
  const response = await fetch("/api/knowledge/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "知识删除失败");
  }

  await loadKnowledge();
  setStatus(`知识已删除：${data.path}；${summarizeVectorSync(data.vector_sync)}`);
}

function summarizeVectorSync(sync) {
  if (!sync) {
    return "未返回向量同步信息";
  }
  if (sync.vector_written) {
    return `向量同步完成，删除 ${sync.points_deleted} 条，写入 ${sync.points_upserted} 条`;
  }
  return `向量未写入，原因：${sync.disabled_reason || "unknown"}`;
}

els.composerForm.addEventListener("submit", sendMessage);
els.fileInput.addEventListener("change", async (event) => {
  try {
    await readFiles(event.target.files, "attachments");
    renderFiles();
  } catch (error) {
    setStatus(`附件读取失败：${error.message}`);
  }
});
els.knowledgeFileInput.addEventListener("change", async (event) => {
  try {
    await readFiles(event.target.files, "knowledgeUploads");
    renderKnowledgeUploads();
  } catch (error) {
    setStatus(`知识文件读取失败：${error.message}`);
  }
});
els.newSessionBtn.addEventListener("click", createNewSession);
els.refreshSessions.addEventListener("click", () => {
  loadSessions().catch((error) => setStatus(`会话刷新失败：${error.message}`));
});
els.refreshCases.addEventListener("click", () => {
  loadCases().catch((error) => setStatus(`案例刷新失败：${error.message}`));
});
els.refreshKnowledge.addEventListener("click", () => {
  loadKnowledge().catch((error) => setStatus(`知识刷新失败：${error.message}`));
});
els.refreshKnowledgePanel.addEventListener("click", () => {
  loadKnowledge().catch((error) => setStatus(`知识刷新失败：${error.message}`));
});
els.toggleKnowledgeBtn.addEventListener("click", toggleKnowledgePanel);
els.toggleFinalizeBtn.addEventListener("click", toggleFinalizePanel);
els.finalizeBtn.addEventListener("click", finalizeCase);
els.knowledgeImportBtn.addEventListener("click", importKnowledge);
els.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

createNewSession();
loadSessions().catch((error) => setStatus(`会话初始化失败：${error.message}`));
loadCases().catch((error) => setStatus(`案例初始化失败：${error.message}`));
loadKnowledge().catch((error) => setStatus(`知识初始化失败：${error.message}`));
