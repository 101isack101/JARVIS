const UI_VERSION = 'v1.00';

const stateMeta = {
  idle: {
    label: 'LISTO',
    detail: 'Mantener Ctrl para hablar',
    core: 'Awaiting signal',
    dot: 'bg-cyan-300',
  },
  listening: {
    label: 'ESCUCHANDO',
    detail: 'Entrada de voz activa',
    core: 'Receiving voice input',
    dot: 'bg-cyan-300',
  },
  thinking: {
    label: 'PROCESANDO',
    detail: 'Analizando contexto',
    core: 'Running cognitive sweep',
    dot: 'bg-amber-300',
  },
  speaking: {
    label: 'RESPONDIENDO',
    detail: 'Salida de audio activa',
    core: 'Voice synthesis active',
    dot: 'bg-emerald-300',
  },
  blocked: {
    label: 'BLOQUEADO',
    detail: 'Revisar budget o aprobacion',
    core: 'Security hold',
    dot: 'bg-red-400',
  },
};

const el = {
  versionLabel: document.getElementById('versionLabel'),
  stateLabel: document.getElementById('stateLabel'),
  stateDetail: document.getElementById('stateDetail'),
  stateDot: document.getElementById('stateDot'),
  modePill: document.getElementById('modePill'),
  connectionStatus: document.getElementById('connectionStatus'),
  connectionBar: document.getElementById('connectionBar'),
  privacyPill: document.getElementById('privacyPill'),
  neuralCore: document.getElementById('neuralCore'),
  coreStateLabel: document.getElementById('coreStateLabel'),
  voiceWave: document.getElementById('voiceWave'),
  clock: document.getElementById('clock'),
  activityInline: document.getElementById('activityInline'),
  inputTranscript: document.getElementById('inputTranscript'),
  outputTranscript: document.getElementById('outputTranscript'),
  eventFeed: document.getElementById('eventFeed'),
  eventCount: document.getElementById('eventCount'),
  memoryOk: document.getElementById('memoryOk'),
  memoryActive: document.getElementById('memoryActive'),
  memoryErr: document.getElementById('memoryErr'),
  geminiBudget: document.getElementById('geminiBudget'),
  geminiBudgetFill: document.getElementById('geminiBudgetFill'),
  claudeBudget: document.getElementById('claudeBudget'),
  claudeBudgetFill: document.getElementById('claudeBudgetFill'),
  approvalModal: document.getElementById('approvalModal'),
  approvalTitle: document.getElementById('approvalTitle'),
  approvalRisk: document.getElementById('approvalRisk'),
  approvalDetails: document.getElementById('approvalDetails'),
  approvalCountdown: document.getElementById('approvalCountdown'),
  copyBtn: document.getElementById('copyBtn'),
  clearBtn: document.getElementById('clearBtn'),
  compactBtn: document.getElementById('compactBtn'),
  centerBtn: document.getElementById('centerBtn'),
  closeBtn: document.getElementById('closeBtn'),
  approveApproval: document.getElementById('approveApproval'),
  rejectApproval: document.getElementById('rejectApproval'),
};

let currentState = 'idle';
let voiceEnergy = 0.18;
let eventTotal = 0;
let compact = false;
let bridgeConnected = false;
let uiToken = '';
let approvalTimer = null;
let approvalRemaining = 30;
let activeApprovalId = null;

function initWaveBars() {
  el.voiceWave.innerHTML = '';
  for (let i = 0; i < 56; i += 1) {
    const bar = document.createElement('span');
    bar.style.opacity = `${0.35 + Math.random() * 0.45}`;
    el.voiceWave.appendChild(bar);
  }
}

function updateClock() {
  const now = new Date();
  el.clock.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function setState(state) {
  currentState = stateMeta[state] ? state : 'idle';
  const meta = stateMeta[currentState];
  el.stateLabel.textContent = meta.label;
  el.stateDetail.textContent = meta.detail;
  el.coreStateLabel.textContent = meta.core;
  el.stateDot.className = `status-dot ${meta.dot}`;
  el.neuralCore.classList.remove('state-idle', 'state-listening', 'state-thinking', 'state-speaking', 'state-blocked');
  el.neuralCore.classList.add(`state-${currentState}`);
}

function setMode(mode) {
  el.modePill.textContent = mode === 'LIBRE' ? 'Modo libre' : 'Modo PTT';
}

function setConnectionStatus(status, detail = '') {
  const labels = {
    connecting: 'Conectando',
    connected: 'Conectado',
    reconnecting: 'Reconectando',
    error: 'Error',
    stopped: 'Detenido',
  };
  el.connectionStatus.textContent = detail ? `${labels[status] || status}` : (labels[status] || status);
  el.connectionStatus.title = detail || '';
  el.connectionStatus.className = status === 'error'
    ? 'text-red-300'
    : status === 'reconnecting'
      ? 'text-amber-300'
      : status === 'stopped'
        ? 'text-cyan-100/45'
        : 'text-emerald-300';
  el.connectionBar.style.width = status === 'connected'
    ? '88%'
    : status === 'connecting'
      ? '44%'
      : status === 'error'
        ? '16%'
        : '32%';
}

function appendInput(text) {
  if (!text) return;
  el.inputTranscript.textContent = `${el.inputTranscript.textContent} ${text}`.trim();
}

function appendOutput(text) {
  if (!text) return;
  el.outputTranscript.textContent = `${el.outputTranscript.textContent}${text}`;
  el.outputTranscript.scrollTop = el.outputTranscript.scrollHeight;
}

function clearTranscripts() {
  el.inputTranscript.textContent = '';
  el.outputTranscript.textContent = '';
}

function copyTranscript() {
  const text = `TU:\n${el.inputTranscript.textContent}\n\nJARVIS:\n${el.outputTranscript.textContent}`;
  navigator.clipboard?.writeText(text);
  logEvent('Transcript copiado', 'ok');
}

function eventClass(level) {
  if (level === 'error') return 'event-line text-red-300';
  if (level === 'warn') return 'event-line text-amber-300';
  if (level === 'ok') return 'event-line text-emerald-300';
  return 'event-line';
}

function appendEventLine(stamp, message, level = 'info', prepend = true) {
  const line = document.createElement('div');
  line.className = eventClass(level);
  line.textContent = `${stamp} ${message}`;
  if (prepend) {
    el.eventFeed.prepend(line);
  } else {
    el.eventFeed.appendChild(line);
  }
  while (el.eventFeed.children.length > 8) el.eventFeed.lastChild.remove();
}

function logEvent(message, level = 'info') {
  if (!message) return;
  eventTotal += 1;
  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  appendEventLine(now, message, level, true);
  el.eventCount.textContent = String(eventTotal);
  el.activityInline.textContent = message;
}

function renderEvents(events = []) {
  el.eventFeed.innerHTML = '';
  events.slice(-8).forEach((event) => {
    appendEventLine(event.stamp || '--:--', event.message || '', event.level || 'info', false);
  });
  eventTotal = events.length;
  el.eventCount.textContent = String(eventTotal);
  const last = events[events.length - 1];
  if (last) el.activityInline.textContent = last.message;
}

function feedAudioLevel(level) {
  voiceEnergy = Math.max(0.05, Math.min(1, Number(level) || 0.05));
  if (currentState !== 'speaking') setState('speaking');
}

function feedAudioPcm(pcmValues) {
  if (!pcmValues || !pcmValues.length) return;
  let total = 0;
  const step = Math.max(1, Math.floor(pcmValues.length / 700));
  let count = 0;
  for (let i = 0; i < pcmValues.length; i += step) {
    total += pcmValues[i] * pcmValues[i];
    count += 1;
  }
  const rms = Math.sqrt(total / Math.max(1, count)) / 32768;
  feedAudioLevel(rms * 7.5);
}

function animateWave() {
  const bars = el.voiceWave.children;
  const activeBoost = currentState === 'speaking' ? 1 : currentState === 'listening' ? 0.55 : 0.22;
  const time = performance.now() / 320;
  for (let i = 0; i < bars.length; i += 1) {
    const phase = Math.sin(time + i * 0.42) * 0.5 + Math.sin(time * 0.52 + i * 0.17) * 0.5;
    const height = 8 + Math.abs(phase) * 56 * voiceEnergy * activeBoost;
    bars[i].style.height = `${height}px`;
    bars[i].style.opacity = `${0.25 + Math.min(0.75, voiceEnergy * activeBoost + Math.abs(phase) * 0.35)}`;
  }
  voiceEnergy = Math.max(0.12, voiceEnergy * 0.96);
  requestAnimationFrame(animateWave);
}

function showApproval(action = {}) {
  activeApprovalId = action.id || null;
  approvalRemaining = Math.round(action.timeout_s || 30);
  el.approvalTitle.textContent = action.title || 'Aprobacion requerida';
  el.approvalRisk.textContent = `Riesgo: ${action.risk || 'destructive'}`;
  el.approvalDetails.textContent = action.details || 'Accion sensible pendiente de confirmacion.';
  el.approvalCountdown.textContent = `Auto-rechazo en ${approvalRemaining}s`;
  el.approvalModal.classList.add('modal-open');
  clearInterval(approvalTimer);
  approvalTimer = setInterval(() => {
    approvalRemaining -= 1;
    el.approvalCountdown.textContent = `Auto-rechazo en ${Math.max(0, approvalRemaining)}s`;
    if (approvalRemaining <= 0) submitApproval(false);
  }, 1000);
}

function hideApproval(approved = false) {
  clearInterval(approvalTimer);
  approvalTimer = null;
  activeApprovalId = null;
  el.approvalModal.classList.remove('modal-open');
}

function submitApproval(approved) {
  if (!activeApprovalId || !bridgeConnected) {
    hideApproval(approved);
    logEvent(approved ? 'Accion aprobada' : 'Accion rechazada', approved ? 'ok' : 'warn');
    return;
  }
  const id = activeApprovalId;
  fetch('/approval', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Jarvis-Ui-Token': uiToken },
    body: JSON.stringify({ id, approved }),
  }).catch(() => {
    hideApproval(false);
    logEvent('No pude enviar decision de aprobacion', 'error');
  });
}

function toggleCompact() {
  compact = !compact;
  document.body.classList.toggle('compact-mode', compact);
  logEvent(compact ? 'Modo compacto activo' : 'Modo expandido activo');
}

function updateMemoryStats(stats = {}) {
  el.memoryOk.textContent = String(stats.ok || 0);
  el.memoryActive.textContent = String(stats.active || 0);
  el.memoryErr.textContent = String(stats.error || 0);
}

function budgetColor(status) {
  if (status === 'blocked') return 'linear-gradient(90deg, #ef4444, #fb7185)';
  if (status === 'alert') return 'linear-gradient(90deg, #f97316, #fbbf24)';
  if (status === 'warn') return 'linear-gradient(90deg, #fbbf24, #fde68a)';
  return 'linear-gradient(90deg, var(--cyan), var(--teal))';
}

function updateProviderBudget(labelEl, fillEl, provider = {}) {
  labelEl.textContent = provider.label || '$0.000/$0.00';
  fillEl.style.width = `${Math.max(0, Math.min(1, provider.pct || 0)) * 100}%`;
  fillEl.style.background = budgetColor(provider.status);
}

function updateBudget(budget = {}) {
  updateProviderBudget(el.geminiBudget, el.geminiBudgetFill, budget.gemini || {});
  updateProviderBudget(el.claudeBudget, el.claudeBudgetFill, budget.claude || {});
}

function applySnapshot(snapshot = {}) {
  if (snapshot.uiToken) uiToken = snapshot.uiToken;
  el.versionLabel.textContent = `Local Neural Interface ${snapshot.version || UI_VERSION}`;
  if (snapshot.privacy) el.privacyPill.textContent = snapshot.privacy;
  setState(snapshot.state || 'idle');
  setMode(snapshot.mode || 'PTT');
  setConnectionStatus(snapshot.connection?.status || 'connecting', snapshot.connection?.detail || '');
  el.inputTranscript.textContent = snapshot.inputTranscript || '';
  el.outputTranscript.textContent = snapshot.outputTranscript || '';
  renderEvents(snapshot.events || []);
  updateMemoryStats(snapshot.memory || {});
  updateBudget(snapshot.budget || {});
}

function bridgeCommand(command) {
  if (!bridgeConnected) return Promise.resolve(false);
  return fetch('/command', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Jarvis-Ui-Token': uiToken },
    body: JSON.stringify({ command }),
  }).then((r) => r.ok).catch(() => false);
}

function handleBridgeEvent(event) {
  const { command, args = [] } = event || {};
  const api = window.JARVIS_UI;
  if (command === 'snapshot') {
    applySnapshot(args[0] || {});
    return;
  }
  if (typeof api[command] === 'function') {
    api[command](...args);
  }
}

function connectBridge() {
  const forceDemo = new URLSearchParams(window.location.search).get('demo') === '1';
  if (forceDemo || window.location.protocol === 'file:') {
    runDemoSequence();
    return;
  }

  fetch('/state', { cache: 'no-store' })
    .then((response) => {
      if (!response.ok) throw new Error('state unavailable');
      return response.json();
    })
    .then((snapshot) => {
      bridgeConnected = true;
      applySnapshot(snapshot);
      const source = new EventSource('/events');
      source.onmessage = (message) => {
        try {
          handleBridgeEvent(JSON.parse(message.data));
        } catch (error) {
          logEvent('Evento UI invalido', 'error');
        }
      };
      source.onerror = () => {
        setConnectionStatus('reconnecting', 'UI bridge reconectando');
      };
    })
    .catch(() => {
      bridgeConnected = false;
      setConnectionStatus('connected');
      runDemoSequence();
    });
}

function runDemoSequence() {
  eventTotal = 3;
  el.inputTranscript.textContent = 'Jarvis, resume mi estado actual y revisa memoria.';
  el.outputTranscript.textContent = 'Listo. Estoy conectado, con memoria observable y Command Center preparado para revisar actividad local.';
  renderEvents([
    { stamp: '19:54', level: 'info', message: 'Overlay listo' },
    { stamp: '19:54', level: 'info', message: 'Gemini listo' },
    { stamp: '19:55', level: 'ok', message: 'Memoria: recall encontro 2' },
  ]);
  setConnectionStatus('connected');
  setTimeout(() => { setState('listening'); logEvent('Escucha activa', 'ok'); }, 800);
  setTimeout(() => { setState('thinking'); logEvent('Analizando contexto', 'warn'); }, 2400);
  setTimeout(() => {
    setState('speaking');
    let ticks = 0;
    const interval = setInterval(() => {
      ticks += 1;
      feedAudioLevel(0.18 + Math.abs(Math.sin(ticks * 0.22)) * 0.72);
      if (ticks > 180) {
        clearInterval(interval);
        setState('idle');
      }
    }, 55);
  }, 3900);
}

el.copyBtn.addEventListener('click', copyTranscript);
el.clearBtn.addEventListener('click', () => {
  if (!bridgeConnected) {
    clearTranscripts();
    logEvent('Transcript limpiado');
    return;
  }
  bridgeCommand('clearTranscripts');
});
el.compactBtn.addEventListener('click', toggleCompact);
el.centerBtn.addEventListener('click', () => {
  bridgeCommand('openDashboard');
  logEvent('Command Center enfocado');
});
el.closeBtn.addEventListener('click', () => {
  bridgeCommand('close');
  logEvent('Cierre solicitado', 'warn');
});
el.approveApproval.addEventListener('click', () => submitApproval(true));
el.rejectApproval.addEventListener('click', () => submitApproval(false));

document.querySelectorAll('.nav-button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.nav-button').forEach((b) => b.classList.remove('active'));
    button.classList.add('active');
    logEvent(`Vista ${button.textContent.trim()} seleccionada`);
  });
});

window.JARVIS_UI = {
  applySnapshot,
  setState,
  setMode,
  setConnectionStatus,
  appendInput,
  appendOutput,
  clearTranscripts,
  logEvent,
  feedAudioLevel,
  feedAudioPcm,
  showApproval,
  hideApproval,
  toggleCompact,
  updateMemoryStats,
  updateBudget,
};

el.versionLabel.textContent = `Local Neural Interface ${UI_VERSION}`;
initWaveBars();
setState('idle');
setMode('PTT');
setConnectionStatus('connecting');
updateClock();
setInterval(updateClock, 1000);
animateWave();
connectBridge();
