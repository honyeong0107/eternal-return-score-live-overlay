const elements = {
  connection: document.getElementById('connection'),
  live: document.getElementById('live-score'),
  captureWindow: document.getElementById('capture-window'),
  refreshWindows: document.getElementById('refresh-windows'),
  overlayUrl: document.getElementById('overlay-url'),
  copyUrl: document.getElementById('copy-url'),
  previewFrame: document.getElementById('preview-frame'),
  preview: document.getElementById('preview'),
  select: document.getElementById('tournament-select'),
  form: document.getElementById('tournament-form'),
  newTournament: document.getElementById('new-tournament'),
  deleteTournament: document.getElementById('delete-tournament'),
  name: document.getElementById('tournament-name'),
  title: document.getElementById('board-title'),
  presetOptions: document.getElementById('preset-options'),
  customTheme: document.getElementById('custom-theme'),
  themeDetails: document.getElementById('theme-details'),
  accent: document.getElementById('accent-color'),
  text: document.getElementById('text-color'),
  muted: document.getElementById('muted-color'),
  ks: document.getElementById('ks-color'),
  rank: document.getElementById('rank-color'),
  rankText: document.getElementById('rank-text-color'),
  elimination: document.getElementById('elimination-color'),
  teamFields: document.getElementById('team-fields'),
  reset: document.getElementById('reset'),
  source: document.getElementById('source-status'),
  tracking: document.getElementById('tracking-status'),
  resolution: document.getElementById('resolution-status'),
  speed: document.getElementById('speed-status'),
  revision: document.getElementById('revision-status'),
  raw: document.getElementById('raw-state'),
  roundCurrent: document.getElementById('round-current'),
  roundSelect: document.getElementById('round-select'),
  completeRound: document.getElementById('complete-round'),
  undoRound: document.getElementById('undo-round'),
  roundEditor: document.getElementById('round-editor'),
  totalScores: document.getElementById('total-scores'),
  toast: document.getElementById('toast'),
};

const THEME_PRESETS = {
  'pastel-pink': {accent: '#f2a7c2', text: '#22171b', muted: '#6e5962', ks: '#8f4763', rank: '#f2a7c2', rankText: '#171214', elimination: '#c9879f'},
  'pastel-brown': {accent: '#c8a98a', text: '#211b16', muted: '#6e6258', ks: '#795b43', rank: '#c8a98a', rankText: '#181410', elimination: '#aa8b70'},
  'pastel-green': {accent: '#a8d5ba', text: '#142019', muted: '#52675a', ks: '#3f7654', rank: '#a8d5ba', rankText: '#102016', elimination: '#7fad90'},
  'pastel-blue': {accent: '#a8d8f0', text: '#111c22', muted: '#52656f', ks: '#3e718a', rank: '#a8d8f0', rankText: '#0e1a20', elimination: '#86aabd'},
  'pastel-red': {accent: '#f2a0a0', text: '#241616', muted: '#705858', ks: '#9c4545', rank: '#f2a0a0', rankText: '#1f1111', elimination: '#cb7474'},
};
const THEME_FIELDS = ['accent', 'text', 'muted', 'ks', 'rank', 'rankText', 'elimination'];

let tournaments = [];
let activeId = '';
let toastTimer;
let isTracking = false;
let roundOpen = true;
let lastState = null;
let roundsKey = '';

for (let index = 1; index <= 8; index += 1) {
  const label = document.createElement('label');
  label.className = 'team-field';
  label.innerHTML = `<b>${String(index).padStart(2, '0')}</b><input maxlength="40" aria-label="${index}번 팀 이름" required>`;
  elements.teamFields.append(label);
}

const teamInputs = [...elements.teamFields.querySelectorAll('input')];

async function api(path, options = {}) {
  const response = await fetch(path, {cache: 'no-store', ...options});
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || '요청을 처리하지 못했습니다.');
  return data;
}

function notify(message, isError = false) {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.className = `toast show${isError ? ' error' : ''}`;
  toastTimer = setTimeout(() => { elements.toast.className = 'toast'; }, 2800);
}

function fitPreview() {
  const scale = elements.previewFrame.clientWidth / 800;
  elements.preview.style.transform = `scale(${scale})`;
}

function setThemeValues(theme) {
  THEME_FIELDS.forEach((key) => { elements[key].value = theme[key]; });
}

function currentThemeValues() {
  return Object.fromEntries(THEME_FIELDS.map((key) => [key, elements[key].value.toLowerCase()]));
}

function updatePresetSelection() {
  const current = currentThemeValues();
  let matched = false;
  elements.presetOptions.querySelectorAll('[data-theme-preset]').forEach((button) => {
    const preset = THEME_PRESETS[button.dataset.themePreset];
    const selected = THEME_FIELDS.every((key) => preset[key] === current[key]);
    button.setAttribute('aria-pressed', String(selected));
    matched ||= selected;
  });
  elements.customTheme.dataset.custom = String(!matched);
}

function fillProfile(profile) {
  activeId = profile?.id || '';
  elements.deleteTournament.disabled = !activeId;
  elements.name.value = profile?.name || '새 대회';
  elements.title.value = profile?.theme?.title || 'LEADERBOARD';
  setThemeValues({
    accent: profile?.theme?.accent || THEME_PRESETS['pastel-blue'].accent,
    text: profile?.theme?.text || THEME_PRESETS['pastel-blue'].text,
    muted: profile?.theme?.muted || THEME_PRESETS['pastel-blue'].muted,
    ks: profile?.theme?.ks || THEME_PRESETS['pastel-blue'].ks,
    rank: profile?.theme?.rank || THEME_PRESETS['pastel-blue'].rank,
    rankText: profile?.theme?.rankText || THEME_PRESETS['pastel-blue'].rankText,
    elimination: profile?.theme?.elimination || THEME_PRESETS['pastel-blue'].elimination,
  });
  elements.themeDetails.hidden = true;
  elements.customTheme.setAttribute('aria-expanded', 'false');
  updatePresetSelection();
  teamInputs.forEach((input, index) => { input.value = profile?.teams?.[index] || `TEAM ${index + 1}`; });
  elements.select.value = activeId;
}

function renderTournamentSelect(data) {
  tournaments = data.tournaments;
  elements.select.replaceChildren(...tournaments.map((profile) => {
    const option = document.createElement('option');
    option.value = profile.id;
    option.textContent = profile.name;
    return option;
  }));
  fillProfile(tournaments.find((profile) => profile.id === data.activeTournament) || tournaments[0]);
}

async function loadTournaments() {
  renderTournamentSelect(await api('/api/tournaments'));
}

async function loadCaptureWindows() {
  const data = await api('/api/capture/windows');
  const monitor = document.createElement('option');
  monitor.value = 'monitor';
  monitor.textContent = '현재 모니터 화면';
  const options = data.windows.map((windowInfo) => {
    const option = document.createElement('option');
    option.value = windowInfo.hwnd;
    option.textContent = windowInfo.title;
    return option;
  });
  elements.captureWindow.replaceChildren(monitor, ...options);
  const selected = data.selected;
  elements.captureWindow.value = selected?.mode === 'window' ? selected.hwnd : 'monitor';
}

function scoreInput(value, label) {
  const input = document.createElement('input');
  input.type = 'number';
  input.min = '0';
  input.max = '999.5';
  input.step = '0.5';
  input.value = Number(value).toFixed(1);
  input.dataset.original = input.value;
  input.setAttribute('aria-label', label);
  return input;
}

function renderRoundEditor(state) {
  elements.roundEditor.replaceChildren();
  const selected = elements.roundSelect.value;
  const current = selected === `current-${state.round}` && state.roundOpen;
  const selectedRound = Number(selected.replace('completed-', ''));
  const completed = current ? null : state.completedRounds.find((item) => item.round === selectedRound);
  if (!current && !completed) return;

  const names = new Map(state.teams.map((team) => [team.team, team.name]));
  const form = document.createElement('form');
  form.className = `round-form${current ? ' current-round-form' : ''}`;
  const header = document.createElement('div');
  header.className = 'round-score-header';
  header.innerHTML = current
    ? '<span>팀</span><span>KS</span><span>TS</span>'
    : '<span>팀</span><span>KS</span><span>TS</span><span>패널티</span>';
  form.append(header);

  const scoreRows = current ? state.teams.map((team) => ({
    team: team.team,
    ts: team.roundTs,
    ks: team.roundKs,
  })) : completed.teams;
  scoreRows.forEach((team) => {
    const row = document.createElement('div');
    row.className = 'round-score-row';
    const name = document.createElement('strong');
    name.textContent = names.get(team.team);
    const roundNumber = current ? state.round : completed.round;
    const ts = scoreInput(team.ts, `${roundNumber}라운드 ${names.get(team.team)} TS`);
    const ks = scoreInput(team.ks, `${roundNumber}라운드 ${names.get(team.team)} KS`);
    ts.dataset.team = String(team.team);
    ts.dataset.score = 'ts';
    ks.dataset.team = String(team.team);
    ks.dataset.score = 'ks';
    row.append(name, ks, ts);
    if (!current) {
      const penalty = scoreInput(team.penalty || 0, `${completed.round}라운드 ${names.get(team.team)} 패널티`);
      penalty.dataset.team = String(team.team);
      penalty.dataset.score = 'penalty';
      row.append(penalty);
    }
    form.append(row);
  });

  const save = document.createElement('button');
  save.className = 'button primary round-save';
  save.type = 'submit';
  save.textContent = current ? `${state.round}라운드 실시간 점수 저장` : `${completed.round}라운드 점수 저장`;
  form.append(save);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const changedTeams = scoreRows.filter((team) => {
      const ts = form.querySelector(`[data-team="${team.team}"][data-score="ts"]`);
      const ks = form.querySelector(`[data-team="${team.team}"][data-score="ks"]`);
      const penalty = form.querySelector(`[data-team="${team.team}"][data-score="penalty"]`);
      return ts.value !== ts.dataset.original || ks.value !== ks.dataset.original
        || (penalty && penalty.value !== penalty.dataset.original);
    });
    if (!changedTeams.length) return;
    save.disabled = true;
    try {
      for (const team of changedTeams) {
        const ts = form.querySelector(`[data-team="${team.team}"][data-score="ts"]`);
        const ks = form.querySelector(`[data-team="${team.team}"][data-score="ks"]`);
        const penalty = form.querySelector(`[data-team="${team.team}"][data-score="penalty"]`);
        await api(current ? '/api/live-score/adjust' : '/api/rounds/adjust', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(current
            ? {team: team.team, ts: Number(ts.value), ks: Number(ks.value)}
            : {
              round: completed.round,
              team: team.team,
              ts: Number(ts.value),
              ks: Number(ks.value),
              penalty: Number(penalty.value),
            }),
        });
      }
      form.querySelectorAll('input').forEach((input) => { input.dataset.original = input.value; });
      roundsKey = '';
      await loadState();
      notify(current
        ? `${state.round}라운드 실시간 점수를 수정했습니다.`
        : `${completed.round}라운드 점수를 수정했습니다.`);
    } catch (error) {
      notify(error.message, true);
    } finally {
      save.disabled = false;
    }
  });
  elements.roundEditor.append(form);
}

function renderRounds(state) {
  const previousTournamentId = lastState?.tournament?.id;
  const tournamentChanged = previousTournamentId !== state.tournament.id;
  lastState = state;
  roundOpen = Boolean(state.roundOpen);
  elements.roundCurrent.textContent = `${state.round}라운드`;
  elements.completeRound.disabled = !roundOpen;
  elements.undoRound.disabled = !state.completedRounds.length || (roundOpen && state.teams.some((team) => {
    const carried = state.completedRounds.reduce((total, item) => {
      const saved = item.teams.find((entry) => entry.team === team.team);
      return total + Number(saved?.ts || 0) - Number(saved?.penalty || 0);
    }, 0);
    return Number(team.ts) !== carried;
  }));
  elements.completeRound.textContent = `${state.round}라운드 종료`;
  elements.live.disabled = !roundOpen;

  const nextKey = JSON.stringify({
    tournament: state.tournament.id,
    round: state.round,
    roundOpen: state.roundOpen,
    completed: state.completedRounds,
    current: state.teams.map((team) => [team.team, team.roundKs, team.roundTs]),
  });
  if (nextKey === roundsKey) return;
  const dirty = [...elements.roundEditor.querySelectorAll('input')]
    .some((input) => input.value !== input.dataset.original);
  if (dirty && !tournamentChanged) return;
  const previous = elements.roundSelect.value;
  roundsKey = nextKey;
  const options = [];
  if (state.roundOpen) {
    const option = document.createElement('option');
    option.value = `current-${state.round}`;
    option.textContent = `현재 ${state.round}라운드`;
    options.push(option);
  }
  options.push(...state.completedRounds.map((item) => {
    const option = document.createElement('option');
    option.value = `completed-${item.round}`;
    option.textContent = `${item.round}라운드`;
    return option;
  }));
  if (!options.length) {
    const option = document.createElement('option');
    option.textContent = '라운드 없음';
    options.push(option);
  }
  elements.roundSelect.replaceChildren(...options);
  elements.roundSelect.disabled = options.length === 1 && !options[0].value;
  const available = options.some((option) => option.value === previous);
  elements.roundSelect.value = available ? previous : (state.roundOpen
    ? `current-${state.round}`
    : `completed-${state.completedRounds.at(-1).round}`);
  renderRoundEditor(state);
}

function renderTotals(state) {
  const table = document.createElement('div');
  table.className = 'total-score-table';
  const header = document.createElement('div');
  header.className = 'total-score-header';
  header.innerHTML = '<span>팀</span><span>KS</span><span>TS</span>';
  table.append(header);
  state.teams.forEach((team, index) => {
    const row = document.createElement('div');
    row.className = 'total-score-row';
    const name = document.createElement('strong');
    name.textContent = team.name;
    const ts = document.createElement('span');
    ts.textContent = Number(team.ts).toFixed(1);
    const ks = document.createElement('span');
    ks.textContent = Number(team.ks).toFixed(1);
    row.append(name, ks, ts);
    table.append(row);
  });
  elements.totalScores.replaceChildren(table);
}

async function loadState() {
  try {
    const state = await api('/api/state');
    const source = state.capture?.mode === 'window'
      ? state.capture.title
      : (['starting', 'screen'].includes(state.health.source) ? '현재 모니터 화면' : state.health.source);
    isTracking = Boolean(state.tracking);
    elements.live.textContent = isTracking ? '라이브 스코어 종료' : '라이브 스코어 시작';
    elements.live.classList.toggle('tracking', isTracking);
    elements.connection.textContent = isTracking
      ? '추적 중 · 점수판이 실시간으로 갱신됩니다.'
      : '대기 중 · 시작하면 현재 경기 점수를 이어서 추적합니다.';
    elements.source.textContent = state.capture?.error ? '창 캡처 종료됨' : source;
    elements.tracking.textContent = isTracking ? '진행 중' : '대기 중';
    elements.resolution.textContent = state.health.resolutionOk ? '1920 × 1080' : '자동 변환 중';
    elements.speed.textContent = `${Number(state.health.processingMs).toFixed(1)} ms`;
    elements.revision.textContent = String(state.revision);
    elements.raw.textContent = JSON.stringify(state, null, 2);
    renderRounds(state);
    renderTotals(state);
  } catch (_) {
    elements.connection.textContent = '연결이 끊겼습니다. 프로그램을 다시 실행하세요.';
  }
}

elements.live.addEventListener('click', async () => {
  const stopping = isTracking;
  elements.live.disabled = true;
  elements.live.textContent = stopping ? '종료하는 중…' : '팀명과 점수 읽는 중…';
  try {
    const result = await api(stopping ? '/api/live-score/stop' : '/api/live-score/start', {method: 'POST'});
    if (!stopping) await loadTournaments();
    await loadState();
    notify(stopping
      ? '추적을 종료했습니다. 누적 점수는 그대로 유지됩니다.'
      : `${result.teams.length}개 팀의 라이브 스코어 추적을 시작했습니다.`);
  } catch (error) {
    notify(error.message, true);
  } finally {
    elements.live.disabled = !roundOpen;
    elements.live.textContent = isTracking ? '라이브 스코어 종료' : '라이브 스코어 시작';
  }
});

elements.captureWindow.addEventListener('change', async () => {
  elements.captureWindow.disabled = true;
  try {
    const monitor = elements.captureWindow.value === 'monitor';
    const result = await api('/api/capture/select', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(monitor
        ? {mode: 'monitor'}
        : {mode: 'window', hwnd: elements.captureWindow.value}),
    });
    await loadState();
    notify(monitor
      ? '현재 모니터 화면을 인식합니다.'
      : `${result.selected.title} 창을 인식합니다.`);
  } catch (error) {
    notify(error.message, true);
    await loadCaptureWindows();
  } finally {
    elements.captureWindow.disabled = false;
  }
});

elements.refreshWindows.addEventListener('click', async () => {
  elements.refreshWindows.disabled = true;
  try {
    await loadCaptureWindows();
    notify('실행 중인 창 목록을 새로고침했습니다.');
  } catch (error) {
    notify(error.message, true);
  } finally {
    elements.refreshWindows.disabled = false;
  }
});

elements.completeRound.addEventListener('click', async () => {
  const current = lastState?.round;
  if (!current || !confirm(`${current}라운드를 종료할까요?`)) return;
  elements.completeRound.disabled = true;
  try {
    await api('/api/rounds/complete', {method: 'POST'});
    await loadState();
    notify(`${current}라운드 점수를 저장했습니다.`);
  } catch (error) {
    notify(error.message, true);
  }
});

elements.undoRound.addEventListener('click', async () => {
  const previous = lastState?.completedRounds?.at(-1)?.round;
  if (!previous || !confirm(`${previous}라운드 종료를 되돌릴까요?`)) return;
  elements.undoRound.disabled = true;
  try {
    await api('/api/rounds/undo', {method: 'POST'});
    await loadState();
    notify(`${previous}라운드를 다시 진행합니다.`);
  } catch (error) {
    notify(error.message, true);
  }
});

elements.roundSelect.addEventListener('change', () => {
  if (lastState) renderRoundEditor(lastState);
});

elements.select.addEventListener('change', async () => {
  try {
    const result = await api('/api/tournaments/select', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: elements.select.value}),
    });
    fillProfile(result.profile);
    await loadState();
    notify(`${result.profile.name} 점수판으로 전환했습니다.`);
  } catch (error) {
    notify(error.message, true);
  }
});

elements.newTournament.addEventListener('click', () => {
  fillProfile(null);
  elements.name.focus();
});

elements.deleteTournament.addEventListener('click', async () => {
  if (!activeId) return;
  const profile = tournaments.find((item) => item.id === activeId);
  if (!profile || !confirm(`'${profile.name}' 대회를 삭제할까요?`)) return;
  elements.deleteTournament.disabled = true;
  try {
    await api('/api/tournaments/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({id: activeId}),
    });
    await loadTournaments();
    await loadState();
    notify(`'${profile.name}' 대회를 삭제했습니다.`);
  } catch (error) {
    notify(error.message, true);
  } finally {
    elements.deleteTournament.disabled = !activeId;
  }
});

elements.presetOptions.addEventListener('click', (event) => {
  const button = event.target.closest('[data-theme-preset]');
  if (!button) return;
  setThemeValues(THEME_PRESETS[button.dataset.themePreset]);
  elements.themeDetails.hidden = true;
  elements.customTheme.setAttribute('aria-expanded', 'false');
  updatePresetSelection();
});

elements.customTheme.addEventListener('click', () => {
  const willOpen = elements.themeDetails.hidden;
  elements.themeDetails.hidden = !willOpen;
  elements.customTheme.setAttribute('aria-expanded', String(willOpen));
});

THEME_FIELDS.forEach((key) => elements[key].addEventListener('input', updatePresetSelection));

elements.form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const profile = {
    id: activeId,
    name: elements.name.value,
    teams: teamInputs.map((input) => input.value),
    theme: {
      title: elements.title.value,
      accent: elements.accent.value,
      surface: '#ffffff',
      text: elements.text.value,
      muted: elements.muted.value,
      ks: elements.ks.value,
      rank: elements.rank.value,
      rankText: elements.rankText.value,
      line: '#000000',
      elimination: elements.elimination.value,
    },
  };
  try {
    const result = await api('/api/tournaments/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(profile),
    });
    await loadTournaments();
    fillProfile(result.profile);
    await loadState();
    notify('대회 설정을 저장했습니다.');
  } catch (error) {
    notify(error.message, true);
  }
});

elements.reset.addEventListener('click', async () => {
  if (!confirm('현재 점수와 전멸 상태를 모두 초기화할까요?')) return;
  try {
    await api('/api/reset', {method: 'POST'});
    await loadState();
    notify('새 경기를 시작할 준비가 됐습니다.');
  } catch (error) {
    notify(error.message, true);
  }
});

elements.copyUrl.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(elements.overlayUrl.textContent);
    notify('OBS 주소를 복사했습니다.');
  } catch (_) {
    notify('주소를 선택해 직접 복사해 주세요.', true);
  }
});

elements.overlayUrl.textContent = `${location.origin}/overlay`;
new ResizeObserver(fitPreview).observe(elements.previewFrame);
fitPreview();
Promise.all([loadTournaments(), loadCaptureWindows(), loadState()]).catch((error) => notify(error.message, true));
setInterval(loadState, 1000);
