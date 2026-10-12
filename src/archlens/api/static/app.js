// ArchLens web UI (M4.7): submit a repository, follow the run, show the report. It talks only to
// the API that serves it; the API key stays in this browser tab (sessionStorage). Everything the
// API returns is inserted as text; the report renders in a sandboxed iframe (no scripts).

const POLL_MS = 4000;
const KEY_SLOT = "archlens.apiKey";
const DOWNLOADS = [
  ["report.html", "HTML"],
  ["report.md", "Markdown"],
  ["assessment.json", "JSON"],
];
const MARKS = { pending: "○", running: "◔", done: "✓", failed: "✗", skipped: "–" };

const $ = (id) => document.getElementById(id);
let current = null; // the run on screen; answers about any other run are dropped
let timer = 0;

function remember(key) {
  try {
    sessionStorage.setItem(KEY_SLOT, key);
  } catch {
    // storage blocked: the key just stays in the field
  }
}

function recall() {
  try {
    return sessionStorage.getItem(KEY_SLOT) || "";
  } catch {
    return "";
  }
}

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

// fetch with the API key; throws an Error carrying `status` for any non-2xx answer.
async function api(path, init = {}) {
  const headers = { "X-API-Key": $("key").value.trim() };
  if (init.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...init, headers, cache: "no-store" });
  if (response.ok) return response;
  let detail = response.statusText;
  try {
    const body = await response.json();
    if (typeof body.detail === "string") detail = body.detail;
    else if (Array.isArray(body.detail)) detail = body.detail.map((d) => d.msg).join("; ");
  } catch {
    // not JSON
  }
  const error = new Error(`${response.status} ${detail}`);
  error.status = response.status;
  throw error;
}

const runPath = (runId, rest = "") => `/assessments/${encodeURIComponent(runId)}${rest}`;

$("submit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  remember($("key").value.trim());
  const body = { repo_url: $("repo").value.trim() };
  const ref = $("ref").value.trim();
  if (ref) body.ref = ref;
  const metrics = $("metrics").value.split(",").map((m) => m.trim()).filter(Boolean);
  if (metrics.length) body.metrics = metrics;
  $("submit").disabled = true;
  try {
    const response = await api("/assessments", { method: "POST", body: JSON.stringify(body) });
    follow((await response.json()).run_id);
  } catch (error) {
    showError(`Could not start the assessment: ${error.message}`);
  } finally {
    $("submit").disabled = false;
  }
});

$("open-form").addEventListener("submit", (event) => {
  event.preventDefault();
  follow($("run-id").value);
});

window.addEventListener("hashchange", () => follow(location.hash.slice(1)));

function follow(runId) {
  runId = runId.trim().toUpperCase();
  if (!runId) return;
  if (!$("key").value.trim()) {
    showError("Enter your API key first.");
    $("key").focus();
    return;
  }
  remember($("key").value.trim());
  showError("");
  current = runId;
  clearTimeout(timer);
  history.replaceState(null, "", `#${runId}`);
  $("run").hidden = false;
  $("run-id-label").textContent = runId;
  setStatus("loading");
  $("run-summary").textContent = "";
  $("stages").replaceChildren();
  $("report").hidden = true;
  $("report-frame").srcdoc = "";
  poll(runId);
}

async function poll(runId) {
  let state;
  try {
    state = await (await api(runPath(runId))).json();
  } catch (error) {
    if (runId !== current) return;
    if (!error.status || error.status >= 500) {
      // network trouble or the API scaling up: keep trying
      $("run-summary").textContent = `Connection problem (${error.message}); retrying…`;
      timer = setTimeout(() => poll(runId), POLL_MS);
    } else {
      current = null; // let the user try the same run again, e.g. with another key
      setStatus("error");
      showError(`Could not load run ${runId}: ${error.message}`);
    }
    return;
  }
  if (runId !== current) return;
  render(state);
  if (state.status === "done") await showReport(runId);
  else if (state.status !== "failed") timer = setTimeout(() => poll(runId), POLL_MS);
}

function setStatus(status) {
  $("run-status").textContent = status;
  $("run-status").className = `badge s-${status}`;
}

function render(state) {
  setStatus(state.status);
  const parts = [state.repo_url || "local repository"];
  if (state.ref) parts.push(`ref ${state.ref}`);
  if (state.metrics_done.length) parts.push(`${state.metrics_done.length} metric(s) evaluated`);
  parts.push(`LLM cost $${state.cost_usd.toFixed(4)}`);
  if (state.status === "queued") parts.push("waiting for a worker (starting one can take a minute)");
  $("run-summary").textContent = parts.join(" · ");
  $("stages").replaceChildren(...state.stages.map(stageItem));
}

function stageItem(stage) {
  const item = document.createElement("li");
  item.className = `s-${stage.status}`;
  item.append(span(MARKS[stage.status] || "?", "mark"), span(stage.stage));
  if (stage.started_at && stage.finished_at) {
    const seconds = (Date.parse(stage.finished_at) - Date.parse(stage.started_at)) / 1000;
    if (seconds >= 0) item.append(span(`${seconds.toFixed(1)} s`, "muted"));
  }
  if (stage.error) item.append(span(stage.error, "error-text"));
  return item;
}

function span(text, className = "") {
  const element = document.createElement("span");
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function showReport(runId) {
  let html;
  try {
    html = await (await api(runPath(runId, "/artifacts/report.html"))).text();
  } catch (error) {
    if (runId === current) showError(`The run finished but its report didn't load: ${error.message}`);
    return;
  }
  if (runId !== current) return;
  $("report-frame").srcdoc = html;
  $("downloads").replaceChildren(...DOWNLOADS.map(([name, label]) => download(runId, name, label)));
  $("report").hidden = false;
}

function download(runId, name, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary";
  button.textContent = label;
  button.title = `Download ${name}`;
  button.addEventListener("click", async () => {
    try {
      const blob = await (await api(runPath(runId, `/artifacts/${name}`))).blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `${runId}-${name}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 10_000);
    } catch (error) {
      showError(`Download failed: ${error.message}`);
    }
  });
  return button;
}

$("key").value = recall();
if (location.hash.length > 1) follow(location.hash.slice(1));
