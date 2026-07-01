const state = {
  users: [],
  selectedUser: null,
  documents: [],
};
const RUN_STORAGE_PREFIX = "solar-demo-runs:";
const USER_STORAGE_KEY = "solar-demo-selected-user";

const userSelect = document.querySelector("#user-select");
const accessBadge = document.querySelector("#access-badge");
const notice = document.querySelector("#notice");
const chatForm = document.querySelector("#chat-form");
const conversation = document.querySelector("#conversation");
const reportForm = document.querySelector("#report-form");
const reportType = document.querySelector("#report-type");
const monthField = document.querySelector("#month-field");
const documentList = document.querySelector("#document-list");

function identityHeaders() {
  if (!state.selectedUser) {
    throw new Error("Select a demo user first.");
  }
  return { "X-Demo-User": state.selectedUser.user_id };
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status}).`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Keep the bounded status message for non-JSON errors.
    }
    throw new Error(detail);
  }
  return response;
}

function showNotice(message, isError = false) {
  notice.textContent = message;
  notice.className = `notice visible${isError ? " error" : ""}`;
}

function clearNotice() {
  notice.textContent = "";
  notice.className = "notice";
}

function setBusy(form, busy) {
  form.querySelectorAll("button, select, textarea").forEach((control) => {
    control.disabled = busy;
  });
}

function refreshUserState() {
  state.selectedUser =
    state.users.find((user) => user.user_id === userSelect.value) || null;
  document.querySelectorAll("[data-needs-user]").forEach((button) => {
    button.disabled = !state.selectedUser;
  });
  if (!state.selectedUser) {
    accessBadge.textContent = "Select a user";
    localStorage.removeItem(USER_STORAGE_KEY);
    return;
  }
  localStorage.setItem(USER_STORAGE_KEY, state.selectedUser.user_id);
  accessBadge.textContent = state.selectedUser.can_view_financials
    ? "Energy + financial access"
    : "Energy-only access";
  reportType.value = "energy";
  monthField.hidden = true;
  state.documents = [];
  conversation.replaceChildren();
  const placeholder = document.createElement("p");
  placeholder.className = "placeholder";
  placeholder.textContent =
    "Try “List my plants” or “Compare my plants for March.”";
  conversation.append(placeholder);
  renderDocuments();
  clearNotice();
  recoverRuns();
}

function addMessage(kind, text) {
  const placeholder = conversation.querySelector(".placeholder");
  if (placeholder) placeholder.remove();
  const message = document.createElement("div");
  message.className = `message ${kind}`;
  message.textContent = text;
  if (kind === "agent") {
    for (const downloadPath of protectedDocumentPaths(text)) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Download report";
      button.addEventListener("click", () =>
        downloadFromPath(downloadPath, null, button),
      );
      message.append(document.createElement("br"), button);
    }
  }
  conversation.append(message);
  conversation.scrollTop = conversation.scrollHeight;
}

function protectedDocumentPaths(text) {
  const normalized = String(text).replace(/\\\//g, "/").replace(/&amp;/g, "&");
  const candidates = normalized.match(
    /(?:https?:\/\/[^\s()[\]{}<>"']+)?\/documents\/[A-Za-z0-9-]+\/download\?[^\s()[\]{}<>"']+/g,
  ) || [];
  const paths = new Set();
  candidates.forEach((candidate) => {
    try {
      const parsed = new URL(candidate.replace(/[.,;:!?]+$/, ""), window.location.origin);
      const pathMatch = parsed.pathname.match(
        /^\/documents\/([A-Za-z0-9-]+)\/download$/,
      );
      const runId = parsed.searchParams.get("run_id");
      if (
        parsed.origin === window.location.origin &&
        pathMatch &&
        runId &&
        /^[A-Za-z0-9-]+$/.test(runId)
      ) {
        paths.add(
          `/documents/${encodeURIComponent(pathMatch[1])}/download` +
            `?run_id=${encodeURIComponent(runId)}`,
        );
      }
    } catch {
      // Ignore malformed, external, and non-document URLs in model text.
    }
  });
  return paths;
}

function renderDocuments() {
  documentList.replaceChildren();
  if (!state.documents.length) {
    const empty = document.createElement("li");
    empty.className = "placeholder";
    empty.textContent = "No reports generated yet.";
    documentList.append(empty);
    return;
  }
  state.documents.forEach((documentInfo) => {
    const item = document.createElement("li");
    item.className = "document-item";
    const label = document.createElement("span");
    label.textContent = documentInfo.filename;
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Download";
    button.addEventListener("click", () => downloadDocument(documentInfo, button));
    item.append(label, button);
    documentList.append(item);
  });
}

function runStorageKey() {
  return `${RUN_STORAGE_PREFIX}${state.selectedUser.user_id}`;
}

function savedRunIds() {
  if (!state.selectedUser) return [];
  try {
    const value = JSON.parse(localStorage.getItem(runStorageKey()) || "[]");
    return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function rememberRun(runId) {
  const ids = [runId, ...savedRunIds().filter((item) => item !== runId)].slice(0, 20);
  localStorage.setItem(runStorageKey(), JSON.stringify(ids));
}

async function recoverRuns() {
  const selectedUserId = state.selectedUser?.user_id;
  const recoveredIds = [];
  for (const runId of savedRunIds()) {
    if (state.selectedUser?.user_id !== selectedUserId) return;
    try {
      const response = await api(`/runs/${encodeURIComponent(runId)}`, {
        headers: identityHeaders(),
      });
      const run = await response.json();
      recoveredIds.push(run.run_id);
      if (run.run_type === "chat" && run.result) {
        addMessage("agent", `Recovered answer:\n${run.result}`);
      }
      run.documents.forEach((documentInfo) => {
        if (!state.documents.some((item) => item.document_id === documentInfo.document_id)) {
          state.documents.push(documentInfo);
        }
      });
    } catch {
      // Remove stale, inaccessible, or reset-database run IDs.
    }
  }
  if (state.selectedUser?.user_id === selectedUserId) {
    localStorage.setItem(runStorageKey(), JSON.stringify(recoveredIds));
    renderDocuments();
  }
}

async function downloadDocument(documentInfo, button) {
  const query = new URLSearchParams({ run_id: documentInfo.run_id });
  const path =
    `/documents/${encodeURIComponent(documentInfo.document_id)}/download?${query}`;
  await downloadFromPath(path, documentInfo.filename, button);
}

function responseFilename(response) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="([^"]+)"/i);
  return match ? match[1] : null;
}

function safeFilename(candidate) {
  const normalized = String(candidate || "")
    .replace(/[/\\\u0000-\u001f\u007f]/g, "_")
    .replace(/[^A-Za-z0-9._ -]/g, "_")
    .slice(0, 180);
  return normalized && normalized !== "." && normalized !== ".."
    ? normalized
    : "solar-report";
}

async function downloadFromPath(path, suggestedFilename, button) {
  clearNotice();
  button.disabled = true;
  try {
    const response = await api(path, { headers: identityHeaders() });
    const blob = await response.blob();
    const filename = safeFilename(
      suggestedFilename || responseFilename(response),
    );
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    showNotice(`Downloaded ${filename}.`);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function loadUsers() {
  try {
    const response = await api("/demo-users");
    state.users = await response.json();
    userSelect.replaceChildren();
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = "Select a demo user";
    userSelect.append(prompt);
    state.users.forEach((user) => {
      const option = document.createElement("option");
      option.value = user.user_id;
      option.textContent = `${user.email} — ${user.role}`;
      userSelect.append(option);
    });
    const previousUser = localStorage.getItem(USER_STORAGE_KEY);
    if (state.users.some((user) => user.user_id === previousUser)) {
      userSelect.value = previousUser;
    }
    refreshUserState();
  } catch (error) {
    userSelect.replaceChildren();
    const option = document.createElement("option");
    option.textContent = "Could not load users";
    userSelect.append(option);
    showNotice(error.message, true);
  }
}

userSelect.addEventListener("change", refreshUserState);

reportType.addEventListener("change", () => {
  monthField.hidden = reportType.value !== "financial";
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearNotice();
  const question = document.querySelector("#question");
  const text = question.value.trim();
  if (!text) return;
  addMessage("user", text);
  question.value = "";
  setBusy(chatForm, true);
  try {
    const response = await api("/chat", {
      method: "POST",
      headers: { ...identityHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({ question: text }),
    });
    const payload = await response.json();
    rememberRun(payload.run_id);
    addMessage("agent", payload.answer);
  } catch (error) {
    addMessage("agent", `Unable to answer: ${error.message}`);
  } finally {
    setBusy(chatForm, false);
  }
});

reportForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearNotice();
  setBusy(reportForm, true);
  try {
    const type = reportType.value;
    const body = { format: document.querySelector("#report-format").value };
    if (type === "energy") {
      body.start = null;
      body.end = null;
    } else {
      const month = document.querySelector("#report-month").value;
      body.month = month ? Number(month) : null;
    }
    const response = await api(`/reports/${type}`, {
      method: "POST",
      headers: { ...identityHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const documentInfo = await response.json();
    rememberRun(documentInfo.run_id);
    state.documents.unshift(documentInfo);
    renderDocuments();
    showNotice(`Generated ${documentInfo.filename}.`);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    setBusy(reportForm, false);
  }
});

loadUsers();
