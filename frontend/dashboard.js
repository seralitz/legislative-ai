const API = "";

// ── State ───────────────────────────────────────────────────────
let problems = [];
let selectedProblem = null;
let currentSort = "severity";
let pollInterval = null;

// ── DOM refs ────────────────────────────────────────────────────
const domainSelect     = document.getElementById("domain-select");
const runAuditBtn      = document.getElementById("run-audit-btn");
const statusBadge      = document.getElementById("status-badge");
const progressContainer= document.getElementById("progress-bar-container");
const progressBar      = document.getElementById("progress-bar");
const progressText     = document.getElementById("progress-text");
const emptyState       = document.getElementById("empty-state");
const tableContainer   = document.getElementById("results-table-container");
const tbody            = document.getElementById("problems-tbody");
const resultsCount     = document.getElementById("results-count");
const filterType       = document.getElementById("filter-type");
const filterSeverity   = document.getElementById("filter-severity");
const detailPanel      = document.getElementById("detail-panel");
const closeDetailBtn   = document.getElementById("close-detail");
const generateFixBtn   = document.getElementById("generate-fix-btn");
const fixResult        = document.getElementById("fix-result");
const copyFixBtn       = document.getElementById("copy-fix-btn");

// ── Type / Severity label maps ──────────────────────────────────
const TYPE_LABELS = {
  outdated:      "Устаревшее / Outdated",
  contradiction: "Противоречие / Contradiction",
  redundant:     "Избыточность / Redundant",
};

const SEVERITY_LABELS = {
  high:   "Высокая / High",
  medium: "Средняя / Medium",
  low:    "Низкая / Low",
};

const SEVERITY_ORDER = { high: 0, medium: 1, low: 2 };

// ── Init ────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  await loadDomains();
  bindEvents();
});

async function loadDomains() {
  try {
    const resp = await fetch(`${API}/api/domains`);
    const data = await resp.json();
    domainSelect.innerHTML = "";
    for (const d of data.domains) {
      const opt = document.createElement("option");
      opt.value = d.key;
      opt.textContent = d.label;
      domainSelect.appendChild(opt);
    }
  } catch (err) {
    console.error("Failed to load domains:", err);
    domainSelect.innerHTML = '<option value="здравоохранение">Здравоохранение / Healthcare</option>';
  }
}

function bindEvents() {
  runAuditBtn.addEventListener("click", startAudit);
  closeDetailBtn.addEventListener("click", closeDetail);
  generateFixBtn.addEventListener("click", requestFix);
  copyFixBtn.addEventListener("click", copyFix);
  filterType.addEventListener("change", renderTable);
  filterSeverity.addEventListener("change", renderTable);

  document.querySelectorAll(".sort-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      currentSort = btn.dataset.sort;
      document.querySelectorAll(".sort-btn").forEach(b => b.classList.remove("active-sort"));
      btn.classList.add("active-sort");
      renderTable();
    });
  });
}

// ── Audit Flow ──────────────────────────────────────────────────
async function startAudit() {
  const domain = domainSelect.value;
  if (!domain) return;

  setStatus("running");
  progressContainer.classList.remove("hidden");
  progressBar.style.width = "0%";
  progressText.textContent = "Запуск...";
  runAuditBtn.disabled = true;
  runAuditBtn.classList.add("opacity-50");
  problems = [];
  closeDetail();
  renderTable();

  try {
    await fetch(`${API}/api/audit/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain }),
    });

    pollInterval = setInterval(() => pollStatus(domain), 2000);
  } catch (err) {
    console.error("Failed to start audit:", err);
    setStatus("error");
    runAuditBtn.disabled = false;
    runAuditBtn.classList.remove("opacity-50");
  }
}

async function pollStatus(domain) {
  try {
    const resp = await fetch(`${API}/api/audit/status?domain=${encodeURIComponent(domain)}`);
    const status = await resp.json();

    if (status.total_batches > 0) {
      const pct = Math.round((status.completed_batches / status.total_batches) * 100);
      progressBar.style.width = `${pct}%`;
      progressText.textContent = `Пакет ${status.completed_batches}/${status.total_batches} — ${status.problems_found} проблем`;
    }

    if (status.status === "completed" || status.status === "error") {
      clearInterval(pollInterval);
      pollInterval = null;
      progressBar.style.width = "100%";
      runAuditBtn.disabled = false;
      runAuditBtn.classList.remove("opacity-50");

      if (status.status === "completed") {
        setStatus("completed");
        await loadResults(domain);
      } else {
        setStatus("error");
        progressText.textContent = status.error || "Ошибка";
      }
    }
  } catch (err) {
    console.error("Poll failed:", err);
  }
}

async function loadResults(domain) {
  try {
    const resp = await fetch(`${API}/api/audit/results?domain=${encodeURIComponent(domain)}&page_size=200`);
    const data = await resp.json();
    problems = data.problems || [];
    renderTable();
  } catch (err) {
    console.error("Failed to load results:", err);
  }
}

// ── Table Rendering ─────────────────────────────────────────────
function renderTable() {
  let filtered = [...problems];

  const typeFilter = filterType.value;
  const sevFilter = filterSeverity.value;
  if (typeFilter)  filtered = filtered.filter(p => p.problem_type === typeFilter);
  if (sevFilter)   filtered = filtered.filter(p => p.severity === sevFilter);

  filtered.sort((a, b) => {
    if (currentSort === "severity") return (SEVERITY_ORDER[a.severity] || 9) - (SEVERITY_ORDER[b.severity] || 9);
    if (currentSort === "type") return a.problem_type.localeCompare(b.problem_type);
    if (currentSort === "law") return a.law_title.localeCompare(b.law_title);
    return 0;
  });

  if (filtered.length === 0 && problems.length === 0) {
    emptyState.classList.remove("hidden");
    tableContainer.classList.add("hidden");
    return;
  }

  emptyState.classList.add("hidden");
  tableContainer.classList.remove("hidden");
  resultsCount.textContent = `(${filtered.length} из ${problems.length})`;

  tbody.innerHTML = "";
  for (const p of filtered) {
    const tr = document.createElement("tr");
    tr.className = "border-b border-gov-800/50";
    if (selectedProblem && selectedProblem.id === p.id) tr.classList.add("active-row");

    tr.innerHTML = `
      <td class="py-3 pl-3"><div class="problem-dot dot-${p.severity}"></div></td>
      <td class="py-3 px-2"><span class="badge badge-${p.severity}">${SEVERITY_LABELS[p.severity] || p.severity}</span></td>
      <td class="py-3 px-2"><span class="type-badge type-${p.problem_type}">${TYPE_LABELS[p.problem_type] || p.problem_type}</span></td>
      <td class="py-3 px-2 font-medium">${escapeHtml(p.law_title)}</td>
      <td class="py-3 px-2 text-gov-300">${escapeHtml(p.article)}</td>
      <td class="py-3 px-2 text-gov-400 max-w-xs truncate">${escapeHtml(p.description)}</td>
    `;
    tr.addEventListener("click", () => openDetail(p));
    tbody.appendChild(tr);
  }
}

// ── Detail Panel ────────────────────────────────────────────────
function openDetail(problem) {
  selectedProblem = problem;
  fixResult.classList.add("hidden");

  document.getElementById("detail-meta").innerHTML = `
    <div class="flex items-center gap-3">
      <span class="badge badge-${problem.severity}">${SEVERITY_LABELS[problem.severity]}</span>
      <span class="type-badge type-${problem.problem_type}">${TYPE_LABELS[problem.problem_type]}</span>
    </div>
    <div>
      <span class="text-gov-400 text-xs">Закон / Law:</span>
      <p class="font-medium">${escapeHtml(problem.law_title)}</p>
    </div>
    <div>
      <span class="text-gov-400 text-xs">Статья / Article:</span>
      <p class="font-medium">${escapeHtml(problem.article)}</p>
    </div>
  `;

  document.getElementById("detail-description").textContent = problem.description;

  const reasoningBlock = document.getElementById("detail-reasoning-block");
  const reasoningEl = document.getElementById("detail-legal-reasoning");
  if (problem.legal_reasoning) {
    reasoningEl.textContent = problem.legal_reasoning;
    reasoningBlock.classList.remove("hidden");
  } else {
    reasoningBlock.classList.add("hidden");
  }

  document.getElementById("detail-law-text").textContent = decodeHtml(problem.law_text) || "(Текст не загружен)";

  const affectedContainer = document.getElementById("detail-affected");
  affectedContainer.innerHTML = "";
  if (problem.affected_articles && problem.affected_articles.length > 0) {
    for (const art of problem.affected_articles) {
      const chip = document.createElement("span");
      chip.className = "article-chip";
      chip.textContent = art;
      affectedContainer.appendChild(chip);
    }
  } else {
    affectedContainer.innerHTML = '<span class="text-xs text-gov-500">—</span>';
  }

  detailPanel.classList.add("open");
  renderTable();
}

function closeDetail() {
  selectedProblem = null;
  detailPanel.classList.remove("open");
  renderTable();
}

// ── Fix Generation ──────────────────────────────────────────────
async function requestFix() {
  if (!selectedProblem) return;

  generateFixBtn.disabled = true;
  generateFixBtn.innerHTML = '<div class="spinner"></div> Генерация...';
  fixResult.classList.add("hidden");

  try {
    const resp = await fetch(`${API}/api/fix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        problem: selectedProblem,
        law_text: selectedProblem.law_text || "",
      }),
    });

    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const fix = await resp.json();

    document.getElementById("fix-preamble").textContent = fix.preamble;
    document.getElementById("fix-amendment-text").textContent = fix.amendment_text;
    document.getElementById("fix-justification").textContent = fix.justification;

    const affList = document.getElementById("fix-affected-list");
    affList.innerHTML = "";
    if (fix.affected_articles && fix.affected_articles.length > 0) {
      for (const art of fix.affected_articles) {
        const chip = document.createElement("span");
        chip.className = "article-chip";
        chip.textContent = art;
        affList.appendChild(chip);
      }
    } else {
      affList.innerHTML = '<span class="text-xs text-gov-500">—</span>';
    }

    fixResult.classList.remove("hidden");
  } catch (err) {
    console.error("Fix generation failed:", err);
    alert("Ошибка генерации исправления. Попробуйте снова.");
  } finally {
    generateFixBtn.disabled = false;
    generateFixBtn.innerHTML = `
      <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
      Предложить исправление / Generate Fix
    `;
  }
}

function copyFix() {
  const preamble      = document.getElementById("fix-preamble").textContent;
  const amendmentText = document.getElementById("fix-amendment-text").textContent;
  const justification = document.getElementById("fix-justification").textContent;
  const fullDoc = [preamble, amendmentText, justification].filter(Boolean).join("\n\n---\n\n");

  navigator.clipboard.writeText(fullDoc).then(() => {
    copyFixBtn.innerHTML = `
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"/>
      </svg>
      Скопировано! / Copied!
    `;
    setTimeout(() => {
      copyFixBtn.innerHTML = `
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"/>
        </svg>
        Копировать документ / Copy Document
      `;
    }, 2000);
  });
}

// ── Status Badge ────────────────────────────────────────────────
function setStatus(state) {
  const map = {
    idle:      { text: "Готов / Ready",      cls: "bg-gov-800 text-gov-300" },
    running:   { text: "Аудит... / Auditing", cls: "bg-accent-gold/20 text-accent-gold pulse-dot" },
    completed: { text: "Завершён / Complete", cls: "bg-accent-green/20 text-green-400" },
    error:     { text: "Ошибка / Error",      cls: "bg-accent-red/20 text-red-400" },
  };
  const info = map[state] || map.idle;
  statusBadge.className = `px-3 py-1 rounded-full text-xs font-medium ${info.cls}`;
  statusBadge.textContent = info.text;
}

// ── Utility ─────────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return "";
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function decodeHtml(str) {
  if (!str) return "";
  const ta = document.createElement("textarea");
  ta.innerHTML = str;
  return ta.value;
}
