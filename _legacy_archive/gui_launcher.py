"""
OBS Dynamics — GUI native professionnelle (CustomTkinter).
Sidebar + Dashboard (cartes jeux avec pastille d'état) + Paramètres (config OBS WebSocket).
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

import customtkinter as ctk
import requests
import uvicorn

# ============================================================================
# CHEMINS / CONSTANTES
# ============================================================================
def get_base_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).parent

BASE_DIR = get_base_path()
ENV_PATH = BASE_DIR / ".env"  # source de vérité backend (core/config.py, prefix OBS_)
API_BASE = "http://127.0.0.1:8000"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("gui_launcher")

# --- Palette (identité visuelle "OBS Dynamics" : cyan/dark) ---
COL_BG = "#0d1117"
COL_SIDEBAR = "#12151c"
COL_CARD = "#161b22"
COL_BORDER = "#22272e"
COL_ACCENT = "#00e0ff"
COL_ACCENT_HOVER = "#00b8d1"
COL_TEXT_MUTED = "#8b949e"
COL_GREEN = "#2ecc71"
COL_RED = "#e0455c"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# ============================================================================
# CONFIG MANAGER — persistance .env (OBS_WS_HOST / OBS_WS_PORT / OBS_WS_PASSWORD)
# Lit/écrit exactement les clés que core/config.py (pydantic-settings, prefix
# "OBS_") s'attend à trouver. Préserve les autres lignes du .env (ne les efface pas).
# ============================================================================
@dataclass
class OBSConfig:
    host: str = "localhost"
    port: int = 4455
    password: str = ""


ENV_KEYS = {"host": "OBS_WS_HOST", "port": "OBS_WS_PORT", "password": "OBS_WS_PASSWORD"}


class ConfigManager:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> OBSConfig:
        with self._lock:
            cfg = OBSConfig()
            if not self._path.exists():
                return cfg
            try:
                for line in self._path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, _, value = stripped.partition("=")
                    key = key.strip().upper()
                    value = value.strip().strip('"').strip("'")
                    if key == ENV_KEYS["host"]:
                        cfg.host = value
                    elif key == ENV_KEYS["port"]:
                        try:
                            cfg.port = int(value)
                        except ValueError:
                            logger.warning("OBS_WS_PORT invalide dans .env, valeur par défaut utilisée.")
                    elif key == ENV_KEYS["password"]:
                        cfg.password = value
            except OSError:
                logger.exception("Lecture .env échouée, valeurs par défaut utilisées.")
            return cfg

    def save(self, cfg: OBSConfig) -> bool:
        with self._lock:
            try:
                updates = {
                    ENV_KEYS["host"]: cfg.host,
                    ENV_KEYS["port"]: str(cfg.port),
                    ENV_KEYS["password"]: cfg.password,
                }
                seen = dict.fromkeys(updates, False)
                lines: list[str] = []

                if self._path.exists():
                    for line in self._path.read_text(encoding="utf-8").splitlines():
                        stripped = line.strip()
                        if "=" in stripped and not stripped.startswith("#"):
                            key = stripped.split("=", 1)[0].strip().upper()
                            if key in updates:
                                lines.append(f"{key}={updates[key]}")
                                seen[key] = True
                                continue
                        lines.append(line)

                for key, present in seen.items():
                    if not present:
                        lines.append(f"{key}={updates[key]}")

                tmp = self._path.with_suffix(".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.replace(self._path)  # écriture atomique
                return True
            except OSError:
                logger.exception("Échec sauvegarde .env.")
                return False


# ============================================================================
# SERVER MANAGER — thread uvicorn
# ============================================================================
class ServerManager:
    def __init__(self) -> None:
        self._server: Optional[uvicorn.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._start_exception: Optional[BaseException] = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> Optional[BaseException]:
        return self._start_exception

    def _run_server(self, server: uvicorn.Server) -> None:
        """Cible du thread serveur. Capture toute exception pour qu'elle soit
        exploitable côté GUI au lieu de disparaître silencieusement dans le thread."""
        try:
            server.run()
        except Exception as exc:  # noqa: BLE001 - on veut TOUT capturer ici
            self._start_exception = exc
            logger.exception("Le serveur uvicorn a planté.")

    def start(self) -> None:
        with self._lock:
            if self.is_running:
                return
            self._start_exception = None
            try:
                from app import app as fastapi_app  # import différé (peut être lent au 1er lancement)
            except Exception as exc:
                logger.exception("Échec import app.py")
                self._start_exception = exc
                raise

            config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=8000,
                                     log_level="warning", access_log=False)
            self._server = uvicorn.Server(config)
            self._thread = threading.Thread(
                target=self._run_server, args=(self._server,), daemon=True, name="uvicorn-server"
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._server is not None:
                self._server.should_exit = True
                if self._thread is not None:
                    self._thread.join(timeout=5)
                self._server = None
                self._thread = None

    def wait_until_ready(self, timeout: float = 20.0) -> bool:
        """Poll le backend jusqu'à ce qu'il réponde réellement (évite les délais
        fixes peu fiables: l'import initial peut prendre plusieurs secondes
        selon la machine). Retourne False si le délai est dépassé ou si le
        thread serveur est mort prématurément (voir last_error)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running:
                return False  # le thread serveur est mort prématurément (voir last_error)
            try:
                resp = requests.get(API_BASE, timeout=1.5)
                if resp.status_code < 500:
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.4)
        return False


# ============================================================================
# COMPOSANT : CARTE JEU avec pastille d'état (vert=actif / rouge=inactif)
# ============================================================================
class GameCard(ctk.CTkFrame):
    def __init__(self, master, name: str, exe: str, active: bool, **kwargs) -> None:
        super().__init__(master, fg_color=COL_CARD, corner_radius=12,
                          border_width=1, border_color=COL_BORDER, **kwargs)
        self.grid_columnconfigure(0, weight=1)

        icon = ctk.CTkLabel(self, text="🎮", font=ctk.CTkFont(size=26))
        icon.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 4))

        name_lbl = ctk.CTkLabel(self, text=name, font=ctk.CTkFont(size=15, weight="bold"),
                                 anchor="w")
        name_lbl.grid(row=1, column=0, sticky="w", padx=14)

        exe_lbl = ctk.CTkLabel(self, text=exe, font=ctk.CTkFont(size=11),
                                text_color=COL_TEXT_MUTED, anchor="w")
        exe_lbl.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 14))

        # Pastille d'état — coin supérieur droit, superposée via place()
        dot_color = COL_GREEN if active else COL_RED
        status_txt = "Actif" if active else "Inactif"
        dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=16),
                            text_color=dot_color)
        dot.place(relx=1.0, rely=0.0, x=-12, y=10, anchor="ne")
        tooltip = ctk.CTkLabel(self, text=status_txt, font=ctk.CTkFont(size=9),
                                text_color=dot_color)
        tooltip.place(relx=1.0, rely=0.0, x=-14, y=28, anchor="ne")


# ============================================================================
# VUE : DASHBOARD
# ============================================================================
class DashboardView(ctk.CTkFrame):
    def __init__(self, master, post_ui: Callable[[Callable[[], None]], None], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._post_ui = post_ui  # file d'attente thread-safe (voir App._pump_ui_queue)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 8))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Jeux détectés", font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=0, column=0, sticky="w")

        self.scan_btn = ctk.CTkButton(header, text="🔍 Scanner", width=130, height=34,
                                       fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                                       text_color="#0d1117", font=ctk.CTkFont(weight="bold"),
                                       command=self.refresh)
        self.scan_btn.grid(row=0, column=1, sticky="e")

        self.status_lbl = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11),
                                        text_color=COL_TEXT_MUTED, anchor="w")
        self.status_lbl.grid(row=1, column=0, sticky="w", padx=26)
        self.status_lbl.configure(
            text="Démarre le serveur (barre latérale) pour scanner les jeux.",
        )

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=20, pady=10)
        for c in range(3):
            self.scroll.grid_columnconfigure(c, weight=1)

        self._empty_state()
        self.scan_btn.configure(state="disabled")  # activé une fois le backend confirmé prêt

    def set_scan_enabled(self, enabled: bool) -> None:
        """Désactive le bouton Scanner tant que le backend n'est pas confirmé actif,
        pour éviter les erreurs de connexion sur un serveur pas encore prêt."""
        self.scan_btn.configure(state="normal" if enabled else "disabled")

    def _clear(self) -> None:
        for widget in self.scroll.winfo_children():
            widget.destroy()

    def _empty_state(self) -> None:
        self._clear()
        ctk.CTkLabel(self.scroll, text="Aucun jeu — lancez un scan.",
                     text_color=COL_TEXT_MUTED).grid(row=0, column=0, padx=10, pady=20)

    def refresh(self) -> None:
        self.scan_btn.configure(state="disabled", text="Scan...")
        self.status_lbl.configure(text="Analyse des processus en cours...")
        threading.Thread(target=self._fetch_games, daemon=True).start()

    def _fetch_games(self) -> None:
        games: list[dict] = []
        error: Optional[str] = None
        try:
            resp = requests.get(f"{API_BASE}/api/games/scan", timeout=8)
            resp.raise_for_status()
            games = resp.json()
        except requests.RequestException as exc:
            error = f"Backend indisponible : {exc}"
            logger.warning(error)
        # self.after() n'est pas garanti thread-safe depuis un thread hors mainloop:
        # on passe par la file d'attente pompée dans la mainloop (App._pump_ui_queue).
        self._post_ui(lambda: self._render_games(games, error))

    def _render_games(self, games: list[dict], error: Optional[str]) -> None:
        self.scan_btn.configure(state="normal", text="🔍 Scanner")
        self._clear()

        if error:
            self.status_lbl.configure(text=error, text_color=COL_RED)
            self._empty_state()
            return

        if not games:
            self.status_lbl.configure(text="Aucun jeu configuré.", text_color=COL_TEXT_MUTED)
            self._empty_state()
            return

        self.status_lbl.configure(
            text=f"{len(games)} jeu(x) — {sum(1 for g in games if g.get('active'))} actif(s)",
            text_color=COL_TEXT_MUTED,
        )
        for i, g in enumerate(games):
            card = GameCard(self.scroll, name=str(g.get("name", "?")),
                             exe=str(g.get("exe", "")), active=bool(g.get("active", False)))
            card.grid(row=i // 3, column=i % 3, sticky="nsew", padx=8, pady=8)


# ============================================================================
# VUE : PARAMÈTRES — connexion OBS WebSocket
# ============================================================================
class SettingsView(ctk.CTkFrame):
    def __init__(self, master, config_mgr: ConfigManager, on_saved: Callable[[], None], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.config_mgr = config_mgr
        self.on_saved = on_saved
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Paramètres", font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=24, pady=(24, 16))

        card = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14,
                             border_width=1, border_color=COL_BORDER)
        card.grid(row=1, column=0, sticky="ew", padx=24)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text="Connexion OBS WebSocket",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 12))

        self.host_var = ctk.StringVar()
        self.port_var = ctk.StringVar()
        self.pwd_var = ctk.StringVar()

        self._field(card, 1, "Adresse (host)", self.host_var)
        self._field(card, 2, "Port", self.port_var)
        self._password_field(card, 3, "Mot de passe", self.pwd_var)

        self.msg_lbl = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=11))
        self.msg_lbl.grid(row=4, column=0, columnspan=2, sticky="w", padx=20, pady=(4, 0))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=5, column=0, columnspan=2, sticky="ew", padx=20, pady=18)

        ctk.CTkButton(btn_row, text="💾 Enregistrer", width=150, height=36,
                       fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                       text_color="#0d1117", font=ctk.CTkFont(weight="bold"),
                       command=self._save).pack(side="left")

        self._load_into_form()

    def _field(self, parent, row: int, label: str, var: ctk.StringVar) -> None:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12),
                     text_color=COL_TEXT_MUTED).grid(row=row, column=0, sticky="w",
                                                      padx=20, pady=6)
        ctk.CTkEntry(parent, textvariable=var, width=240, height=34,
                     fg_color=COL_BG, border_color=COL_BORDER).grid(
            row=row, column=1, sticky="e", padx=20, pady=6)

    def _password_field(self, parent, row: int, label: str, var: ctk.StringVar) -> None:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12),
                     text_color=COL_TEXT_MUTED).grid(row=row, column=0, sticky="w",
                                                      padx=20, pady=6)
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.grid(row=row, column=1, sticky="e", padx=20, pady=6)

        self._pwd_entry = ctk.CTkEntry(wrapper, textvariable=var, width=200, height=34,
                                        show="•", fg_color=COL_BG, border_color=COL_BORDER)
        self._pwd_entry.pack(side="left")

        self._pwd_visible = False
        toggle_btn = ctk.CTkButton(wrapper, text="👁", width=34, height=34,
                                    fg_color=COL_BG, hover_color=COL_BORDER,
                                    command=self._toggle_pwd)
        toggle_btn.pack(side="left", padx=(4, 0))

    def _toggle_pwd(self) -> None:
        self._pwd_visible = not self._pwd_visible
        self._pwd_entry.configure(show="" if self._pwd_visible else "•")

    def _load_into_form(self) -> None:
        cfg = self.config_mgr.load()
        self.host_var.set(cfg.host)
        self.port_var.set(str(cfg.port))
        self.pwd_var.set(cfg.password)

    def _save(self) -> None:
        try:
            port = int(self.port_var.get())
            if not (0 < port <= 65535):
                raise ValueError("Port hors plage 1-65535")
        except ValueError as exc:
            self.msg_lbl.configure(text=f"⚠ Port invalide : {exc}", text_color=COL_RED)
            return

        cfg = self.config_mgr.load()
        cfg.host = self.host_var.get().strip() or "localhost"
        cfg.port = port
        cfg.password = self.pwd_var.get()

        if self.config_mgr.save(cfg):
            self.msg_lbl.configure(text="✓ Configuration enregistrée.", text_color=COL_GREEN)
            self.on_saved()
        else:
            self.msg_lbl.configure(text="✗ Échec de la sauvegarde.", text_color=COL_RED)


# ============================================================================
# SIDEBAR
# ============================================================================
class Sidebar(ctk.CTkFrame):
    def __init__(self, master, on_nav: Callable[[str], None],
                 on_start: Callable[[], None], on_stop: Callable[[], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_SIDEBAR, corner_radius=0, width=210, **kwargs)
        self.grid_propagate(False)
        self.grid_rowconfigure(6, weight=1)
        self.on_nav = on_nav

        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 28))
        ctk.CTkLabel(brand, text="⚡ OBS", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=COL_ACCENT).pack(side="left")
        ctk.CTkLabel(brand, text=" Dynamics", font=ctk.CTkFont(size=18, weight="bold")
                     ).pack(side="left")

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_btn("dashboard", "🏠  Tableau de bord", row=1)
        self._nav_btn("settings", "⚙️  Paramètres", row=2)

        # Contrôle serveur en bas de sidebar
        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.grid(row=6, column=0, sticky="sew", padx=16, pady=20)

        self.status_dot = ctk.CTkLabel(ctrl, text="●", text_color=COL_RED,
                                        font=ctk.CTkFont(size=14))
        self.status_dot.pack(anchor="w")
        self.status_text = ctk.CTkLabel(ctrl, text="Serveur arrêté", font=ctk.CTkFont(size=11),
                                         text_color=COL_TEXT_MUTED)
        self.status_text.pack(anchor="w", pady=(0, 10))

        self.start_btn = ctk.CTkButton(ctrl, text="▶ Démarrer", height=36,
                                        fg_color=COL_GREEN, hover_color="#27ae60",
                                        command=on_start)
        self.start_btn.pack(fill="x", pady=2)

        self.stop_btn = ctk.CTkButton(ctrl, text="■ Arrêter", height=36,
                                       fg_color=COL_RED, hover_color="#c0392b",
                                       command=on_stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=2)

    def _nav_btn(self, key: str, text: str, row: int) -> None:
        btn = ctk.CTkButton(self, text=text, anchor="w", height=40, corner_radius=8,
                             fg_color="transparent", hover_color=COL_CARD,
                             font=ctk.CTkFont(size=13),
                             command=lambda: self.on_nav(key))
        btn.grid(row=row, column=0, sticky="ew", padx=12, pady=3)
        self.nav_buttons[key] = btn

    def set_active(self, key: str) -> None:
        for k, btn in self.nav_buttons.items():
            btn.configure(fg_color=COL_CARD if k == key else "transparent")

    def set_server_state(self, running: bool) -> None:
        self.status_dot.configure(text_color=COL_GREEN if running else COL_RED)
        self.status_text.configure(text="Serveur actif" if running else "Serveur arrêté")
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")


# ============================================================================
# APPLICATION PRINCIPALE
# ============================================================================
class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OBS Dynamics")
        self.geometry("980x640")
        self.minsize(820, 560)
        self.configure(fg_color=COL_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.server = ServerManager()
        self.config_mgr = ConfigManager(ENV_PATH)
        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(self, on_nav=self._navigate,
                                on_start=self._start_server, on_stop=self._stop_server)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.views: dict[str, ctk.CTkFrame] = {
            "dashboard": DashboardView(self.content, post_ui=self.post_ui),
            "settings": SettingsView(self.content, self.config_mgr, on_saved=self._on_config_saved),
        }
        self._navigate("dashboard")
        self._pump_ui_queue()

    def post_ui(self, callback: Callable[[], None]) -> None:
        """Point d'entrée thread-safe pour qu'un thread d'arrière-plan demande
        une mise à jour de l'UI. Ne JAMAIS appeler directement de widgets
        Tkinter depuis un thread autre que la mainloop — passer par ici."""
        self._ui_queue.put(callback)

    def _pump_ui_queue(self) -> None:
        """Exécuté uniquement dans la mainloop: vide la file et applique les
        mises à jour UI en attente, puis se replanifie toutes les 50ms."""
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                logger.exception("Erreur dans un callback UI en file d'attente.")
        self.after(50, self._pump_ui_queue)

    def _navigate(self, key: str) -> None:
        for view in self.views.values():
            view.grid_forget()
        self.views[key].grid(row=0, column=0, sticky="nsew")
        self.sidebar.set_active(key)
        # Ne scanne que si le backend est démarré, sinon affiche un message clair
        if key == "dashboard":
            if self.server.is_running:
                self.views["dashboard"].refresh()
            else:
                self.views["dashboard"].status_lbl.configure(
                    text="⚠ Serveur arrêté — clique sur \"Démarrer\" dans la barre latérale.",
                    text_color=COL_RED,
                )

    def _start_server(self) -> None:
        # Démarrage entièrement en arrière-plan: l'import de app.py (FastAPI,
        # dépendances core/...) peut prendre 1-3s et gèlerait la mainloop Tkinter
        # si appelé directement depuis ce handler de clic.
        self.sidebar.start_btn.configure(state="disabled", text="Démarrage...")
        self.sidebar.status_text.configure(text="Démarrage en cours...")
        threading.Thread(target=self._start_server_bg, daemon=True).start()

    def _start_server_bg(self) -> None:
        try:
            self.server.start()
        except Exception as exc:
            logger.exception("Échec démarrage serveur")
            self.post_ui(lambda: self._on_start_failed(exc))
            return
        # Attend une réponse HTTP réelle plutôt qu'un délai fixe: l'import
        # initial peut prendre plusieurs secondes selon la machine.
        ready = self.server.wait_until_ready(timeout=25.0)
        if ready:
            self.post_ui(self._on_start_success)
        else:
            # Remonte la VRAIE cause si le thread serveur a planté, sinon timeout générique
            real_error = self.server.last_error
            exc = real_error if real_error is not None else RuntimeError(
                "Le serveur n'a pas répondu dans le délai imparti (25s)."
            )
            self.post_ui(lambda: self._on_start_failed(exc))

    def _on_start_success(self) -> None:
        self.sidebar.set_server_state(True)
        self.sidebar.start_btn.configure(text="▶ Démarrer")
        self.views["dashboard"].set_scan_enabled(True)
        webbrowser.open(API_BASE)
        self.views["dashboard"].refresh()

    def _on_start_failed(self, exc: Exception) -> None:
        self.sidebar.start_btn.configure(state="normal", text="▶ Démarrer")
        self.sidebar.status_text.configure(text=f"Erreur : {exc}")
        self.views["dashboard"].set_scan_enabled(False)
        logger.error("Démarrage serveur échoué : %s", exc)

    def _stop_server(self) -> None:
        self.sidebar.stop_btn.configure(state="disabled", text="Arrêt...")
        threading.Thread(target=self._stop_server_bg, daemon=True).start()

    def _stop_server_bg(self) -> None:
        self.server.stop()
        self.post_ui(self._on_stop_success)

    def _on_stop_success(self) -> None:
        self.sidebar.set_server_state(False)
        self.sidebar.stop_btn.configure(text="■ Arrêter")
        self.views["dashboard"].set_scan_enabled(False)

    def _on_config_saved(self) -> None:
        """Notifie le backend pour recharger la config OBS et reconnecter le WebSocket."""
        if not self.server.is_running:
            return
        threading.Thread(target=self._trigger_backend_reload, daemon=True).start()

    def _trigger_backend_reload(self) -> None:
        try:
            resp = requests.post(f"{API_BASE}/api/obs/reconnect", timeout=6)
            resp.raise_for_status()
            logger.info("Backend reconnecté avec la nouvelle configuration OBS.")
        except requests.RequestException:
            logger.exception("Échec reconnexion backend après sauvegarde config.")

    def _on_close(self) -> None:
        self.server.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
