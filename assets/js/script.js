// --- ÉTAT GLOBAL DE L'APPLICATION ---
        let isRegisterMode = false;
        let itemToDeleteId = null;

        // Jeux par défaut
        const defaultGames = [
            { id: 1, name: "Valorant", exe: "VALORANT-Win64-Shipping.exe", scene: "Valorant - InGame" },
            { id: 2, name: "Overwatch", exe: "Overwatch.exe", scene: "Overwatch - InGame" },
            { id: 3, name: "Rust", exe: "RustClient.exe", scene: "Rust - InGame" },
            { id: 4, name: "Minecraft", exe: "javaw.exe", scene: "Minecraft - InGame" }
        ];

        // Hotkeys par défaut (ex: Cache Map Rust)
        const defaultHotkeys = [
            { id: 1, game: "Rust", action: "Cache-Map automatique", key: "G", media: "map_overlay_mask.png" },
            { id: 2, game: "Valorant", action: "Masquer minimap", key: "Shift + Alt + M", media: "blur_minimap.png" }
        ];

        // --- INITIALISATION ---
        document.addEventListener('DOMContentLoaded', () => {
            initTheme();
            checkSession();
            setupAuthListeners();
        });

        // --- AUTHENTIFICATION LOCALE ---
        function setupAuthListeners() {
            const authForm = document.getElementById('auth-form');
            const authToggleBtn = document.getElementById('auth-toggle-btn');

            authToggleBtn.addEventListener('click', () => {
                isRegisterMode = !isRegisterMode;
                document.getElementById('auth-title').innerText = isRegisterMode ? "Créer un compte" : "Connexion";
                document.getElementById('auth-subtitle').innerText = isRegisterMode ? "Enregistrez votre compte en local" : "Accédez au centre de contrôle OBS Dynamics";
                document.getElementById('auth-btn-text').innerText = isRegisterMode ? "S'inscrire" : "Se Connecter";
                document.getElementById('auth-toggle-text').innerText = isRegisterMode ? "Déjà un compte ?" : "Pas encore de compte ?";
                authToggleBtn.innerText = isRegisterMode ? "Se connecter" : "S'inscrire";
            });

            authForm.addEventListener('submit', (e) => {
                e.preventDefault();
                const email = document.getElementById('email').value;
                const password = document.getElementById('password').value;

                if (isRegisterMode) {
                    localStorage.setItem('obs_user', JSON.stringify({ email, password }));
                    showToast("Compte créé avec succès !", "success");
                } else {
                    const user = JSON.parse(localStorage.getItem('obs_user'));
                    if (!user || user.email !== email || user.password !== password) {
                        showToast("Email ou mot de passe incorrect.", "danger");
                        return;
                    }
                }

                localStorage.setItem('obs_logged_in', 'true');
                localStorage.setItem('obs_current_user', email);
                openApp(email);
            });

            document.getElementById('logout-btn').addEventListener('click', () => {
                localStorage.removeItem('obs_logged_in');
                document.getElementById('app-container').style.display = 'none';
                document.getElementById('auth-wrapper').style.display = 'block';
                showToast("Déconnecté.", "info");
            });
        }

        function checkSession() {
            const isLoggedIn = localStorage.getItem('obs_logged_in');
            const currentUser = localStorage.getItem('obs_current_user');

            if (isLoggedIn === 'true' && currentUser) {
                openApp(currentUser);
            }
        }

        function openApp(email) {
            document.getElementById('auth-wrapper').style.display = 'none';
            const app = document.getElementById('app-container');
            app.style.display = 'flex';

            document.getElementById('user-display-email').innerText = email;

            loadGames();
            loadHotkeys();
            populateObsScenesMock();

            // Animation avec Anime.js
            anime({
                targets: '#app-container',
                opacity: [0, 1],
                translateY: [15, 0],
                duration: 500,
                easing: 'easeOutQuad'
            });
        }

        // --- NAVIGATION DANS LES ONGLETS ---
        function switchTab(tabId, btnElement) {
            document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));

            document.getElementById(tabId).classList.add('active');
            btnElement.classList.add('active');
        }

        // --- THÈME CLAIR / SOMBRE ---
        function initTheme() {
            const savedTheme = localStorage.getItem('obs_theme') || 'dark';
            document.documentElement.setAttribute('data-theme', savedTheme);
            updateThemeIcon(savedTheme);

            document.getElementById('theme-toggle').addEventListener('click', toggleTheme);
        }

        function toggleTheme() {
            const currentTheme = document.documentElement.getAttribute('data-theme');
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('obs_theme', newTheme);
            updateThemeIcon(newTheme);
            showToast(`Mode ${newTheme === 'dark' ? 'Sombre' : 'Clair'} activé`, 'info');
        }

        function updateThemeIcon(theme) {
            const icon = document.querySelector('#theme-toggle i');
            icon.className = theme === 'dark' ? 'fa-solid fa-moon' : 'fa-solid fa-sun';
        }

        // --- GESTION DES JEUX ---
        function getGames() {
            const data = localStorage.getItem('obs_games');
            return data ? JSON.parse(data) : defaultGames;
        }

        function saveGames(games) {
            localStorage.setItem('obs_games', JSON.stringify(games));
        }

        function loadGames() {
            const games = getGames();
            const container = document.getElementById('games-container');
            container.innerHTML = '';

            games.forEach(game => {
                const card = document.createElement('div');
                card.className = 'game-card';
                card.innerHTML = `
          <div>
            <div class="game-card-header">
              <div class="game-card-title">
                <h3>${game.name}</h3>
                <p><i class="fa-solid fa-gear"></i> ${game.exe}</p>
              </div>
              <span class="game-card-badge">Prêt</span>
            </div>
            <div class="game-card-body">
              <p><i class="fa-solid fa-tv"></i> Scène OBS: <strong>${game.scene || game.name}</strong></p>
            </div>
          </div>
          <div class="game-card-footer">
            <span style="font-size: 0.75rem; color: var(--text-muted);">
              <i class="fa-solid fa-image"></i> ${game.menuImagesCount || 0} Menu / ${game.ingameImagesCount || 0} Jeu
            </span>
            <div style="display: flex; gap: 0.5rem;">
              <button class="btn-icon-edit" onclick="openEditGameModal(${game.id})" title="Modifier">
                <i class="fa-solid fa-gear"></i>
              </button>
              <button class="btn-icon-danger" onclick="confirmDeleteGame(${game.id})" title="Supprimer">
                <i class="fa-solid fa-trash"></i>
              </button>
            </div>
          </div>
        `;
                container.appendChild(card);
            });
        }

        function openAddGameModal() {
            document.getElementById('add-game-modal').style.display = 'flex';
        }

        function scanGames() {
            logConsole('Scanning games...');
            showToast('Scan des jeux lancé.', 'info');
            // TODO: implement actual game scanning logic
        }

        function toggleObsGroupDropdown(checkbox) {
            const group = document.getElementById('obs-scene-select-group');
            group.style.display = checkbox.checked ? 'block' : 'none';
        }

        function populateObsScenesMock() {
            const select = document.getElementById('obs-target-scene');
            select.innerHTML = '';
            const scenes = ["Scène Principale", "Scène Gaming", "Overlay Stream", "In-Game Direct"];
            scenes.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s;
                opt.innerText = s;
                select.appendChild(opt);
            });
        }

        document.getElementById('add-game-form').addEventListener('submit', (e) => {
            e.preventDefault();
            const name = document.getElementById('game-name').value;
            const exe = document.getElementById('game-exe').value;
            
            const menuInput = document.getElementById('game-images-menu');
            const menuImagesCount = menuInput && menuInput.files ? menuInput.files.length : 0;
            
            const ingameInputs = document.querySelectorAll('#add-ingame-inputs-container .game-images-ingame');
            let ingameImagesCount = 0;
            ingameInputs.forEach(input => {
                if (input.files) ingameImagesCount += input.files.length;
            });

            const games = getGames();
            games.push({
                id: Date.now(),
                name,
                exe,
                scene: `${name} - Scene`,
                menuImagesCount,
                ingameImagesCount
            });

            saveGames(games);
            loadGames();
            closeModal('add-game-modal');
            document.getElementById('add-game-form').reset();
            logConsole(`Nouveau jeu configuré : ${name} (${exe})`);
            showToast(`Jeu ${name} ajouté avec succès !`, 'success');
        });

        // --- SUPPRESSION JEU ---
        function confirmDeleteGame(id) {
            itemToDeleteId = id;
            document.getElementById('delete-modal').style.display = 'flex';
        }

        document.getElementById('confirm-delete-btn').addEventListener('click', () => {
            if (itemToDeleteId !== null) {
                let games = getGames();
                games = games.filter(g => g.id !== itemToDeleteId);
                saveGames(games);
                loadGames();
                closeModal('delete-modal');
                logConsole(`Jeu ID ${itemToDeleteId} et fichiers JSON associés supprimés.`);
                showToast("Jeu et configurations supprimés.", "info");
            }
        });

        // --- TOUCHES PROGRAMMÉES (RUST CACHE-MAP...) ---
        function getHotkeys() {
            const data = localStorage.getItem('obs_hotkeys');
            return data ? JSON.parse(data) : defaultHotkeys;
        }

        function saveHotkeys(hotkeys) {
            localStorage.setItem('obs_hotkeys', JSON.stringify(hotkeys));
        }

        function loadHotkeys() {
            const hotkeys = getHotkeys();
            const container = document.getElementById('hotkeys-container');
            container.innerHTML = '';

            hotkeys.forEach(hk => {
                const div = document.createElement('div');
                div.className = 'hotkey-item';
                div.innerHTML = `
          <div class="hotkey-info">
            <h4>[${hk.game}] - ${hk.action}</h4>
            <p><i class="fa-solid fa-photo-film"></i> Overlay : ${hk.media || 'Aucun'}</p>
          </div>
          <div style="display: flex; align-items: center; gap: 1rem;">
            <span class="hotkey-badge">${hk.key}</span>
            <button class="btn-icon-danger" onclick="deleteHotkey(${hk.id})">
              <i class="fa-solid fa-trash"></i>
            </button>
          </div>
        `;
                container.appendChild(div);
            });
        }

        function openAddHotkeyModal() {
            const select = document.getElementById('hotkey-game');
            select.innerHTML = '';
            getGames().forEach(g => {
                const opt = document.createElement('option');
                opt.value = g.name;
                opt.innerText = g.name;
                select.appendChild(opt);
            });
            document.getElementById('add-hotkey-modal').style.display = 'flex';
        }

        document.getElementById('add-hotkey-form').addEventListener('submit', (e) => {
            e.preventDefault();
            const game = document.getElementById('hotkey-game').value;
            const action = document.getElementById('hotkey-action').value;
            const key = document.getElementById('hotkey-key').value;

            const hotkeys = getHotkeys();
            hotkeys.push({
                id: Date.now(),
                game,
                action,
                key,
                media: 'overlay_custom.png'
            });

            saveHotkeys(hotkeys);
            loadHotkeys();
            closeModal('add-hotkey-modal');
            document.getElementById('add-hotkey-form').reset();
            showToast("Raccourci ajouté !", "success");
        });

        function deleteHotkey(id) {
            let hotkeys = getHotkeys().filter(h => h.id !== id);
            saveHotkeys(hotkeys);
            loadHotkeys();
            showToast("Raccourci supprimé.", "info");
        }

        // --- CONTRÔLES SCRIPT ---
        function handleScriptControl(action) {
            const dot = document.getElementById('status-dot');
            const text = document.getElementById('status-text');

            if (action === 'start') {
                dot.className = 'status-dot active';
                text.innerText = 'Script en cours d\'exécution (Scan actif)';
                logConsole('Moteur OpenCV & psutil démarré.');
                showToast('Script lancé !', 'success');
            } else if (action === 'pause') {
                dot.className = 'status-dot';
                text.innerText = 'Script en Pause';
                logConsole('Script mis en pause.');
                showToast('Script en pause.', 'info');
            } else if (action === 'stop') {
                dot.className = 'status-dot stopped';
                text.innerText = 'Script arrêté';
                logConsole('Script complètement arrêté.');
                showToast('Script arrêté.', 'danger');
            } else if (action === 'folder') {
                logConsole('Ouverture du dossier racine sur le bureau...');
                showToast('Commande "Ouvrir Dossier" envoyée.', 'info');
            }
        }

        // --- UTILITAIRES ---
        function closeModal(id) {
            document.getElementById(id).style.display = 'none';
        }

        function logConsole(msg) {
            const box = document.getElementById('console-box');
            const time = new Date().toLocaleTimeString();
            const line = document.createElement('div');
            line.className = 'console-line';
            line.innerHTML = `<span class="console-timestamp">[${time}]</span> <span>${msg}</span>`;
            box.appendChild(line);
            box.scrollTop = box.scrollHeight;
        }

        function showToast(message, type = 'info') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = 'toast';

            let icon = 'fa-circle-info';
            if (type === 'success') icon = 'fa-circle-check';
            if (type === 'danger') icon = 'fa-triangle-exclamation';

            toast.innerHTML = `<i class="fa-solid ${icon}"></i> <span>${message}</span>`;
            container.appendChild(toast);

            setTimeout(() => {
                toast.remove();
            }, 3500);
        }

// Logique pour l'icône mot de passe
document.addEventListener('DOMContentLoaded', () => {
    const togglePassword = document.getElementById('toggle-password');
    const passwordInput = document.getElementById('password');
    if (togglePassword && passwordInput) {
        togglePassword.addEventListener('click', function () {
            const type = passwordInput.getAttribute('type') === 'password' ? 'text' : 'password';
            passwordInput.setAttribute('type', type);
            this.classList.toggle('fa-eye');
            this.classList.toggle('fa-eye-slash');
        });
    }
});


// --- EDITION JEU ---
let itemToEditId = null;
function openEditGameModal(id) {
    itemToEditId = id;
    const game = getGames().find(g => g.id === id);
    if (game) {
        document.getElementById('edit-game-name').value = game.name;
        document.getElementById('edit-game-exe').value = game.exe;
        document.getElementById('edit-game-modal').style.display = 'flex';
    }
}

document.getElementById('edit-game-form').addEventListener('submit', (e) => {
    e.preventDefault();
    if (itemToEditId !== null) {
        let games = getGames();
        const index = games.findIndex(g => g.id === itemToEditId);
        if (index > -1) {
            games[index].name = document.getElementById('edit-game-name').value;
            games[index].exe = document.getElementById('edit-game-exe').value;
            games[index].scene = games[index].name + ' - Scene';
            
            const editMenuInput = document.getElementById('edit-game-images-menu');
            if (editMenuInput && editMenuInput.files && editMenuInput.files.length > 0) {
                games[index].menuImagesCount = editMenuInput.files.length;
            }
            
            const editIngameInputs = document.querySelectorAll('#edit-ingame-inputs-container .game-images-ingame');
            let editIngameImagesCount = 0;
            editIngameInputs.forEach(input => {
                if (input.files) editIngameImagesCount += input.files.length;
            });
            if (editIngameImagesCount > 0) {
                games[index].ingameImagesCount = editIngameImagesCount;
            }

            saveGames(games);
            loadGames();
            closeModal('edit-game-modal');
            showToast('Jeu modifié avec succès !', 'success');
        }
    }
});

// --- DYNAMICAL FILE INPUTS ---
function addFileInput(containerId) {
    const container = document.getElementById(containerId);
    const input = document.createElement('input');
    input.type = 'file';
    input.className = 'form-control game-images-ingame';
    input.style.paddingLeft = '1rem';
    input.style.marginBottom = '0.5rem';
    input.accept = 'image/png';
    input.multiple = true;
    container.appendChild(input);
}
