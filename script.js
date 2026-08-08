// ==========================================================================
// OBS Dynamics — Frontend logic
// Talks to the FastAPI backend in app.py over the /api/* endpoints, using a
// cookie-based session (credentials: 'include' on every call).
// ==========================================================================

const API_BASE = '';
const state = {
  games: [],
  hotkeys: [],
  scriptState: 'stopped',
  logsSince: 0,
  pendingDeleteId: null,
  editingGameId: null,
  logsPoller: null,
};

// --------------------------------------------------------------------------
// API helper
// --------------------------------------------------------------------------
async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...options,
  });

  if (response.status === 401) {
    showAuthScreen();
    throw new Error('Non authentifié.');
  }

  if (!response.ok) {
    let detail = `Erreur ${response.status}`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = body.detail;
    } catch (_) {
      /* response had no JSON body */
    }
    throw new Error(detail);
  }

  if (response.status === 204) return null;
  const contentType = response.headers.get('content-type') || '';
  return contentType.includes('application/json') ? response.json() : null;
}

// --------------------------------------------------------------------------
// Toasts
// --------------------------------------------------------------------------
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const icons = { success: 'fa-circle-check', error: 'fa-circle-exclamation', info: 'fa-circle-info' };
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<i class="fa-solid ${icons[type] || icons.info}"></i><span></span>`;
  toast.querySelector('span').textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.25s ease';
    setTimeout(() => toast.remove(), 260);
  }, 4200);
}

// --------------------------------------------------------------------------
// Auth screen
// --------------------------------------------------------------------------
let authMode = 'login'; // 'login' | 'register'

function showAuthScreen() {
  stopLogsPolling();
  document.getElementById('auth-wrapper').classList.add('active');
  document.getElementById('app-container').classList.remove('active');
}

function showAppScreen() {
  document.getElementById('auth-wrapper').classList.remove('active');
  document.getElementById('app-container').classList.add('active');
  startLogsPolling();
  refreshStatus();
  loadGames();
  loadHotkeys();
}

function setAuthMode(mode) {
  authMode = mode;
  const title = document.getElementById('auth-title');
  const subtitle = document.getElementById('auth-subtitle');
  const btnText = document.getElementById('auth-btn-text');
  const toggleText = document.getElementById('auth-toggle-text');
  const toggleBtn = document.getElementById('auth-toggle-btn');
  const errorBox = document.getElementById('auth-error');
  if (errorBox) errorBox.classList.remove('visible');

  if (mode === 'register') {
    title.textContent = 'Créer un compte';
    subtitle.textContent = 'Configurez votre centre de contrôle OBS Dynamics';
    btnText.textContent = "S'inscrire";
    toggleText.textContent = 'Déjà un compte ?';
    toggleBtn.textContent = 'Se connecter';
  } else {
    title.textContent = 'Connexion';
    subtitle.textContent = 'Accédez au centre de contrôle OBS Dynamics';
    btnText.textContent = 'Se Connecter';
    toggleText.textContent = 'Pas encore de compte ?';
    toggleBtn.textContent = "S'inscrire";
  }
}

async function initAuthMode() {
  try {
    const data = await api('/api/auth/has-account');
    setAuthMode(data.has_account ? 'login' : 'register');
  } catch (_) {
    setAuthMode('login');
  }
}

async function handleAuthSubmit(event) {
  event.preventDefault();
  const email = document.getElementById('email').value.trim();
  const password = document.getElementById('password').value;
  const errorBox = document.getElementById('auth-error');
  const submitBtn = document.getElementById('auth-btn');

  submitBtn.disabled = true;
  try {
    const endpoint = authMode === 'register' ? '/api/auth/register' : '/api/auth/login';
    const data = await api(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    document.getElementById('user-display-email').textContent = data.user.email;
    showToast(authMode === 'register' ? 'Compte créé, bienvenue !' : 'Connexion réussie.', 'success');
    showAppScreen();
  } catch (err) {
    if (errorBox) {
      errorBox.textContent = err.message;
      errorBox.classList.add('visible');
    }
  } finally {
    submitBtn.disabled = false;
  }
}

async function handleLogout() {
  try {
    await api('/api/auth/logout', { method: 'POST' });
  } catch (_) {
    /* ignore network errors on logout */
  }
  showToast('Déconnecté.', 'info');
  showAuthScreen();
}

async function bootstrapSession() {
  try {
    const data = await api('/api/auth/me');
    document.getElementById('user-display-email').textContent = data.user.email;
    showAppScreen();
  } catch (_) {
    await initAuthMode();
    showAuthScreen();
  }
}

// --------------------------------------------------------------------------
// Tabs & theme
// --------------------------------------------------------------------------
function switchTab(tabId, btnEl) {
  document.querySelectorAll('.tab-content').forEach((el) => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach((el) => el.classList.remove('active'));
  document.getElementById(tabId).classList.add('active');
  if (btnEl) btnEl.classList.add('active');
}

function toggleTheme() {
  const html = document.documentElement;
  const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('obs-dynamics-theme', next);
  const icon = document.querySelector('#theme-toggle i');
  if (icon) icon.className = next === 'dark' ? 'fa-solid fa-moon' : 'fa-solid fa-sun';
}

function restoreTheme() {
  const saved = localStorage.getItem('obs-dynamics-theme');
  if (saved) {
    document.documentElement.setAttribute('data-theme', saved);
    const icon = document.querySelector('#theme-toggle i');
    if (icon) icon.className = saved === 'dark' ? 'fa-solid fa-moon' : 'fa-solid fa-sun';
  }
}

// --------------------------------------------------------------------------
// Script control (Lancer / Pause / Arrêter / Dossier)
// --------------------------------------------------------------------------
function renderStatus() {
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  dot.className = `status-dot ${state.scriptState}`;
  const labels = {
    running: 'Script en cours (analyse active)',
    paused: 'Script en pause',
    stopped: 'Script prêt (En attente)',
  };
  text.textContent = labels[state.scriptState] || 'Statut inconnu';
}

async function refreshStatus() {
  try {
    const data = await api('/api/status');
    state.scriptState = data.state;
    renderStatus();
  } catch (_) {
    /* handled by api() (redirects to auth on 401) */
  }
}

async function handleScriptControl(action) {
  const endpoints = {
    start: '/api/script/start',
    pause: '/api/script/pause',
    stop: '/api/script/stop',
    folder: '/api/script/folder',
  };
  const endpoint = endpoints[action];
  if (!endpoint) return;

  try {
    const data = await api(endpoint, { method: 'POST' });
    if (action === 'folder') {
      showToast('Dossier racine ouvert.', 'success');
      return;
    }
    state.scriptState = data.state;
    renderStatus();
    const messages = { start: 'Script lancé.', pause: 'Script en pause.', stop: 'Script arrêté.' };
    showToast(messages[action], 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function scanGames() {
  const btn = document.getElementById('scan-games-btn');
  btn.disabled = true;
  try {
    const data = await api('/api/scan', { method: 'POST' });
    const detected = data.results.filter((r) => r.detected);
    showToast(
      detected.length
        ? `${detected.length} jeu(x) détecté(s) à l'écran.`
        : 'Aucun jeu détecté actuellement.',
      detected.length ? 'success' : 'info'
    );
  } catch (err) {
    showToast(err.message, 'error');
  } finally {
    btn.disabled = false;
  }
}

// --------------------------------------------------------------------------
// Console / logs polling
// --------------------------------------------------------------------------
function appendConsoleLine(line) {
  const box = document.getElementById('console-box');
  if (!box) return;
  const div = document.createElement('div');
  div.className = `console-line level-${line.level}`;
  const time = new Date(line.timestamp * 1000).toLocaleTimeString('fr-FR');
  div.innerHTML = `<span class="console-timestamp">[${line.level} ${time}]</span><span></span>`;
  div.querySelector('span:last-child').textContent = line.message;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

async function pollLogs() {
  try {
    const data = await api(`/api/logs?since=${state.logsSince}`);
    for (const line of data.logs) {
      appendConsoleLine(line);
      state.logsSince = Math.max(state.logsSince, line.timestamp);
    }
  } catch (_) {
    /* silent: console polling shouldn't spam toasts */
  }
}

function startLogsPolling() {
  stopLogsPolling();
  pollLogs();
  state.logsPoller = setInterval(pollLogs, 2500);
}

function stopLogsPolling() {
  if (state.logsPoller) {
    clearInterval(state.logsPoller);
    state.logsPoller = null;
  }
}

// --------------------------------------------------------------------------
// Games
// --------------------------------------------------------------------------
async function loadGames() {
  try {
    const data = await api('/api/games');
    state.games = data.games;
    renderGames();
    populateHotkeyGameSelect();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderGames() {
  const container = document.getElementById('games-container');
  if (!state.games.length) {
    container.innerHTML = `<div class="empty-state"><i class="fa-solid fa-gamepad" style="font-size:1.6rem;display:block;margin-bottom:0.6rem;"></i>Aucun jeu configuré. Cliquez sur « Ajouter un jeu » pour commencer.</div>`;
    return;
  }
  container.innerHTML = state.games
    .map((game) => {
      const badges = [];
      if (game.create_scene) badges.push('<span class="badge"><i class="fa-solid fa-clapperboard"></i> Scène auto</span>');
      if (game.create_group) badges.push('<span class="badge"><i class="fa-solid fa-layer-group"></i> Groupe OBS</span>');
      const imgCount = (game.menu_images?.length || 0) + (game.ingame_images?.length || 0);
      badges.push(`<span class="badge"><i class="fa-solid fa-image"></i> ${imgCount} image(s)</span>`);
      return `
        <div class="game-card" data-id="${game.id}">
          <div class="game-card-header">
            <h3>${escapeHtml(game.name)}</h3>
            <div class="card-actions">
              <button class="icon-btn" title="Modifier" onclick="openEditGameModal('${game.id}')"><i class="fa-solid fa-pen"></i></button>
              <button class="icon-btn danger" title="Supprimer" onclick="openDeleteModal('${game.id}')"><i class="fa-solid fa-trash"></i></button>
            </div>
          </div>
          <span class="exe">${escapeHtml(game.exe)}</span>
          <div class="badge-row">${badges.join('')}</div>
        </div>`;
    })
    .join('');
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}

function openAddGameModal() {
  document.getElementById('add-game-form').reset();
  resetDynamicImageInputs('add-menu-inputs-container', 'game-images-menu');
  resetDynamicImageInputs('add-ingame-inputs-container', 'game-images-ingame');
  populateSceneSelect('obs-target-scene');
  openModal('add-game-modal');
}

async function openEditGameModal(gameId) {
  const game = state.games.find((g) => g.id === gameId);
  if (!game) return;
  state.editingGameId = gameId;
  document.getElementById('edit-game-name').value = game.name;
  document.getElementById('edit-game-exe').value = game.exe;
  resetDynamicImageInputs('edit-menu-inputs-container', 'game-images-menu');
  resetDynamicImageInputs('edit-ingame-inputs-container', 'game-images-ingame');
  openModal('edit-game-modal');
}

function openDeleteModal(gameId) {
  state.pendingDeleteId = gameId;
  openModal('delete-modal');
}

async function confirmDeleteGame() {
  if (!state.pendingDeleteId) return;
  try {
    await api(`/api/games/${state.pendingDeleteId}`, { method: 'DELETE' });
    showToast('Jeu supprimé.', 'success');
    closeModal('delete-modal');
    state.pendingDeleteId = null;
    await loadGames();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function handleAddGameSubmit(event) {
  event.preventDefault();
  const formData = new FormData();
  formData.append('name', document.getElementById('game-name').value.trim());
  formData.append('exe', document.getElementById('game-exe').value.trim());
  formData.append('create_scene', document.getElementById('opt-scene').checked);
  formData.append('create_group', document.getElementById('opt-group').checked);
  const targetScene = document.getElementById('obs-target-scene').value;
  if (targetScene) formData.append('target_scene', targetScene);

  appendFilesToFormData(formData, 'add-menu-inputs-container', 'menu_images');
  appendFilesToFormData(formData, 'add-ingame-inputs-container', 'ingame_images');

  try {
    await api('/api/games', { method: 'POST', body: formData });
    showToast('Jeu ajouté.', 'success');
    closeModal('add-game-modal');
    await loadGames();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function handleEditGameSubmit(event) {
  event.preventDefault();
  if (!state.editingGameId) return;
  const formData = new FormData();
  formData.append('name', document.getElementById('edit-game-name').value.trim());
  formData.append('exe', document.getElementById('edit-game-exe').value.trim());
  appendFilesToFormData(formData, 'edit-menu-inputs-container', 'menu_images');
  appendFilesToFormData(formData, 'edit-ingame-inputs-container', 'ingame_images');

  try {
    await api(`/api/games/${state.editingGameId}`, { method: 'PUT', body: formData });
    showToast('Jeu mis à jour.', 'success');
    closeModal('edit-game-modal');
    state.editingGameId = null;
    await loadGames();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function appendFilesToFormData(formData, containerId, fieldName) {
  const container = document.getElementById(containerId);
  container.querySelectorAll('input[type="file"]').forEach((input) => {
    Array.from(input.files || []).forEach((file) => formData.append(fieldName, file));
  });
}

// --------------------------------------------------------------------------
// Dynamic "add another image" inputs
// --------------------------------------------------------------------------
function addFileInput(containerId, className) {
  const container = document.getElementById(containerId);
  const input = document.createElement('input');
  input.type = 'file';
  input.className = `form-control ${className}`;
  input.accept = 'image/png';
  input.multiple = true;
  input.style.paddingLeft = '1rem';
  input.style.marginBottom = '0.5rem';
  container.appendChild(input);
}

function resetDynamicImageInputs(containerId, className) {
  const container = document.getElementById(containerId);
  container.innerHTML = '';
  const input = document.createElement('input');
  input.type = 'file';
  input.id = containerId.startsWith('edit') ? `edit-${className}` : className;
  input.className = `form-control ${className}`;
  input.accept = 'image/png';
  input.multiple = true;
  input.style.paddingLeft = '1rem';
  input.style.marginBottom = '0.5rem';
  container.appendChild(input);
}

// --------------------------------------------------------------------------
// OBS scenes
// --------------------------------------------------------------------------
async function populateSceneSelect(selectId) {
  const select = document.getElementById(selectId);
  select.innerHTML = '<option value="">Chargement des scènes…</option>';
  try {
    const data = await api('/api/obs/scenes');
    if (!data.connected) {
      select.innerHTML = '<option value="">OBS non connecté</option>';
      return;
    }
    select.innerHTML =
      '<option value="">— Aucune —</option>' +
      data.scenes.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
  } catch (_) {
    select.innerHTML = '<option value="">OBS non connecté</option>';
  }
}

function toggleObsGroupDropdown() {
  const group = document.getElementById('obs-scene-select-group');
  const wantsScene = document.getElementById('opt-scene').checked;
  const wantsGroup = document.getElementById('opt-group').checked;
  group.classList.toggle('visible', wantsScene || wantsGroup);
}

// --------------------------------------------------------------------------
// Hotkeys
// --------------------------------------------------------------------------
async function loadHotkeys() {
  try {
    const data = await api('/api/hotkeys');
    state.hotkeys = data.hotkeys;
    renderHotkeys();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderHotkeys() {
  const container = document.getElementById('hotkeys-container');
  if (!state.hotkeys.length) {
    container.innerHTML = `<div class="empty-state"><i class="fa-solid fa-keyboard" style="font-size:1.6rem;display:block;margin-bottom:0.6rem;"></i>Aucun raccourci programmé pour le moment.</div>`;
    return;
  }
  container.innerHTML = state.hotkeys
    .map((hotkey) => {
      const game = state.games.find((g) => g.id === hotkey.game_id);
      return `
        <div class="hotkey-card" data-id="${hotkey.id}">
          <span class="hotkey-key">${escapeHtml(hotkey.key)}</span>
          <div class="hotkey-meta">
            <strong>${escapeHtml(hotkey.action)}</strong>
            <span>${escapeHtml(game ? game.name : 'Jeu supprimé')}</span>
          </div>
          <button class="icon-btn danger" title="Supprimer" onclick="deleteHotkey('${hotkey.id}')"><i class="fa-solid fa-trash"></i></button>
        </div>`;
    })
    .join('');
}

function populateHotkeyGameSelect() {
  const select = document.getElementById('hotkey-game');
  if (!select) return;
  select.innerHTML = state.games
    .map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`)
    .join('');
}

function openAddHotkeyModal() {
  if (!state.games.length) {
    showToast('Ajoutez au moins un jeu avant de créer un raccourci.', 'error');
    return;
  }
  document.getElementById('add-hotkey-form').reset();
  populateHotkeyGameSelect();
  openModal('add-hotkey-modal');
}

async function handleAddHotkeySubmit(event) {
  event.preventDefault();
  const formData = new FormData();
  formData.append('game_id', document.getElementById('hotkey-game').value);
  formData.append('action', document.getElementById('hotkey-action').value.trim());
  formData.append('key', document.getElementById('hotkey-key').value.trim());
  const mediaInput = document.getElementById('hotkey-media');
  if (mediaInput.files[0]) formData.append('media', mediaInput.files[0]);

  try {
    await api('/api/hotkeys', { method: 'POST', body: formData });
    showToast('Raccourci ajouté.', 'success');
    closeModal('add-hotkey-modal');
    await loadHotkeys();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function deleteHotkey(hotkeyId) {
  try {
    await api(`/api/hotkeys/${hotkeyId}`, { method: 'DELETE' });
    showToast('Raccourci supprimé.', 'success');
    await loadHotkeys();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// --------------------------------------------------------------------------
// Modals
// --------------------------------------------------------------------------
function openModal(id) {
  document.getElementById(id).classList.add('active');
}

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
}

// --------------------------------------------------------------------------
// Init
// --------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  restoreTheme();

  document.getElementById('auth-form').addEventListener('submit', handleAuthSubmit);
  document.getElementById('auth-toggle-btn').addEventListener('click', () => {
    setAuthMode(authMode === 'login' ? 'register' : 'login');
  });
  document.getElementById('toggle-password').addEventListener('click', () => {
    const pwd = document.getElementById('password');
    const icon = document.getElementById('toggle-password');
    const show = pwd.type === 'password';
    pwd.type = show ? 'text' : 'password';
    icon.className = show ? 'fa-solid fa-eye-slash toggle-password' : 'fa-solid fa-eye toggle-password';
  });

  document.getElementById('theme-toggle').addEventListener('click', toggleTheme);
  document.getElementById('logout-btn').addEventListener('click', handleLogout);

  document.getElementById('opt-scene').addEventListener('change', toggleObsGroupDropdown);
  document.getElementById('opt-group').addEventListener('change', toggleObsGroupDropdown);

  document.getElementById('add-game-form').addEventListener('submit', handleAddGameSubmit);
  document.getElementById('edit-game-form').addEventListener('submit', handleEditGameSubmit);
  document.getElementById('add-hotkey-form').addEventListener('submit', handleAddHotkeySubmit);
  document.getElementById('confirm-delete-btn').addEventListener('click', confirmDeleteGame);

  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) overlay.classList.remove('active');
    });
  });

  bootstrapSession();
});
