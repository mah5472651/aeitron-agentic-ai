const state = {
  mode: 'chat',
  sessionId: null,
  runtime: null,
};

const messages = document.getElementById('messages');
const composer = document.getElementById('composer');
const promptBox = document.getElementById('prompt');
const sendButton = document.getElementById('send');
const clearButton = document.getElementById('clearButton');
const workspaceInput = document.getElementById('workspace');
const runtimeLabel = document.getElementById('runtimeLabel');
const backendValue = document.getElementById('backendValue');
const modelValue = document.getElementById('modelValue');
const scoreValue = document.getElementById('scoreValue');
const modeLabel = document.getElementById('modeLabel');
const viewTitle = document.getElementById('viewTitle');
const logoImage = document.querySelector('.brand-logo');

function addMessage(role, text) {
  const item = document.createElement('article');
  item.className = `message ${role}`;
  item.textContent = text;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

function setBusy(isBusy) {
  sendButton.disabled = isBusy;
  scoreValue.textContent = isBusy ? 'running' : 'ready';
}

function setMode(mode) {
  state.mode = mode;
  document.querySelectorAll('.segment').forEach((button) => {
    button.classList.toggle('active', button.dataset.mode === mode);
  });
  const titles = {
    chat: 'Agentic Coding Console',
    agent: 'Autonomous Build Runtime',
    security: 'Security Review Console',
    quality: 'Architecture Quality Scorecard',
  };
  modeLabel.textContent = mode[0].toUpperCase() + mode.slice(1);
  viewTitle.textContent = titles[mode];
}

async function loadRuntime() {
  const response = await fetch('/v1/runtime');
  const runtime = await response.json();
  state.runtime = runtime;
  workspaceInput.value = runtime.default_workspace;
  runtimeLabel.textContent = runtime.status;
  backendValue.textContent = runtime.backend;
  modelValue.textContent = runtime.model;
}

async function sendChat(text) {
  const response = await fetch('/v1/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      message: text,
      session_id: state.sessionId,
      workspace: workspaceInput.value,
      max_new_tokens: 900,
      temperature: 0.2,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || JSON.stringify(data));
  }
  state.sessionId = data.session_id;
  backendValue.textContent = data.backend;
  modelValue.textContent = data.model;
  addMessage('assistant', data.message);
}

async function runAgent(text) {
  const response = await fetch('/v1/agent/run', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      prompt: text,
      workspace: workspaceInput.value,
      allow_writes: false,
      allow_sandbox: true,
      context_token_budget: 12000,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || JSON.stringify(data));
  }
  scoreValue.textContent = `${Math.round(data.confidence * 100)}%`;
  addMessage('assistant', `${data.summary}\n\n${data.final_answer}`);
}

async function runSecurity(text) {
  const payload = text.trim()
    ? {text}
    : {workspace: workspaceInput.value};
  const response = await fetch('/v1/security/analyze', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || JSON.stringify(data));
  }
  scoreValue.textContent = `${Math.round(data.score * 100)}%`;
  const findings = data.findings
    .slice(0, 8)
    .map((item) => `- ${item.severity.toUpperCase()} ${item.title}${item.file_path ? ` (${item.file_path}:${item.line || ''})` : ''}`)
    .join('\n');
  addMessage('assistant', `${data.summary}${findings ? `\n\n${findings}` : ''}`);
}

function formatReport(label, report) {
  if (!report || !report.available) {
    return `${label}: unavailable`;
  }
  const score = report.score ?? report.candidate_score ?? 'n/a';
  const ready = report.architecture_ready ?? report.candidate_ready ?? report.passed ?? 'n/a';
  const categories = report.category_scores
    ? Object.entries(report.category_scores)
        .map(([name, value]) => `  - ${name}: ${value}`)
        .join('\n')
    : '';
  const recs = report.recommendations && report.recommendations.length
    ? `\nRecommendations:\n${report.recommendations.map((item) => `  - ${item}`).join('\n')}`
    : '';
  return `${label}\nRun: ${report.run_id || 'n/a'}\nScore: ${score}\nReady: ${ready}\nSummary: ${JSON.stringify(report.summary || {})}${categories ? `\n${categories}` : ''}${recs}`;
}

async function runQuality() {
  const response = await fetch('/v1/quality/latest');
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || JSON.stringify(data));
  }
  const phase13Score = data.phase13 && (data.phase13.candidate_score || data.phase13.score);
  scoreValue.textContent = phase13Score ? `${Math.round(phase13Score)}%` : 'report';
  addMessage(
    'assistant',
    [
      formatReport('Readiness', data.readiness),
      formatReport('Phase 12 Architecture Gauntlet', data.phase12),
      formatReport('Phase 13 Backend Quality', data.phase13),
    ].join('\n\n')
  );
}

document.querySelectorAll('.segment').forEach((button) => {
  button.addEventListener('click', () => setMode(button.dataset.mode));
});

clearButton.addEventListener('click', () => {
  messages.replaceChildren();
  state.sessionId = null;
  scoreValue.textContent = 'ready';
});

composer.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = promptBox.value.trim();
  if (!text && !['security', 'quality'].includes(state.mode)) {
    return;
  }
  if (text) {
    addMessage('user', text);
  }
  promptBox.value = '';
  setBusy(true);
  try {
    if (state.mode === 'agent') {
      await runAgent(text);
    } else if (state.mode === 'security') {
      await runSecurity(text);
    } else if (state.mode === 'quality') {
      await runQuality();
    } else {
      await sendChat(text);
    }
  } catch (error) {
    addMessage('error', `Request failed: ${error.message}`);
  } finally {
    setBusy(false);
    promptBox.focus();
  }
});

loadRuntime()
  .then(() => addMessage('assistant', 'Ready.'))
  .catch((error) => addMessage('error', `Runtime load failed: ${error.message}`));

if (logoImage) {
  logoImage.addEventListener('error', () => {
    logoImage.classList.add('missing');
    logoImage.removeAttribute('src');
  });
}
