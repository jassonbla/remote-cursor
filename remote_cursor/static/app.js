const state = {
  conversations: [],
  selectedId: new URLSearchParams(location.search).get("session"),
  archived: false,
  query: "",
  loadingConversation: false,
  expandedProjects: new Set(),
  collapsedProjects: new Set(),
  repositoriesCollapsed: false,
  agentStatuses: new Map(),
  agentStatusAvailable: false,
  selectedConversation: null,
  listSignature: "",
  selectedConversationSignature: "",
};

const elements = {
  list: document.querySelector("#session-list"),
  count: document.querySelector("#session-count"),
  search: document.querySelector("#search-input"),
  searchPanel: document.querySelector("#search-panel"),
  searchToggle: document.querySelector("#search-toggle"),
  archiveToggle: document.querySelector("#archive-toggle"),
  repositoriesToggle: document.querySelector("#repositories-toggle"),
  title: document.querySelector("#conversation-title"),
  meta: document.querySelector("#conversation-meta"),
  content: document.querySelector("#conversation-content"),
  connectionDot: document.querySelector("#connection-dot"),
  connectionLabel: document.querySelector("#connection-label"),
  profileAvatar: document.querySelector("#profile-avatar"),
  profileFallback: document.querySelector("#profile-fallback"),
  profileEmail: document.querySelector("#profile-email"),
  profilePlan: document.querySelector("#profile-plan"),
  announcer: document.querySelector("#status-announcer"),
  sidebar: document.querySelector("#sidebar"),
  openSidebar: document.querySelector("#open-sidebar"),
  closeSidebar: document.querySelector("#close-sidebar"),
  collapsedSearch: document.querySelector("#collapsed-search"),
  scrim: document.querySelector("#sidebar-scrim"),
  branchContext: document.querySelector("#branch-context"),
  branchName: document.querySelector("#branch-name"),
  prChipList: document.querySelector("#pr-chip-list"),
  goBottom: document.querySelector("#go-bottom"),
};

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function icon(kind) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("codicon");
  svg.setAttribute("aria-hidden", "true");
  const names = {
    folder: "folder-opened",
    folderClosed: "folder",
    chevron: "chevron-right",
    file: "file",
    tool: "tools",
  };
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `/vendor/codicons.svg#${names[kind] || names.tool}`);
  svg.append(use);
  return svg;
}

function relativeTime(timestamp) {
  const numeric = Number(timestamp);
  if (!numeric) return "";
  const milliseconds = numeric < 10_000_000_000 ? numeric * 1000 : numeric;
  const delta = Math.max(0, Date.now() - milliseconds);
  if (delta < 60_000) return "now";
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)}h`;
  if (delta < 604_800_000) return `${Math.floor(delta / 86_400_000)}d`;
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(new Date(milliseconds));
}

function signature(value) {
  return JSON.stringify(value);
}

function projectLabel(project) {
  const path = String(project?.path || "").replace(/\/$/, "");
  if (path) return path.split("/").filter(Boolean).at(-1) || project?.name || "Other";
  return project?.name || "Other";
}

function groupConversations(conversations) {
  const groups = new Map();
  for (const conversation of conversations) {
    const key = conversation.project?.id || conversation.project?.path || conversation.project?.name || "other";
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        name: projectLabel(conversation.project),
        path: conversation.project?.path || "",
        conversations: [],
      });
    }
    groups.get(key).conversations.push(conversation);
  }
  return [...groups.values()].sort((a, b) => Number(b.conversations[0]?.updatedAt || 0) - Number(a.conversations[0]?.updatedAt || 0));
}

function agentStatusFor(conversationId) {
  const thread = state.agentStatuses.get(conversationId);
  return thread?.status || (state.agentStatusAvailable ? "unknown" : "unavailable");
}

function syncSessionRuntime(button, conversation) {
  const status = agentStatusFor(conversation.id);
  const running = status === "running";
  button.dataset.agentStatus = status;
  button.querySelector(".session-time").hidden = running;
  button.querySelector(".session-runtime").hidden = !running;
  button.setAttribute("aria-label", running ? `${conversation.title}, 응답 생성 중` : conversation.title);
}

function renderSessionButton(conversation) {
  const button = node("button", "session-button");
  button.type = "button";
  button.dataset.id = conversation.id;
  button.setAttribute("aria-current", conversation.id === state.selectedId ? "page" : "false");
  button.title = conversation.title;

  const title = node("span", "session-title", conversation.title);
  const time = node("time", "session-time", relativeTime(conversation.updatedAt));
  const numeric = Number(conversation.updatedAt);
  if (numeric) time.dateTime = new Date(numeric < 10_000_000_000 ? numeric * 1000 : numeric).toISOString();
  const runtime = node("span", "session-runtime");
  runtime.append(node("span", "runtime-spinner"));
  runtime.hidden = true;
  runtime.setAttribute("aria-hidden", "true");
  const trailing = node("span", "session-trailing");
  trailing.append(time, runtime);
  button.append(title, trailing);
  syncSessionRuntime(button, conversation);
  button.addEventListener("click", () => selectConversation(conversation.id));
  return button;
}

function renderList() {
  elements.list.replaceChildren();
  elements.count.textContent = String(state.conversations.length);

  if (!state.conversations.length) {
    const message = node("p", "sidebar-empty", state.query ? "검색 결과가 없습니다." : "표시할 세션이 없습니다.");
    elements.list.append(message);
    return;
  }

  for (const [index, group] of groupConversations(state.conversations).entries()) {
    const section = node("section", "project-group");
    const contentId = `project-sessions-${index}`;
    const collapsed = state.collapsedProjects.has(group.key);
    const heading = node("button", "project-heading");
    heading.type = "button";
    heading.title = group.path || group.name;
    heading.setAttribute("aria-expanded", String(!collapsed));
    heading.setAttribute("aria-controls", contentId);
    const chevron = icon("chevron");
    chevron.classList.add("project-chevron");
    heading.append(icon(collapsed ? "folderClosed" : "folder"), node("span", "", group.name), chevron);
    heading.addEventListener("click", () => {
      if (collapsed) state.collapsedProjects.delete(group.key);
      else state.collapsedProjects.add(group.key);
      renderList();
    });
    section.append(heading);

    const projectContent = node("div", "project-content");
    projectContent.id = contentId;
    projectContent.hidden = collapsed;

    const expanded = state.expandedProjects.has(group.key);
    const limit = expanded ? group.conversations.length : 3;
    for (const conversation of group.conversations.slice(0, limit)) projectContent.append(renderSessionButton(conversation));
    if (group.conversations.length > 3) {
      const more = node("button", "more-sessions", expanded ? "Less" : "More");
      more.type = "button";
      more.setAttribute("aria-expanded", String(expanded));
      more.addEventListener("click", () => {
        if (expanded) state.expandedProjects.delete(group.key);
        else state.expandedProjects.add(group.key);
        renderList();
      });
      projectContent.append(more);
    }
    section.append(projectContent);
    elements.list.append(section);
  }
}

function renderListLoading() {
  const listLoading = node("div", "list-loading");
  for (let index = 0; index < 8; index += 1) listLoading.append(node("div", "skeleton"));
  elements.list.replaceChildren(listLoading);
}

function renderConversationLoading() {
  state.selectedConversation = null;
  elements.branchName.textContent = "";
  elements.branchContext.hidden = true;
  elements.branchContext.title = "";
  renderPullRequests([]);
  elements.goBottom.hidden = true;
  const loading = node("div", "messages");
  for (let index = 0; index < 5; index += 1) {
    const skeleton = node("div", "skeleton");
    skeleton.style.height = index % 2 ? "96px" : "42px";
    skeleton.style.marginBottom = "20px";
    loading.append(skeleton);
  }
  elements.content.replaceChildren(loading);
}

function summarizeInput(input) {
  if (!input || typeof input !== "object") return "";
  const preferred = ["path", "file_path", "query", "pattern", "command", "description", "name"];
  for (const key of preferred) {
    if (typeof input[key] === "string" && input[key]) return input[key].replace(/\s+/g, " ").slice(0, 120);
  }
  return "View input";
}

function appendInline(parent, text) {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("`")) {
      parent.append(node("code", "inline-code", token.slice(1, -1)));
    } else if (token.startsWith("**")) {
      parent.append(node("strong", "", token.slice(2, -2)));
    } else {
      const linkMatch = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/);
      if (linkMatch) {
        const link = node("a", "", linkMatch[1]);
        link.href = linkMatch[2];
        link.target = "_blank";
        link.rel = "noreferrer noopener";
        parent.append(link);
      }
    }
    cursor = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(cursor)));
}

function splitTableRow(line) {
  let source = String(line || "").trim();
  if (source.startsWith("|")) source = source.slice(1);
  if (source.endsWith("|")) {
    let backslashes = 0;
    for (let index = source.length - 2; index >= 0 && source[index] === "\\"; index -= 1) backslashes += 1;
    if (backslashes % 2 === 0) source = source.slice(0, -1);
  }

  const cells = [];
  let cell = "";
  let inCode = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    const next = source[index + 1];
    if (character === "\\" && ["|", "\\", "`"].includes(next)) {
      cell += next;
      index += 1;
    } else if (character === "`") {
      inCode = !inCode;
      cell += character;
    } else if (character === "|" && !inCode) {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += character;
    }
  }
  cells.push(cell.trim());
  return cells;
}

function tableAlignment(delimiter) {
  const value = String(delimiter || "").trim();
  if (!/^:?-{3,}:?$/.test(value)) return false;
  if (value.startsWith(":") && value.endsWith(":")) return "center";
  if (value.endsWith(":")) return "right";
  return "left";
}

function parseTableHeader(lines, index) {
  if (index + 1 >= lines.length || !lines[index].includes("|")) return null;
  const headers = splitTableRow(lines[index]);
  const delimiters = splitTableRow(lines[index + 1]);
  if (headers.length < 2 || delimiters.length !== headers.length) return null;
  const alignments = delimiters.map(tableAlignment);
  if (alignments.some((alignment) => alignment === false)) return null;
  return { headers, alignments };
}

function appendTableRow(parent, values, alignments, tagName) {
  const row = node("tr");
  for (let index = 0; index < alignments.length; index += 1) {
    const cell = node(tagName);
    if (tagName === "th") cell.scope = "col";
    cell.style.textAlign = alignments[index];
    appendInline(cell, values[index] || "");
    row.append(cell);
  }
  parent.append(row);
}

function renderMarkdown(text) {
  const shell = node("div", "text-block markdown");
  const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
  let index = 0;
  let paragraph = [];
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const element = node("p");
    // Cursor follows CommonMark's soft-break behavior: a single source
    // newline inside a paragraph becomes ordinary whitespace. Empty lines
    // still delimit paragraphs below.
    appendInline(element, paragraph.join(" "));
    shell.append(element);
    paragraph = [];
  };

  while (index < lines.length) {
    const line = lines[index];
    const fence = line.match(/^\s*```([^`]*)$/);
    if (fence) {
      flushParagraph();
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      const pre = node("pre", "code-block");
      if (fence[1].trim()) pre.dataset.language = fence[1].trim();
      pre.append(node("code", "", codeLines.join("\n")));
      shell.append(pre);
      index += 1;
      continue;
    }
    const tableHeader = parseTableHeader(lines, index);
    if (tableHeader) {
      flushParagraph();
      const wrapper = node("div", "markdown-table-shell");
      wrapper.tabIndex = 0;
      wrapper.setAttribute("aria-label", "Scrollable table");
      const table = node("table", "markdown-table");
      const head = node("thead");
      appendTableRow(head, tableHeader.headers, tableHeader.alignments, "th");
      table.append(head);

      const body = node("tbody");
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
        appendTableRow(body, splitTableRow(lines[index]), tableHeader.alignments, "td");
        index += 1;
      }
      table.append(body);
      wrapper.append(table);
      shell.append(wrapper);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      index += 1;
      continue;
    }
    if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      flushParagraph();
      shell.append(node("hr"));
      index += 1;
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      const element = node(`h${Math.min(6, heading[1].length + 2)}`);
      appendInline(element, heading[2]);
      shell.append(element);
      index += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      flushParagraph();
      const list = node("ul");
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        const item = node("li");
        appendInline(item, lines[index].replace(/^\s*[-*]\s+/, ""));
        list.append(item);
        index += 1;
      }
      shell.append(list);
      continue;
    }
    const orderedItem = line.match(/^\s*(\d+)\.\s+(.+)$/);
    if (orderedItem) {
      flushParagraph();
      const list = node("ol");
      list.start = Number(orderedItem[1]);
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^\s*(\d+)\.\s+(.+)$/);
        if (!itemMatch) break;
        const item = node("li");
        item.value = Number(itemMatch[1]);
        appendInline(item, itemMatch[2]);
        list.append(item);
        index += 1;
      }
      shell.append(list);
      continue;
    }
    if (/^>\s?/.test(line)) {
      flushParagraph();
      const quote = node("blockquote");
      appendInline(quote, line.replace(/^>\s?/, ""));
      shell.append(quote);
      index += 1;
      continue;
    }
    paragraph.push(line);
    index += 1;
  }
  flushParagraph();
  return shell;
}

function parseUserTransport(text) {
  const attachments = [];
  let display = String(text || "");
  display = display.replace(/<timestamp>[\s\S]*?<\/timestamp>\s*/gi, "");
  display = display.replace(/<image_files>([\s\S]*?)<\/image_files>\s*/gi, (_, value) => {
    for (const line of value.split("\n").map((item) => item.trim()).filter(Boolean)) attachments.push(line);
    return "";
  });
  const query = display.match(/<user_query>([\s\S]*?)<\/user_query>/i);
  if (query) display = query[1];
  return { text: display.trim(), attachments };
}

function renderTextBlock(text, role) {
  if (role !== "user") return renderMarkdown(text);
  const parsed = parseUserTransport(text);
  const shell = node("div", "transport-block");
  if (parsed.attachments.length) {
    const attachments = node("div", "attachment-list");
    for (const path of parsed.attachments) {
      const chip = node("span", "attachment-chip");
      chip.title = path;
      chip.append(icon("file"), node("span", "", path.split("/").filter(Boolean).at(-1) || "첨부 파일"));
      attachments.append(chip);
    }
    shell.append(attachments);
  }
  if (parsed.text) shell.append(renderMarkdown(parsed.text));
  return shell;
}

function blockType(block) {
  if (block && typeof block === "object") return block.type || "unknown";
  return "text";
}

function renderOrdinaryBlock(block, role) {
  if (typeof block === "string") return renderTextBlock(block, role);
  if (!block || typeof block !== "object") return node("div", "unknown-block", String(block ?? ""));
  if (block.type === "text") return renderTextBlock(block.text || "", role);
  return node("div", "unknown-block", JSON.stringify(block, null, 2));
}

function renderToolActivity(tools) {
  const details = node("details", "tool-activity");
  const label = tools.length === 1 ? "Explored 1 tool" : `Explored ${tools.length} tools`;
  details.append(node("summary", "", label));
  const list = node("div", "tool-list");
  for (const tool of tools) {
    const entry = node("div", "tool-entry");
    const heading = node("div", "tool-entry-heading");
    heading.append(icon("tool"), node("strong", "", tool.name || "Tool"), node("span", "", summarizeInput(tool.input)));
    entry.append(heading, node("pre", "tool-json", JSON.stringify(tool.input ?? {}, null, 2)));
    list.append(entry);
  }
  details.append(list);
  return details;
}

function groupTurns(messages) {
  const turns = [];
  let current = null;
  for (const message of messages) {
    if (message.role === "user") {
      current = { user: message, assistant: [] };
      turns.push(current);
    } else {
      if (!current) {
        current = { user: null, assistant: [] };
        turns.push(current);
      }
      current.assistant.push(message);
    }
  }
  return turns;
}

function renderAssistantContent(messages) {
  const body = node("div", "response-body");
  let pendingTools = [];
  const flushTools = () => {
    if (!pendingTools.length) return;
    body.append(renderToolActivity(pendingTools));
    pendingTools = [];
  };
  for (const message of messages) {
    for (const block of message.content || []) {
      if (blockType(block) === "tool_use") pendingTools.push(block);
      else {
        flushTools();
        body.append(renderOrdinaryBlock(block, "assistant"));
      }
    }
  }
  flushTools();
  return body;
}

function renderAssistantTurn(messages) {
  const response = node("div", "assistant-response");
  let finalIndex = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].isFinal) {
      finalIndex = index;
      break;
    }
  }
  if (finalIndex < 0) finalIndex = Math.max(0, messages.length - 1);

  const workMessages = messages.slice(0, finalIndex);
  const finalMessages = messages.slice(finalIndex);
  if (workMessages.length) {
    const work = node("details", "work-trace");
    work.append(node("summary", "work-status", "Worked"), renderAssistantContent(workMessages));
    response.append(work);
  } else {
    response.append(node("div", "work-status", "Worked"));
  }
  if (finalMessages.length) response.append(renderAssistantContent(finalMessages));
  return response;
}

function syncConversationRuntime() {
  const messages = elements.content.querySelector(".messages");
  if (!messages || !state.selectedConversation) return;
  const running = agentStatusFor(state.selectedConversation.id) === "running";
  const existing = messages.querySelector(".assistant-generating");
  if (!running) {
    existing?.remove();
    queueGoBottomSync();
    return;
  }
  if (existing) return;

  const indicator = node("div", "assistant-generating");
  indicator.setAttribute("role", "status");
  indicator.setAttribute("aria-label", "Cursor Agent가 응답을 생성 중입니다");
  const status = node("div", "work-status generating-status");
  status.append(node("span", "runtime-spinner"), node("span", "", "Working…"));
  indicator.append(status);
  messages.append(indicator);
  queueGoBottomSync();
}

function renderPullRequests(pullRequests) {
  elements.prChipList.replaceChildren();
  const statuses = {
    merged: { label: "merged", icon: "git-merge" },
    open: { label: "open", icon: "git-pull-request" },
    closed: { label: "closed", icon: "git-pull-request-closed" },
    unknown: { label: "status unknown", icon: "git-pull-request" },
  };
  for (const pullRequest of Array.isArray(pullRequests) ? pullRequests : []) {
    if (!pullRequest?.url || !pullRequest?.number) continue;
    const statusName = Object.hasOwn(statuses, pullRequest.status) ? pullRequest.status : "unknown";
    const status = statuses[statusName];
    const repository = String(pullRequest.repository || "");
    const repositoryName = repository.split("/").filter(Boolean).at(-1) || "PR";
    const link = node("a", `pr-chip pr-chip--${statusName}`);
    link.href = pullRequest.url;
    link.target = "_blank";
    link.rel = "noreferrer noopener";
    link.setAttribute("aria-label", `${repository || repositoryName} pull request ${pullRequest.number}, ${status.label}`);
    link.title = [pullRequest.title, `${repository} #${pullRequest.number}`, status.label].filter(Boolean).join(" · ");
    const statusIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    statusIcon.classList.add("codicon", "pr-chip-status");
    statusIcon.setAttribute("aria-hidden", "true");
    const statusUse = document.createElementNS("http://www.w3.org/2000/svg", "use");
    statusUse.setAttribute("href", `/vendor/codicons.svg#${status.icon}`);
    statusIcon.append(statusUse);
    link.append(
      statusIcon,
      node("span", "pr-chip-label", `#${pullRequest.number}`),
    );
    elements.prChipList.append(link);
  }
  elements.prChipList.hidden = !elements.prChipList.childElementCount;
}

let goBottomFrame;
function syncGoBottomVisibility() {
  goBottomFrame = undefined;
  const distance = elements.content.scrollHeight - elements.content.scrollTop - elements.content.clientHeight;
  elements.goBottom.hidden = !elements.content.querySelector(".messages") || distance < 160;
}

function queueGoBottomSync() {
  if (goBottomFrame) return;
  goBottomFrame = requestAnimationFrame(syncGoBottomVisibility);
}

function renderConversation(conversation) {
  state.selectedConversation = conversation;
  state.selectedConversationSignature = signature(conversation);
  elements.title.textContent = conversation.title;
  elements.title.title = conversation.title;
  elements.meta.textContent = projectLabel(conversation.project);
  document.title = `${conversation.title} — Remote Cursor`;
  const branch = typeof conversation.branch === "string" ? conversation.branch.trim() : "";
  elements.branchName.textContent = branch;
  elements.branchContext.hidden = !branch;
  elements.branchContext.title = branch ? `이 세션의 브랜치: ${branch}` : "";
  renderPullRequests(conversation.pullRequests);

  if (!conversation.hasTranscript) {
    const empty = node("div", "empty-state");
    empty.append(node("h3", "", "로컬 대화 원본이 없습니다"), node("p", "", "Cursor 검색 인덱스에는 있지만 로컬 transcript가 없는 세션입니다."));
    elements.content.replaceChildren(empty);
    queueGoBottomSync();
    return;
  }

  const messages = node("div", "messages");
  for (const turn of groupTurns(conversation.messages)) {
    const article = node("article", "turn");
    if (turn.user) {
      const user = node("div", "user-message");
      for (const block of turn.user.content || []) user.append(renderOrdinaryBlock(block, "user"));
      article.append(user);
    }
    if (turn.assistant.length) {
      article.append(renderAssistantTurn(turn.assistant));
    }
    messages.append(article);
  }
  elements.content.replaceChildren(messages);
  syncConversationRuntime();
  queueGoBottomSync();
}

function renderError(title, message) {
  state.selectedConversation = null;
  elements.branchName.textContent = "";
  elements.branchContext.hidden = true;
  elements.branchContext.title = "";
  renderPullRequests([]);
  elements.goBottom.hidden = true;
  const shell = node("div", "error-state");
  shell.append(node("h3", "", title), node("p", "", message));
  elements.content.replaceChildren(shell);
}

async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" }, cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function profileInitials(displayName, email) {
  const words = String(displayName || "").trim().split(/\s+/).filter(Boolean);
  if (words.length) return words.slice(0, 2).map((word) => [...word][0]).join("").toUpperCase();
  return String(email || "RC").slice(0, 2).toUpperCase();
}

async function loadProfile() {
  try {
    const profile = await fetchJson("/api/profile");
    elements.profileEmail.textContent = profile.email || profile.displayName || "Remote Cursor";
    elements.profileEmail.title = profile.displayName || profile.email || "Remote Cursor";
    elements.profilePlan.textContent = profile.plan || "Cursor account";
    elements.profileFallback.textContent = profileInitials(profile.displayName, profile.email);

    if (profile.hasAvatar) {
      elements.profileAvatar.addEventListener("load", () => {
        elements.profileAvatar.hidden = false;
        elements.profileFallback.hidden = true;
      }, { once: true });
      elements.profileAvatar.addEventListener("error", () => {
        elements.profileAvatar.hidden = true;
        elements.profileFallback.hidden = false;
      }, { once: true });
      elements.profileAvatar.alt = profile.displayName ? `${profile.displayName} 프로필 사진` : "프로필 사진";
      elements.profileAvatar.src = "/api/profile/avatar";
    }
  } catch {
    // The profile is optional; keep the non-identifying fallback when unavailable.
  }
}

function applyAgentStatus(payload) {
  const previousSelectedStatus = state.selectedId ? agentStatusFor(state.selectedId) : "unavailable";
  state.agentStatusAvailable = payload?.available === true;
  state.agentStatuses = new Map(
    (Array.isArray(payload?.threads) ? payload.threads : [])
      .filter((thread) => typeof thread?.id === "string")
      .map((thread) => [thread.id, thread]),
  );

  for (const conversation of state.conversations) {
    const button = elements.list.querySelector(`.session-button[data-id="${CSS.escape(conversation.id)}"]`);
    if (button) syncSessionRuntime(button, conversation);
  }
  syncConversationRuntime();

  const selectedStatus = state.selectedId ? agentStatusFor(state.selectedId) : "unavailable";
  if (selectedStatus === "running" && previousSelectedStatus !== "running") {
    elements.announcer.textContent = "Cursor Agent가 응답 생성을 시작했습니다.";
  } else if (previousSelectedStatus === "running" && selectedStatus !== "running") {
    elements.announcer.textContent = "Cursor Agent가 응답 생성을 마쳤습니다.";
  }
}

async function loadAgentStatus() {
  try {
    applyAgentStatus(await fetchJson("/api/agent-status"));
  } catch {
    applyAgentStatus({ available: false, threads: [] });
  }
}

async function loadConversations({ preserveSelection = true, background = false } = {}) {
  const params = new URLSearchParams({ archived: state.archived ? "1" : "0", limit: "500" });
  if (state.query) params.set("q", state.query);
  try {
    const payload = await fetchJson(`/api/conversations?${params}`);
    const nextSignature = signature(payload.conversations);
    const listChanged = state.listSignature !== nextSignature;
    state.conversations = payload.conversations;
    state.listSignature = nextSignature;
    if (!preserveSelection || !state.selectedId) {
      state.selectedId = payload.selectedId && state.conversations.some((item) => item.id === payload.selectedId)
        ? payload.selectedId
        : state.conversations[0]?.id || null;
    }
    if (listChanged) renderList();
    setConnection(true, `${payload.count} sessions`);
    if (state.selectedId && !state.loadingConversation) {
      await loadConversation(state.selectedId, false, { showLoading: !background });
    }
  } catch (error) {
    state.conversations = [];
    renderList();
    setConnection(false, "Connection failed");
    renderError("세션을 불러오지 못했습니다", error.message);
  }
}

async function loadConversation(conversationId, announce = true, { showLoading = announce } = {}) {
  if (!conversationId) return;
  state.loadingConversation = true;
  if (showLoading) renderConversationLoading();
  try {
    const conversation = await fetchJson(`/api/conversations/${encodeURIComponent(conversationId)}`);
    if (state.selectedId !== conversationId) return;
    if (state.selectedConversationSignature !== signature(conversation)) renderConversation(conversation);
    if (announce) elements.announcer.textContent = `${conversation.title} 대화를 열었습니다.`;
  } catch (error) {
    if (state.selectedId === conversationId) renderError("대화를 불러오지 못했습니다", error.message);
  } finally {
    state.loadingConversation = false;
  }
}

async function selectConversation(conversationId) {
  if (conversationId === state.selectedId && elements.content.querySelector(".messages")) return;
  state.selectedId = conversationId;
  const url = new URL(location.href);
  url.searchParams.set("session", conversationId);
  history.replaceState(null, "", url);
  renderList();
  if (sidebarMedia.matches) closeSidebar();
  await loadConversation(conversationId, true, { showLoading: true });
}

function setConnection(online, message) {
  elements.connectionDot.className = `connection-dot ${online ? "online" : "offline"}`;
  elements.connectionDot.title = message;
  elements.connectionLabel.textContent = message;
}

const sidebarMedia = matchMedia("(max-width: 46rem)");

function syncSidebarControls() {
  const expanded = sidebarMedia.matches
    ? document.body.classList.contains("sidebar-open")
    : !document.body.classList.contains("sidebar-collapsed");
  elements.closeSidebar.setAttribute("aria-expanded", String(expanded));
  elements.openSidebar.setAttribute("aria-expanded", String(expanded));
}

function openSidebar({ focus = true } = {}) {
  if (sidebarMedia.matches) document.body.classList.add("sidebar-open");
  else document.body.classList.remove("sidebar-collapsed");
  syncSidebarControls();
  if (focus) elements.closeSidebar.focus();
}

function closeSidebar() {
  if (sidebarMedia.matches) document.body.classList.remove("sidebar-open");
  else document.body.classList.add("sidebar-collapsed");
  syncSidebarControls();
  elements.openSidebar.focus();
}

function toggleSearch(force) {
  const open = force ?? elements.searchPanel.hidden;
  elements.searchPanel.hidden = !open;
  elements.searchToggle.setAttribute("aria-expanded", String(open));
  if (open) elements.search.focus();
}

let searchTimer;
elements.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = elements.search.value.trim();
    state.selectedId = null;
    renderListLoading();
    loadConversations({ preserveSelection: false });
  }, 180);
});

elements.searchToggle.addEventListener("click", () => toggleSearch());
elements.repositoriesToggle.addEventListener("click", () => {
  state.repositoriesCollapsed = !state.repositoriesCollapsed;
  elements.repositoriesToggle.setAttribute("aria-expanded", String(!state.repositoriesCollapsed));
  elements.list.hidden = state.repositoriesCollapsed;
});
elements.archiveToggle.addEventListener("click", () => {
  state.archived = !state.archived;
  state.selectedId = null;
  elements.archiveToggle.setAttribute("aria-pressed", String(state.archived));
  elements.archiveToggle.setAttribute("aria-label", state.archived ? "최근 세션 보기" : "보관된 세션 보기");
  renderListLoading();
  loadConversations({ preserveSelection: false });
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openSidebar({ focus: false });
    toggleSearch(true);
  }
  if (event.key === "Escape") {
    if (!elements.searchPanel.hidden && document.activeElement === elements.search) toggleSearch(false);
    else if (document.body.classList.contains("sidebar-open")) closeSidebar();
  }
});

elements.openSidebar.addEventListener("click", openSidebar);
elements.closeSidebar.addEventListener("click", closeSidebar);
elements.collapsedSearch.addEventListener("click", () => {
  openSidebar({ focus: false });
  toggleSearch(true);
});
elements.scrim.addEventListener("click", closeSidebar);
sidebarMedia.addEventListener("change", syncSidebarControls);
elements.content.addEventListener("scroll", queueGoBottomSync, { passive: true });
elements.goBottom.addEventListener("click", () => {
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  elements.content.scrollTo({ top: elements.content.scrollHeight, behavior: reduceMotion ? "auto" : "smooth" });
  elements.announcer.textContent = "대화 하단으로 이동했습니다.";
});
new MutationObserver(queueGoBottomSync).observe(elements.content, {
  attributes: true,
  childList: true,
  subtree: true,
});
syncSidebarControls();

function connectEvents() {
  const events = new EventSource("/api/events");
  events.addEventListener("data.changed", async () => {
    await loadConversations({ background: true });
    elements.announcer.textContent = "Cursor의 최신 변경사항을 반영했습니다.";
  });
  events.onerror = () => setConnection(false, "Reconnecting…");
  events.onopen = () => setConnection(true, "Live sync");
}

renderListLoading();
loadProfile();
loadConversations({ preserveSelection: true });
loadAgentStatus();
connectEvents();
