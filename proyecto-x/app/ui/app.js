const shell = document.querySelector(".app-shell");
const chat = document.querySelector("#chat");
const form = document.querySelector("#chatForm");
const input = document.querySelector("#messageInput");
const sendButton = document.querySelector("#sendButton");
const providerPill = document.querySelector("#providerPill");
const voiceButton = document.querySelector("#voiceButton");
const speakButton = document.querySelector("#speakButton");
const quickAddButton = document.querySelector("#quickAddButton");
const commandButton = document.querySelector("#commandButton");
const briefingButton = document.querySelector("#briefingButton");
const modal = document.querySelector("#modal");
const modalTitle = document.querySelector("#modalTitle");
const modalEyebrow = document.querySelector("#modalEyebrow");
const modalBody = document.querySelector("#modalBody");
const modalClose = document.querySelector("#modalClose");
const toast = document.querySelector("#toast");
const conversationList = document.querySelector("#conversationList");
const newChatButton = document.querySelector("#newChatButton");
const loginScreen = document.querySelector("#loginScreen");
const loginForm = document.querySelector("#loginForm");
const loginHelp = document.querySelector("#loginHelp");
const welcomeText = document.querySelector("#welcomeText");
const modelLine = document.querySelector("#modelLine");

let csrfToken = null;
let bootstrapRequired = false;
let conversations = [];
let activeConversationId = null;
let sending = false;
let lastAssistantText = "";
let voiceEnabled = false;
let currentProfile = null;
let messageAnimationIndex = 0;
const visualEngine = {
  planning: { className: "visual-planning", icon: "fa-diagram-project" },
  project: { className: "visual-project", icon: "fa-list-check" },
  activity: { className: "visual-activity", icon: "fa-wave-square" },
  "folder-create": { className: "visual-folder", icon: "fa-folder-plus" },
  "folder-file": { className: "visual-folder-file", icon: "fa-folder-tree" },
  "file-write": { className: "visual-write", icon: "fa-file-pen" },
  "file-scan": { className: "visual-scan", icon: "fa-magnifying-glass-chart" },
  research: { className: "visual-research", icon: "fa-globe" },
  code: { className: "visual-code", icon: "fa-code" },
  complete: { className: "visual-complete", icon: "fa-check" },
  error: { className: "visual-error", icon: "fa-triangle-exclamation" },
};

function escapeHtml(value) {
  return String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function nowTime() {
  return new Intl.DateTimeFormat("es-CO", { hour: "2-digit", minute: "2-digit" }).format(new Date());
}

function setState(state) {
  shell.dataset.state = state;
  if (modelLine) modelLine.textContent = state.toUpperCase();
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (csrfToken && /^(POST|PUT|PATCH|DELETE)$/i.test(options.method || "GET")) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(path, { credentials: "same-origin", ...options, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(data?.detail || text || `HTTP ${response.status}`);
  return data;
}

function renderText(value) {
  return escapeHtml(value).replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>");
}

function renderSuggestions(suggestions = []) {
  if (!suggestions.length) return "";
  return `<div class="suggestion-row">${suggestions.map((item) => `
    <button class="suggestion-chip" type="button" data-suggestion-prompt="${escapeHtml(item.prompt || "")}">
      <i class="fa-solid fa-bolt"></i><span>${escapeHtml(item.label || item.prompt || "Ejecutar")}</span>
    </button>
  `).join("")}</div>`;
}

function renderMessageBody(text, options = {}) {
  return `${renderText(text)}${renderSuggestions(options.suggestions || [])}<span class="message-meta">${options.time || nowTime()}</span>`;
}

function addMessage(role, text, options = {}) {
  shell.classList.add("has-chat");
  const node = document.createElement("article");
  node.className = `message ${role}`;
  node.dataset.anim = String((messageAnimationIndex++ % 4) + 1);
  node.innerHTML = renderMessageBody(text, options);
  chat.appendChild(node);
  node.scrollIntoView({ block: "end", behavior: "smooth" });
  return node;
}

function addActivity(label = "MARTES está analizando...") {
  shell.classList.add("has-chat");
  const node = document.createElement("article");
  node.className = "message assistant loading";
  node.innerHTML = `<span class="activity-indicator"><span class="activity-core"></span><span>${escapeHtml(label)}</span></span>`;
  chat.appendChild(node);
  node.scrollIntoView({ block: "end", behavior: "smooth" });
  return node;
}

function applyActivityVisual(node, visual) {
  const config = visualEngine[visual] || visualEngine.activity;
  node.dataset.visual = visual || "activity";
  node.classList.remove(...Object.values(visualEngine).map((item) => item.className));
  node.classList.add(config.className);
  const icon = node.querySelector(".activity-core");
  if (icon) icon.dataset.icon = config.icon;
}

function clearChat() {
  chat.innerHTML = "";
  shell.classList.remove("has-chat");
}

function showToast(text) {
  toast.textContent = text;
  toast.hidden = false;
  setTimeout(() => { toast.hidden = true; }, 5200);
}

function syncComposer() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  sendButton.disabled = sending || !input.value.trim();
}

function showLogin(show) {
  loginScreen.hidden = !show;
  loginScreen.style.display = show ? "" : "none";
  shell.classList.toggle("locked", show);
}

async function ensureSession() {
  try {
    const session = await api("/api/auth/me");
    csrfToken = session.csrf_token;
    showLogin(false);
    return true;
  } catch {
    await loadBootstrapStatus();
    showLogin(true);
    return false;
  }
}

async function loadBootstrapStatus() {
  try {
    const status = await api("/api/auth/bootstrap");
    bootstrapRequired = Boolean(status.required);
    document.querySelector("#loginEmail").value ||= status.email || "";
    loginForm.querySelector("button[type='submit']").textContent = bootstrapRequired
      ? "Crear acceso inicial"
      : "Entrar";
    loginHelp.textContent = bootstrapRequired
      ? "Primer acceso: escriba una contraseña nueva y única para crear el propietario local."
      : "Ingrese con la cuenta propietaria o una cuenta autorizada.";
  } catch {
    bootstrapRequired = false;
    loginHelp.textContent = "No pude comprobar el estado de acceso.";
  }
}

async function loadStatus() {
  const status = await api("/api/status");
  const available = Boolean(status.model.available);
  providerPill.textContent = available ? `${status.model.provider} · online` : `${status.model.provider || "modelo"} · offline`;
  providerPill.classList.toggle("online", available);
  providerPill.classList.toggle("offline", !available);
  setState(available ? "idle" : "offline");
}

async function loadWelcome() {
  try {
    const welcome = await api("/api/welcome");
    if (welcome.profile) currentProfile = welcome.profile;
    if (welcome.enabled && welcome.message) {
      welcomeText.textContent = welcome.message;
      setState(welcome.first_time ? "greeting" : "idle");
      setTimeout(() => {
        if (shell.dataset.state === "greeting") setState("idle");
      }, 1800);
    }
  } catch {
    welcomeText.textContent = "A sus órdenes, señor. ¿Qué necesita?";
  }
}

async function loadConversations(preferredId = null, options = {}) {
  conversations = await api("/api/conversations");
  activeConversationId = preferredId || activeConversationId || conversations[0]?.id || null;
  renderConversations();
  if (activeConversationId && options.open !== false) {
    await openConversation(activeConversationId, { refreshList: false });
  }
}

function renderConversations() {
  conversationList.innerHTML = conversations.map((item) => `
    <button class="conversation-item ${item.id === activeConversationId ? "active" : ""}"
      type="button" data-id="${escapeHtml(item.id)}" title="${escapeHtml(item.title)}">
      <span>${escapeHtml(item.title)}</span>
      <small>${new Date(item.updated_at).toLocaleDateString("es-CO", { day: "2-digit", month: "short" })}</small>
      <em data-menu="${escapeHtml(item.id)}">...</em>
    </button>
  `).join("");
}

async function openConversation(id, options = {}) {
  const payload = await api(`/api/conversations/${encodeURIComponent(id)}`);
  activeConversationId = id;
  clearChat();
  payload.messages.forEach((item) => addMessage(item.role, item.content, {
    time: new Date(item.created_at).toLocaleTimeString("es-CO", { hour: "2-digit", minute: "2-digit" }),
  }));
  renderConversations();
  if (options.refreshList !== false) await refreshConversationsOnly();
}

async function refreshConversationsOnly() {
  conversations = await api("/api/conversations");
  renderConversations();
}

async function createNewChat() {
  const item = await api("/api/conversations", { method: "POST", body: JSON.stringify({ title: "Nuevo chat" }) });
  activeConversationId = item.id;
  clearChat();
  await refreshConversationsOnly();
  input.focus();
}

function compactNotice(text) {
  if (text.includes("GROQ_API_KEY") || text.includes("Groq no esta configurado")) {
    return "Groq aún no tiene API key. Abra Configuración, pegue su clave de GroqCloud y pruebe la conexión.";
  }
  return text;
}

async function sendMessage() {
  const message = input.value.trim();
  if (!message || sending) return;
  if (!activeConversationId) await createNewChat();
  sending = true;
  input.value = "";
  syncComposer();
  addMessage("user", message);
  const activity = addActivity("Enviando mensaje...");
  const assistant = addMessage("assistant", "");
  assistant.hidden = true;
  let pendingSuggestions = [];
  let toolCompleted = false;
  setState("thinking");
  try {
    const response = await apiStream("/api/chat/stream", { message, conversation_id: activeConversationId }, (event) => {
      if (event.event === "state") {
        setState(event.state || "thinking");
        activity.classList.toggle("tool-running", ["executing", "researching", "planning", "verifying"].includes(event.state));
        applyActivityVisual(activity, event.visual);
        activity.querySelector(".activity-indicator span:last-child").textContent = event.label || "Recibiendo respuesta...";
      }
      if (event.event === "tool_done") {
        setState("idle");
        toolCompleted = true;
        pendingSuggestions = event.suggestions || pendingSuggestions;
        activity.classList.add("complete");
        activity.querySelector(".activity-indicator span:last-child").textContent = event.label || "Operación verificada.";
        setTimeout(() => {
          if (document.body.contains(activity)) activity.remove();
        }, 1350);
      }
      if (event.event === "token") {
        setState("responding");
        if (!toolCompleted && document.body.contains(activity)) activity.remove();
        assistant.hidden = false;
        assistant.dataset.raw = (assistant.dataset.raw || "") + event.text;
        assistant.innerHTML = renderMessageBody(assistant.dataset.raw, { suggestions: pendingSuggestions });
        lastAssistantText = assistant.dataset.raw;
        assistant.scrollIntoView({ block: "end", behavior: "smooth" });
      }
      if (event.event === "done") {
        pendingSuggestions = event.suggestions || pendingSuggestions;
        if (assistant.dataset.raw) {
          assistant.innerHTML = renderMessageBody(assistant.dataset.raw, { suggestions: pendingSuggestions });
        }
      }
      if (event.event === "notice") {
        activity.remove();
        assistant.remove();
        addMessage("system", compactNotice(event.notice));
      }
      if (event.event === "error") {
        setState("error");
        activity.classList.add("error");
        activity.remove();
        assistant.remove();
        addMessage("system", event.notice);
      }
      if (event.conversation_id) activeConversationId = event.conversation_id;
    });
    if (!response.hadToken && document.body.contains(activity)) activity.remove();
    if (!assistant.dataset.raw) assistant.remove();
    await refreshConversationsOnly();
    setState("idle");
    if (voiceEnabled && lastAssistantText) speak(lastAssistantText);
  } catch (error) {
    activity.remove();
    assistant.remove();
    addMessage("system", `No pude completar la solicitud: ${error.message}`);
    setState("error");
  } finally {
    sending = false;
    syncComposer();
    input.focus();
  }
}

async function apiStream(path, payload, onEvent) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 45000);
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken || "" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
  } catch (error) {
    window.clearTimeout(timeout);
    if (error.name === "AbortError") throw new Error("La solicitud tardó demasiado y fue detenida.");
    throw error;
  }
  if (!response.ok) {
    window.clearTimeout(timeout);
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `HTTP ${response.status}`);
  }
  if (!response.body) {
    window.clearTimeout(timeout);
    throw new Error("El servidor no abrió el canal de respuesta.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let hadToken = false;
  let finished = false;
  let failed = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        let event;
        try {
          event = JSON.parse(line);
        } catch (error) {
          throw new Error("El servidor envió una respuesta incompleta.");
        }
        if (event.event === "token") hadToken = true;
        if (event.event === "done") finished = true;
        if (event.event === "error") failed = true;
        onEvent(event);
      }
    }
  } catch (error) {
    if (error.name === "AbortError") throw new Error("La solicitud tardó demasiado y fue detenida.");
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
  if (buffer.trim()) throw new Error("El servidor cortó la respuesta antes de terminar.");
  if (!finished && !failed) throw new Error("La respuesta se interrumpió antes de finalizar.");
  return { hadToken };
}

function openModal(title, eyebrow, html) {
  modalTitle.textContent = title;
  modalEyebrow.textContent = eyebrow;
  modalBody.innerHTML = html;
  modal.showModal();
}

async function openBriefing() {
  const briefing = await api("/api/briefing");
  const timeline = briefing.timeline.length
    ? briefing.timeline.map((item) => `<li><strong>${escapeHtml(item.time)}</strong> · ${escapeHtml(item.type)} · ${escapeHtml(item.text)}</li>`).join("")
    : "<li>No hay actividad relevante para hoy.</li>";
  openModal("Briefing del día", "MARTES", `<div class="brief-grid">
    <section class="brief-card"><h3>Resumen</h3><p>${escapeHtml(briefing.summary)}</p></section>
    <section class="brief-card"><h3>Última conversación</h3><p>${escapeHtml(briefing.last_conversation.last_user_message || "Sin conversación previa útil.")}</p></section>
    <section class="brief-card"><h3>Estado</h3><p>Proveedor: ${escapeHtml(briefing.system.active_provider)}<br>Disponible: ${briefing.system.model_available ? "sí" : "no"}</p></section>
    <section class="brief-card" style="grid-column: 1 / -1"><h3>Cronología</h3><ul>${timeline}</ul></section>
  </div>`);
}

async function openCapabilities() {
  const payload = await api("/api/capabilities");
  const tools = payload.tools.map((tool) => `<section class="diagnostic-card">
    <h3>${escapeHtml(tool.description)}</h3>
    <p class="muted">${escapeHtml(tool.id)}</p>
    <p class="muted">${tool.available && tool.can_execute ? "Disponible" : "No disponible"} · riesgo ${escapeHtml(tool.risk_level)}</p>
  </section>`).join("");
  openModal("Capacidades reales", "Registro backend", `<div class="diagnostic-grid">${tools}</div>
    <p class="muted">Workspace autorizado: ${escapeHtml(payload.workspace_root)}</p>`);
}

async function openActivity() {
  const payload = await api("/api/activity");
  const events = payload.activity.length
    ? payload.activity.map((item) => `<li><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.status)}</span><p>${escapeHtml(item.detail || item.kind)}</p></li>`).join("")
    : "<li><strong>Sin actividad registrada</strong><span>IDLE</span><p>Aún no hay planes, proyectos ni ejecuciones guardadas.</p></li>";
  const projects = payload.projects.length
    ? payload.projects.map((item) => `<section class="diagnostic-card"><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.objective || item.description)}</p><p class="muted">${escapeHtml(item.status)} · prioridad ${escapeHtml(item.priority)}</p></section>`).join("")
    : "<p class='muted'>No hay proyectos guardados.</p>";
  const plans = payload.plans.length
    ? payload.plans.map((item) => `<section class="diagnostic-card">
      <h3>${escapeHtml(item.objective)}</h3>
      <p class="muted">${escapeHtml(item.status)} · siguiente: ${escapeHtml(item.next_action || "N/D")}</p>
      <div class="card-actions">
        <button class="ghost-action" data-resume-plan="${escapeHtml(item.id)}">Reanudar</button>
        <button class="ghost-action" data-cancel-plan="${escapeHtml(item.id)}">Cancelar</button>
      </div>
    </section>`).join("")
    : "<p class='muted'>No hay planes persistentes.</p>";
  const reminders = payload.reminders?.length
    ? payload.reminders.map((item) => `<section class="diagnostic-card"><h3>${escapeHtml(item.message)}</h3><p class="muted">${escapeHtml(item.status)} · ${escapeHtml(item.remind_at)} · ${escapeHtml(item.timezone)}</p></section>`).join("")
    : "<p class='muted'>No hay recordatorios pendientes.</p>";
  openModal("Centro de actividad", "MARTES CORE", `<div class="activity-panel">
    <ol class="activity-timeline">${events}</ol>
    <h3>Recordatorios</h3><div class="diagnostic-grid">${reminders}</div>
    <h3>Proyectos</h3><div class="diagnostic-grid">${projects}</div>
    <h3>Planes</h3><div class="diagnostic-grid">${plans}</div>
  </div>`);
}

async function openProfile() {
  const profile = await api("/api/profile");
  currentProfile = profile;
  const settings = profile.settings || {};
  openModal("Perfil personal", "Privacidad local", `<form class="settings-form" id="profileForm">
    <label>Nombre visible<input name="display_name" value="${escapeHtml(profile.display_name || "")}" placeholder="Cómo quiere que lo salude MARTES"></label>
    <label>Ciudad para clima<input name="city" value="${escapeHtml(profile.city || "")}" placeholder="Ej. Bogotá, Colombia"></label>
    <label class="check-row"><input name="welcome_enabled" type="checkbox" ${settings.welcome_enabled ? "checked" : ""}> Bienvenida al iniciar sesión</label>
    <label class="check-row"><input name="task_reminders_enabled" type="checkbox" ${settings.task_reminders_enabled ? "checked" : ""}> Recordatorios de tareas</label>
    <label class="check-row"><input name="contextual_suggestions_enabled" type="checkbox" ${settings.contextual_suggestions_enabled ? "checked" : ""}> Sugerencias contextuales</label>
    <label class="check-row"><input name="periodic_checks_enabled" type="checkbox" ${settings.periodic_checks_enabled ? "checked" : ""}> Comprobaciones periódicas</label>
    <label class="check-row"><input name="voice_enabled" type="checkbox" ${settings.voice_enabled ? "checked" : ""}> Voz</label>
    <label class="check-row"><input name="tone_analysis_enabled" type="checkbox" ${settings.tone_analysis_enabled ? "checked" : ""}> Análisis contextual del tono</label>
    <button type="submit" class="ghost-action">Guardar perfil</button>
    <p class="muted">La ciudad solo se usa para consultar Open-Meteo cuando usted la configura.</p>
  </form>`);
}

async function openWeatherPanel() {
  const profile = currentProfile || await api("/api/profile");
  if (!profile.city) return openProfile();
  try {
    const forecast = await api("/api/weather/current");
    openModal("Clima", "Open-Meteo", `<section class="brief-card">
      <h3>${escapeHtml(forecast.location || profile.city)}</h3>
      <p>${escapeHtml(forecast.temperature)}${escapeHtml(forecast.temperature_unit || "°C")} · ${escapeHtml(forecast.condition || "condición no disponible")}</p>
      <p class="muted">Precipitación: ${forecast.precipitation_probability ?? "N/D"}%<br>Actualizado: ${escapeHtml(forecast.updated_at)}<br>${escapeHtml(forecast.source)}</p>
      <a href="${escapeHtml(forecast.source_url)}" target="_blank" rel="noreferrer">Open-Meteo</a>
    </section>`);
  } catch (error) {
    showToast(error.message);
  }
}

async function openProviderSettings() {
  const settings = await api("/api/settings/provider");
  openModal("Proveedor de IA", "GroqCloud", `<form class="settings-form" id="groqSettingsForm">
    <label>Modo<select name="provider_mode">
      <option value="groq" ${settings.provider_mode === "groq" ? "selected" : ""}>Groq</option>
      <option value="auto" ${settings.provider_mode === "auto" ? "selected" : ""}>Automático</option>
      <option value="ollama" ${settings.provider_mode === "ollama" ? "selected" : ""}>Ollama</option>
    </select></label>
    <label>API key<input name="api_key" type="password" placeholder="${settings.groq.configured ? "Clave guardada; deje vacío para conservarla" : "Pegue su GROQ_API_KEY"}" autocomplete="off"></label>
    <label>Modelo<input name="model" value="${escapeHtml(settings.groq.model)}"></label>
    <label>Base URL<input name="base_url" value="${escapeHtml(settings.groq.base_url)}"></label>
    <label class="check-row"><input name="remote_memory_enabled" type="checkbox" ${settings.groq.remote_memory_enabled ? "checked" : ""}> Permitir memoria local en llamadas remotas</label>
    <button type="submit" class="ghost-action">Guardar y probar conexión</button>
    <p class="muted">La clave no se muestra en la interfaz ni en /api/status.</p>
  </form>`);
}

async function openDiagnostics() {
  const status = await api("/api/status");
  const providers = status.providers.map((provider) => `<section class="diagnostic-card">
    <h3>${escapeHtml(provider.provider)}</h3><p>${provider.available ? "Disponible" : "No disponible"}</p>
    <p class="muted">${escapeHtml(provider.message)}</p><p class="muted">Modelo: ${escapeHtml(provider.model)}</p>
  </section>`).join("");
  openModal("Diagnóstico", "MARTES", `<div class="diagnostic-grid">${providers}</div>`);
}

async function openMemory() {
  const items = await api("/api/memory");
  openModal("Memoria personal", "Control local", items.length
    ? `<div class="diagnostic-grid">${items.map((item) => `<section class="diagnostic-card"><h3>${escapeHtml(item.category)}</h3><p>${escapeHtml(item.content)}</p><p class="muted">${escapeHtml(item.source)} · confianza ${item.confidence}</p><button class="ghost-action" data-delete-memory="${escapeHtml(item.id)}">Eliminar</button></section>`).join("")}</div><button class="ghost-action" data-export-memory="1">Exportar perfil</button>`
    : "<p class='muted'>No hay recuerdos guardados.</p>");
}

function openUserCreate() {
  openModal("Nuevo usuario", "Solo propietario", `<form class="settings-form" id="userCreateForm">
    <label>Correo<input name="email" type="email" autocomplete="off" required></label>
    <label>Contraseña<input name="password" type="password" autocomplete="new-password" required></label>
    <label>Confirmar contraseña<input name="confirm_password" type="password" autocomplete="new-password" required></label>
    <button class="ghost-action" type="submit">Crear cuenta</button>
  </form>`);
}

function openCommands() {
  openModal("Funciones", "MARTES", `<div class="diagnostic-grid">
    <button class="ghost-action" data-command="capabilities">Capacidades reales</button>
    <button class="ghost-action" data-command="profile">Perfil y ciudad</button>
    <button class="ghost-action" data-command="weather">Clima actual</button>
    <button class="ghost-action" data-command="activity">Centro de actividad</button>
    <button class="ghost-action" data-command="briefing">Briefing del día</button>
    <button class="ghost-action" data-command="settings">Proveedor de IA</button>
    <button class="ghost-action" data-command="memory">Memoria personal</button>
    <button class="ghost-action" data-command="diagnostics">Diagnóstico</button>
    <button class="ghost-action" data-command="users">Agregar usuario</button>
    <button class="ghost-action" data-command="research">Preparar investigación</button>
  </div>`);
}

function speak(text) {
  if (!("speechSynthesis" in window)) return showToast("Este navegador no expone speechSynthesis.");
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "es-ES";
  utterance.rate = 1;
  utterance.volume = 0.9;
  setState("speaking");
  utterance.onend = () => setState("idle");
  window.speechSynthesis.speak(utterance);
}

form.addEventListener("submit", (event) => { event.preventDefault(); sendMessage(); });
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); }
});
input.addEventListener("input", syncComposer);
briefingButton.addEventListener("click", openCommands);
commandButton.addEventListener("click", openCommands);
quickAddButton.addEventListener("click", openCommands);
newChatButton.addEventListener("click", createNewChat);
chat.addEventListener("click", (event) => {
  const suggestion = event.target.closest("[data-suggestion-prompt]");
  if (!suggestion) return;
  input.value = suggestion.dataset.suggestionPrompt || "";
  syncComposer();
  sendMessage();
});
voiceButton.addEventListener("click", () => showToast("Transcripción remota con Groq queda documentada; el micrófono no se activa sin permiso explícito."));
speakButton.addEventListener("click", () => {
  voiceEnabled = !voiceEnabled;
  if (!voiceEnabled && "speechSynthesis" in window) window.speechSynthesis.cancel();
  showToast(voiceEnabled ? "Voz del navegador activada." : "Voz desactivada.");
});
loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api(bootstrapRequired ? "/api/auth/bootstrap" : "/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        email: document.querySelector("#loginEmail").value,
        password: document.querySelector("#loginPassword").value,
      }),
    });
    bootstrapRequired = false;
    if (await ensureSession()) await start();
  } catch (error) {
    showToast(error.message);
  }
});
conversationList.addEventListener("click", async (event) => {
  const menu = event.target.dataset?.menu;
  if (menu) {
    event.stopPropagation();
    const action = prompt("Escriba renombrar o eliminar:", "renombrar");
    if (action === "renombrar") {
      const title = prompt("Nuevo título:");
      if (title) await api(`/api/conversations/${encodeURIComponent(menu)}`, { method: "PUT", body: JSON.stringify({ title }) });
    }
    if (action === "eliminar" && confirm("Eliminar solo este chat y sus mensajes?")) {
      await api(`/api/conversations/${encodeURIComponent(menu)}`, { method: "DELETE" });
      activeConversationId = null;
      clearChat();
    }
    await loadConversations();
    return;
  }
  const item = event.target.closest(".conversation-item");
  if (item?.dataset.id) await openConversation(item.dataset.id);
});
modalClose.addEventListener("click", () => modal.close());
modalBody.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (event.target.id === "groqSettingsForm") {
    const data = new FormData(event.target);
    const result = await api("/api/settings/groq", { method: "POST", body: JSON.stringify({
      provider_mode: data.get("provider_mode"), api_key: data.get("api_key") || null,
      model: data.get("model"), base_url: data.get("base_url"),
      remote_memory_enabled: data.get("remote_memory_enabled") === "on",
    }) });
    await loadStatus();
    showToast(result.status.message);
  }
  if (event.target.id === "userCreateForm") {
    const data = new FormData(event.target);
    await api("/api/users", { method: "POST", body: JSON.stringify({
      email: data.get("email"), password: data.get("password"),
      confirm_password: data.get("confirm_password"),
    }) });
    modal.close();
    showToast("Cuenta creada sin mostrar la contraseña.");
  }
  if (event.target.id === "profileForm") {
    const data = new FormData(event.target);
    currentProfile = await api("/api/profile", { method: "PUT", body: JSON.stringify({
      display_name: data.get("display_name") || null,
      city: data.get("city") || null,
      welcome_enabled: data.get("welcome_enabled") === "on",
      task_reminders_enabled: data.get("task_reminders_enabled") === "on",
      contextual_suggestions_enabled: data.get("contextual_suggestions_enabled") === "on",
      periodic_checks_enabled: data.get("periodic_checks_enabled") === "on",
      voice_enabled: data.get("voice_enabled") === "on",
      tone_analysis_enabled: data.get("tone_analysis_enabled") === "on",
    }) });
    modal.close();
    showToast("Perfil actualizado.");
    await loadWelcome();
  }
});
modalBody.addEventListener("click", async (event) => {
  const command = event.target.dataset?.command;
  if (event.target.dataset?.deleteMemory) {
    await api(`/api/memory/${encodeURIComponent(event.target.dataset.deleteMemory)}`, { method: "DELETE" });
    return openMemory();
  }
  if (event.target.dataset?.exportMemory) {
    const items = await api("/api/memory");
    const blob = new Blob([JSON.stringify(items, null, 2)], { type: "application/json" });
    const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "martes-memoria.json" });
    link.click();
  }
  if (event.target.dataset?.resumePlan) {
    const planId = event.target.dataset.resumePlan;
    const result = await api(`/api/plans/${encodeURIComponent(planId)}/resume`, { method: "POST", body: JSON.stringify({}) });
    showToast(result.summary || "Plan reanudado.");
    return openActivity();
  }
  if (event.target.dataset?.cancelPlan) {
    const planId = event.target.dataset.cancelPlan;
    await api(`/api/plans/${encodeURIComponent(planId)}/cancel`, { method: "POST", body: JSON.stringify({}) });
    showToast("Plan cancelado.");
    return openActivity();
  }
  if (!command) return;
  modal.close();
  if (command === "briefing") openBriefing();
  if (command === "activity") openActivity();
  if (command === "capabilities") openCapabilities();
  if (command === "profile") openProfile();
  if (command === "weather") openWeatherPanel();
  if (command === "memory") openMemory();
  if (command === "diagnostics") openDiagnostics();
  if (command === "settings") openProviderSettings();
  if (command === "users") openUserCreate();
  if (command === "research") { input.value = "Investiga el siguiente tema con fuentes actuales y fiables: "; syncComposer(); input.focus(); }
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    openCommands();
  }
});

async function start() {
  await Promise.all([loadStatus(), loadWelcome(), loadConversations(null, { open: false })]);
}

ensureSession().then((ok) => {
  if (ok) start().catch((error) => showToast(error.message));
});
setInterval(() => {
  if (!loginScreen.hidden) return;
  loadStatus().catch(() => setState("offline"));
}, 30000);
syncComposer();
