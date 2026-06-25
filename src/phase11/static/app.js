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
const activityState = document.getElementById('activityState');
const planList = document.getElementById('planList');
const memoryList = document.getElementById('memoryList');
const toolList = document.getElementById('toolList');
const verificationList = document.getElementById('verificationList');

const stageLabels = {
  accepted: 'Request accepted',
  route: 'Intent routed',
  expert_routing: 'Experts selected',
  planning: 'Task graph planned',
  hierarchical_memory: 'Project memory retrieved',
  strict_memory: 'Strict memory ranked',
  vector_memory: 'Experience memory retrieved',
  agent_execution: 'Specialist agents executing',
  critic: 'Critic reviewing',
  reasoning_review: 'Reasoning reviewed',
  strict_stability: 'Role contracts verified',
  verifier: 'Verifier running',
  security: 'Security scan running',
  complete: 'Run complete',
};

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

function resetActivity() {
  activityState.textContent = 'Idle';
  [planList, memoryList, toolList, verificationList].forEach((list) => list.replaceChildren());
}

function appendActivity(list, text, status = 'complete') {
  const item = document.createElement('li');
  item.className = `activity-item ${status}`;
  item.textContent = text;
  list.appendChild(item);
}

function updateStage(payload) {
  const stage = payload.stage || 'running';
  const label = stageLabels[stage] || stage.replaceAll('_', ' ');
  const status = payload.status || 'running';
  activityState.textContent = label;
  const target = ['hierarchical_memory', 'strict_memory', 'vector_memory'].includes(stage)
    ? memoryList
    : ['verifier', 'security', 'critic', 'reasoning_review', 'strict_stability'].includes(stage)
      ? verificationList
      : ['route', 'expert_routing', 'agent_execution'].includes(stage)
        ? toolList
        : planList;
  const detail = payload.hits !== undefined
    ? `${label} · ${payload.hits} hits`
    : payload.confidence !== undefined
      ? `${label} · ${Math.round(payload.confidence * 100)}%`
      : label;
  appendActivity(target, detail, status);
}

function renderAgentReport(data) {
  planList.replaceChildren();
  memoryList.replaceChildren();
  toolList.replaceChildren();
  verificationList.replaceChildren();

  const lanes = data.meta_plan?.execution_lanes || [];
  lanes.forEach((lane) => appendActivity(planList, `${lane.lane}: ${lane.tasks.join(', ')}`));
  if (!lanes.length) appendActivity(planList, 'No meta-plan returned', 'warn');

  const strictHits = data.strict_stability?.memory_retrieval?.hits || [];
  strictHits.slice(0, 6).forEach((hit) => {
    appendActivity(memoryList, `${hit.entry.layer} · ${Math.round(hit.final_score * 100)}% · ${hit.entry.kind}`);
  });
  const vectorHits = data.vector_memory?.hits || [];
  vectorHits.slice(0, 4).forEach((hit) => appendActivity(memoryList, `experience · ${Math.round(hit.score * 100)}%`));
  if (!strictHits.length && !vectorHits.length) appendActivity(memoryList, 'No memory source selected', 'warn');

  const route = data.moe_route?.routes || [];
  route.forEach((item) => appendActivity(toolList, `${item.expert} · ${Math.round(item.score * 100)}%`));
  const artifacts = data.agent?.taskgraph_report?.artifacts || [];
  artifacts.slice(0, 8).forEach((item) => appendActivity(toolList, `${item.role}: ${item.task_id}`));

  appendActivity(
    verificationList,
    `Critic · ${Math.round((data.critic?.confidence || 0) * 100)}%`,
    data.critic?.ok ? 'complete' : 'warn'
  );
  const strictTrace = data.strict_stability?.reasoning_trace;
  if (strictTrace) {
    appendActivity(
      verificationList,
      `Strict stability · ${Math.round(strictTrace.confidence * 100)}%`,
      strictTrace.accepted ? 'complete' : 'warn'
    );
  }
  if (data.verifier) {
    appendActivity(verificationList, `Verifier · ${data.verifier.status} · ${data.verifier.score}`, data.verifier.status === 'fail' ? 'warn' : 'complete');
  }
  if (data.multilang_security) {
    appendActivity(
      verificationList,
      `Security · ${data.multilang_security.status} · ${data.multilang_security.findings.length} findings`,
      data.multilang_security.findings.length ? 'warn' : 'complete'
    );
  }
  activityState.textContent = data.status === 'complete' ? 'Complete' : 'Needs attention';
}

async function consumeEventStream(response, onEvent) {
  if (!response.body) throw new Error('Streaming response body is unavailable');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const {value, done} = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() || '';
    for (const frame of frames) {
      let event = 'message';
      const dataLines = [];
      frame.split(/\r?\n/).forEach((line) => {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      });
      if (!dataLines.length) continue;
      const raw = dataLines.join('\n');
      let payload;
      try { payload = JSON.parse(raw); } catch { payload = {text: raw}; }
      await onEvent(event, payload);
    }
    if (done) break;
  }
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
  resetActivity();
  activityState.textContent = 'Starting';
  const response = await fetch('/v1/agent/run/stream', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      prompt: text,
      workspace: workspaceInput.value,
      strict_stability: true,
      policy_mode: 'strict',
      meta_planning: true,
      hierarchical_memory: true,
      vector_memory: true,
      reasoning_review: true,
      run_verifier: true,
      run_security: true,
    }),
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(data.detail || JSON.stringify(data));
  }
  let finalReport = null;
  await consumeEventStream(response, async (event, payload) => {
    if (event === 'stage' || event === 'status') updateStage(payload);
    if (event === 'report') finalReport = payload;
    if (event === 'error') throw new Error(payload.error || 'Agent stream failed');
  });
  if (!finalReport) throw new Error('Agent completed without a final report');
  scoreValue.textContent = `${Math.round(finalReport.confidence * 100)}%`;
  renderAgentReport(finalReport);
  addMessage('assistant', `${finalReport.summary}\n\n${finalReport.final_answer}`);
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
      formatReport('Mythos V1 Release Gate', data.mythos_v1?.release_gate),
      formatReport('Mythos V1 Training Preflight', data.mythos_v1?.training_preflight),
      formatReport('Mythos V1 Backend Comparison', data.mythos_v1?.backend_comparison),
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
  resetActivity();
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
