"""Chat Twitch : persistance, jeton permanent, parsing IRC, hub et routes."""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from twitch_chat import (PLATFORMS, BaseConnector, ChatHub, ChatMessage,
                         TwitchChatStore, PlatformConfig, Status, TwitchConnector,
                         parse_irc_tags, parse_privmsg)
import secret_store
from overlay_server import OverlayServer


@pytest.fixture
def store(tmp_path: Path) -> TwitchChatStore:
    return TwitchChatStore(tmp_path / "multistream.json")


# --- Persistance ---------------------------------------------------------- #

def test_defaults_when_file_absent(store):
    cfg = store.load()
    assert cfg.overlay_token == ""
    assert set(cfg.platforms) == set(PLATFORMS)
    assert all(cfg.platform(p).enabled for p in PLATFORMS)


def test_roundtrip_preserves_the_platform(store):
    cfg = store.load()
    cfg.platform("twitch").channel = "ma_chaine"
    cfg.platform("twitch").enabled = False
    assert store.save(cfg)

    reloaded = TwitchChatStore(store._path).load()
    assert reloaded.platform("twitch").channel == "ma_chaine"
    assert reloaded.platform("twitch").enabled is False


def test_load_returns_a_copy_not_the_cache(store):
    store.save(store.load())
    first = store.load()
    first.platform("twitch").channel = "pollution"
    assert store.load().platform("twitch").channel == ""


def test_corrupt_file_falls_back_to_defaults(store):
    store._path.write_text("{ pas du json", encoding="utf-8")
    cfg = store.load()
    assert set(cfg.platforms) == set(PLATFORMS)


# --- Jeton permanent ------------------------------------------------------ #

def test_token_is_created_once_and_survives_reload(store):
    """Le lien collé dans OBS doit rester valide après redémarrage : c'est
    exactement ce que vérifie la relecture par un second store."""
    token = store.ensure_token()
    assert token
    assert store.ensure_token() == token
    assert TwitchChatStore(store._path).ensure_token() == token


def test_regenerate_changes_the_token(store):
    old = store.ensure_token()
    new = store.regenerate_token()
    assert new and new != old
    assert store.load().overlay_token == new


def test_regenerate_keeps_platform_settings(store):
    cfg = store.load()
    cfg.platform("twitch").channel = "ma_chaine"
    store.save(cfg)
    store.regenerate_token()
    assert store.load().platform("twitch").channel == "ma_chaine"


def test_the_token_is_encrypted_on_disk(store):
    """Le jeton EST le seul contrôle d'accès de la page de chat : il n'a pas à
    rester lisible dans le JSON, comme les identifiants du .env."""
    jeton = store.ensure_token()
    brut = json.loads(store._path.read_text(encoding="utf-8"))["overlay_token"]
    if not secret_store.available():          # hors Windows : pas de DPAPI
        assert brut == jeton
        return
    assert brut != jeton and secret_store.is_encrypted(brut)
    assert store.load().overlay_token == jeton          # relecture transparente


def test_a_legacy_plaintext_token_is_encrypted_at_startup(store):
    """Un fichier écrit avant le chiffrement ne doit pas rester en clair
    jusqu'au prochain changement de réglage — donc peut-être jamais. Et le
    jeton lui-même ne change pas : la source OBS déjà collée survit."""
    store._path.write_text(json.dumps({"overlay_token": "jeton-en-clair",
                                       "platforms": {}}), encoding="utf-8")
    assert store.ensure_token() == "jeton-en-clair"
    brut = json.loads(store._path.read_text(encoding="utf-8"))["overlay_token"]
    if secret_store.available():
        assert secret_store.is_encrypted(brut)


# --- Parsing Twitch ------------------------------------------------------- #

def test_parse_irc_tags_unescapes_spaces():
    tags = parse_irc_tags(r"display-name=Jean\sDupont;color=#FF0000")
    assert tags["display-name"] == "Jean Dupont"
    assert tags["color"] == "#FF0000"


def test_parse_privmsg_extracts_author_text_color_badges():
    line = (r"@badges=moderator/1,subscriber/12;color=#1E90FF;display-name=Alice "
            ":alice!alice@alice.tmi.twitch.tv PRIVMSG #chaine :salut tout le monde")
    parsed = parse_privmsg(line)
    assert parsed is not None
    assert parsed["author"] == "Alice"
    assert parsed["text"] == "salut tout le monde"
    assert parsed["color"] == "#1E90FF"
    assert parsed["badges"] == ("moderator", "subscriber")


def test_parse_privmsg_falls_back_to_nick_without_tags():
    parsed = parse_privmsg(":bob!bob@bob.tmi.twitch.tv PRIVMSG #chaine :coucou")
    assert parsed is not None and parsed["author"] == "bob"


def test_parse_privmsg_keeps_colons_inside_the_message():
    """Le message est le RESTE de la ligne : découper au premier « : » le
    tronquerait à chaque smiley ou URL."""
    parsed = parse_privmsg(":a!a@a.tmi.twitch.tv PRIVMSG #c :voir https://x.tv :)")
    assert parsed is not None and parsed["text"] == "voir https://x.tv :)"


@pytest.mark.parametrize("line", [
    "PING :tmi.twitch.tv",
    ":tmi.twitch.tv 366 justinfan1 #chaine :End of /NAMES list",
    "@msg-id=x :tmi.twitch.tv NOTICE #chaine :Login authentication failed",
    "",
])
def test_parse_privmsg_ignores_protocol_noise(line):
    assert parse_privmsg(line) is None


# --- Boucle de lecture Twitch --------------------------------------------- #

class _FakeSocket:
    """Socket IRC scriptée : rejoue des morceaux, puis simule la fermeture.

    Les morceaux sont volontairement découpés au milieu des lignes : c'est le
    cas réel (TCP ne respecte aucune frontière de message) et c'est ce qui
    casse une implémentation naïve.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.sent: list[bytes] = []

    def recv(self, _size: int) -> bytes:
        if not self._chunks:
            return b""            # fermeture par le serveur
        return self._chunks.pop(0)

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


def _drain_twitch(chunks: list[bytes]):
    """Fait tourner la boucle de lecture jusqu'à la fermeture simulée."""
    messages: list[ChatMessage] = []
    statuses: list[tuple[str, str]] = []
    conn = TwitchConnector(PlatformConfig(channel="chaine"), messages.append,
                           lambda _p, s, d: statuses.append((s, d)))
    sock = _FakeSocket(chunks)
    with pytest.raises(ConnectionError):
        conn._read_loop(sock)     # la fermeture doit remonter pour reconnecter
    return conn, sock, messages, statuses


def test_read_loop_emits_normalized_messages():
    line = (r"@color=#1E90FF;display-name=Alice "
            ":alice!alice@alice.tmi.twitch.tv PRIVMSG #chaine :bonjour").encode()
    _conn, _sock, messages, _statuses = _drain_twitch([line + b"\r\n"])
    assert len(messages) == 1
    assert messages[0].platform == "twitch"
    assert messages[0].author == "Alice"
    assert messages[0].text == "bonjour"
    assert messages[0].color == "#1E90FF"
    assert messages[0].timestamp > 0


def test_read_loop_reassembles_lines_split_across_packets():
    """TCP coupe où il veut : un message à cheval sur deux paquets doit
    quand même arriver entier, et une seule fois."""
    _conn, _sock, messages, _statuses = _drain_twitch([
        b":a!a@a.tmi.twitch.tv PRIVMSG #chaine :debut",
        b" et fin\r\n:b!b@b.tmi.twitch.tv PRIVMSG #chaine :deux\r\n",
    ])
    assert [m.text for m in messages] == ["debut et fin", "deux"]


def test_read_loop_answers_ping_and_does_not_publish_it():
    _conn, sock, messages, _statuses = _drain_twitch([
        b"PING :tmi.twitch.tv\r\n:a!a@a.tmi.twitch.tv PRIVMSG #chaine :ok\r\n"])
    assert b"PONG :tmi.twitch.tv\r\n" in sock.sent
    assert [m.text for m in messages] == ["ok"]


def test_read_loop_reports_connected_on_end_of_names():
    _conn, _sock, _messages, statuses = _drain_twitch([
        b":tmi.twitch.tv 366 justinfan1 #chaine :End of /NAMES list\r\n"])
    assert (Status.CONNECTED, "chaine") in statuses


# --- Backoff -------------------------------------------------------------- #

class _DummyConnector(BaseConnector):
    platform = "dummy"

    def _session(self) -> None:
        return None


def test_backoff_grows_and_is_capped():
    conn = _DummyConnector(PlatformConfig(channel="x"), lambda m: None, lambda *a: None)
    delays = [conn._backoff_delay(n) for n in range(1, 12)]
    assert delays[0] < delays[3]                             # ça monte
    assert all(d <= conn.BACKOFF_MAX * 1.3 for d in delays)  # plafond + jitter
    assert all(d >= 1.0 for d in delays)


def test_backoff_is_jittered():
    """Sans bruit, quatre connecteurs coupés ensemble retenteraient à la
    même milliseconde à chaque cycle."""
    conn = _DummyConnector(PlatformConfig(channel="x"), lambda m: None, lambda *a: None)
    assert len({conn._backoff_delay(3) for _ in range(20)}) > 1


def test_unconfigured_connector_reports_needs_config_without_thread():
    conn = _DummyConnector(PlatformConfig(channel=""), lambda m: None, lambda *a: None)
    conn.start()
    assert conn.status == Status.NEEDS_CONFIG
    assert not conn.is_running


def test_a_clean_session_does_not_escalate_the_backoff():
    """Une session qui se termine SANS exception n'est pas un échec : le
    backoff doit repartir de zéro. Sinon l'attente grimpait à plus d'une
    minute pour une simple fin de session, et l'utilisateur croyait à une
    panne — c'est ce qui s'est produit le 2026-09-07."""
    delais: list[float] = []
    tours: list[int] = []
    done = threading.Event()

    class Propre(BaseConnector):
        platform = "twitch"

        def _session(self) -> None:
            tours.append(1)
            if len(tours) >= 4:
                done.set()
                self._stop.set()

        def _backoff_delay(self, attempt: int) -> float:
            delais.append(attempt)
            return 0.01

    conn = Propre(PlatformConfig(channel="chaine"), lambda m: None, lambda *a: None)
    conn.start()
    assert done.wait(timeout=5)
    conn.stop()

    # Chaque tour repart de la tentative 1 : aucune escalade.
    assert delais and set(delais) == {1}, delais


# --- Isolation ------------------------------------------------------------ #

def test_a_crashing_session_never_escapes_the_connector():
    """Un connecteur qui plante ne doit jamais faire tomber l'application :
    l'exception est absorbée et l'état passe en ERROR."""
    seen: list[tuple[str, str, str]] = []
    done = threading.Event()

    class Boom(BaseConnector):
        platform = "boom"

        def _session(self) -> None:
            raise RuntimeError("socket morte")

    def on_status(platform: str, status: str, detail: str) -> None:
        seen.append((platform, status, detail))
        if status == Status.ERROR:
            done.set()

    conn = Boom(PlatformConfig(channel="x"), lambda m: None, on_status)
    conn.start()
    assert done.wait(timeout=5), "l'état ERROR n'a jamais été signalé"
    conn.stop()
    assert ("boom", Status.ERROR, "socket morte") in seen


# --- Hub ------------------------------------------------------------------ #

def _msg(platform: str = "twitch", text: str = "coucou") -> ChatMessage:
    return ChatMessage(id="m1", platform=platform, author="Alice", text=text,
                       timestamp=time.time())


def _hub(store: TwitchChatStore) -> ChatHub:
    return ChatHub(store)


def test_hub_fans_out_to_every_subscriber(store):
    hub = _hub(store)
    a, b = hub.subscribe(), hub.subscribe()
    assert hub.publish(_msg()) == 2
    assert json.loads(a.get_nowait())["text"] == "coucou"
    assert json.loads(b.get_nowait())["text"] == "coucou"


def test_disabled_platform_never_reaches_the_network(store):
    """Le filtre est côté serveur : un chat masqué ne doit pas atteindre la
    page, même dans le DOM."""
    hub = _hub(store)
    sub = hub.subscribe()
    hub.set_enabled("twitch", False)
    assert hub.publish(_msg("twitch")) == 0
    assert hub.publish(_msg("youtube")) == 1
    assert json.loads(sub.get_nowait())["platform"] == "youtube"


def test_disabled_platform_is_also_absent_from_history(store):
    hub = _hub(store)
    hub.publish(_msg("twitch"))
    hub.publish(_msg("youtube"))
    hub.set_enabled("twitch", False)
    assert [m["platform"] for m in hub.history()] == ["youtube"]


def test_history_is_capped_and_keeps_the_newest(store):
    from twitch_chat import HISTORY_SIZE
    hub = _hub(store)
    for i in range(HISTORY_SIZE + 25):
        hub.publish(_msg(text=f"m{i}"))
    history = hub.history()
    assert len(history) == HISTORY_SIZE
    assert history[-1]["text"] == f"m{HISTORY_SIZE + 24}"


def test_saturated_subscriber_loses_the_oldest_not_the_newest(store):
    from twitch_chat import _SUB_QUEUE_SIZE
    hub = _hub(store)
    sub = hub.subscribe()
    for i in range(_SUB_QUEUE_SIZE + 30):
        hub.publish(_msg(text=f"m{i}"))
    drained = []
    while not sub.empty():
        drained.append(json.loads(sub.get_nowait()))
    assert drained[-1]["text"] == f"m{_SUB_QUEUE_SIZE + 29}"


def test_unsubscribe_stops_delivery(store):
    hub = _hub(store)
    sub = hub.subscribe()
    hub.unsubscribe(sub)
    assert hub.publish(_msg()) == 0
    assert hub.listener_count() == 0


def test_apply_config_without_a_channel_asks_for_configuration(store):
    """Sans nom de chaîne, le connecteur ne démarre pas et le dit."""
    hub = _hub(store)
    hub.apply_config(store.load())
    try:
        assert hub.status("twitch")[0] == Status.NEEDS_CONFIG
    finally:
        hub.stop_all()


def test_a_status_callback_that_raises_does_not_break_the_hub(store):
    def hostile(_platform: str, _status: str, _detail: str) -> None:
        raise RuntimeError("interface morte")

    hub = ChatHub(store, on_status_change=hostile)
    hub.apply_config(store.load())     # ne doit pas lever
    hub.stop_all()


# --- Routes HTTP ---------------------------------------------------------- #

class _StubHub:
    """Hub minimal : le serveur n'a besoin que de ces trois méthodes."""

    def __init__(self) -> None:
        self.queue: "queue.Queue[str]" = queue.Queue(maxsize=8)
        self.seed = [{"id": "h1", "platform": "twitch", "author": "Alice",
                      "text": "message archive", "color": "#9146FF",
                      "badges": [], "timestamp": 0.0}]

    def subscribe(self):
        return self.queue

    def unsubscribe(self, _sub) -> None:
        return None

    def history(self):
        return self.seed


# Catalogue minimal : le serveur résout ses libellés par clé, exactement comme
# il le fera avec i18n.t en production.
_TEXTS = {
    "TWITCH_CHAT_WAIT_TEXT": "En attente",
    "OAUTH_PAGE_OK_TITLE": "Compte connecte",
    "OAUTH_PAGE_OK_BODY": "Tu peux fermer cet onglet.",
    "OAUTH_PAGE_FAIL_TITLE": "Connexion refusee",
    "OAUTH_PAGE_FAIL_BODY": "Rien n'a ete enregistre.",
}


@pytest.fixture
def chat_server():
    hub = _StubHub()
    srv = OverlayServer(lambda _rid: None, port=0, chat_hub=hub,
                        chat_token_getter=lambda: "jeton-secret",
                        text_getter=_TEXTS.get)
    assert srv.start()
    yield srv, hub
    srv.stop()


def _get(srv: OverlayServer, path: str):
    with urllib.request.urlopen(srv.base_url() + path, timeout=5) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read()


def test_chat_url_carries_the_persisted_token(chat_server):
    srv, _hub = chat_server
    assert srv.chat_url() == f"{srv.base_url()}/chat/jeton-secret"


def test_chat_page_is_served_for_the_right_token(chat_server):
    srv, _hub = chat_server
    status, ctype, body = _get(srv, "/chat/jeton-secret")
    assert status == 200 and "text/html" in ctype
    assert b'"jeton-secret"' in body
    assert b"En attente" in body


def test_chat_page_declares_a_content_security_policy(chat_server):
    """La page ne charge rien d'extérieur : le CSP l'écrit, pour qu'une
    injection future ne puisse ni tirer un script distant ni exfiltrer le
    chat vers un tiers."""
    srv, _hub = chat_server
    with urllib.request.urlopen(srv.base_url() + "/chat/jeton-secret", timeout=5) as reponse:
        csp = reponse.headers.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "img-src 'self'" in csp


def test_chat_page_template_has_no_unsubstituted_token(chat_server):
    srv, _hub = chat_server
    _, _, body = _get(srv, "/chat/jeton-secret")
    for marker in (b"__CHAT_TOKEN__", b"__WAIT_TEXT__", b"__MAX_VISIBLE__"):
        assert marker not in body


def test_chat_page_renders_messages_as_text_not_html(chat_server):
    """Un message de chat est du texte hostile : la page doit passer par
    textContent, jamais par innerHTML."""
    srv, _hub = chat_server
    _, _, body = _get(srv, "/chat/jeton-secret")
    assert b"textContent" in body
    assert b"innerHTML" not in body


def test_wrong_token_is_refused(chat_server):
    srv, _hub = chat_server
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(srv, "/chat/mauvais-jeton")
    assert excinfo.value.code == 404


def test_chat_routes_are_absent_without_a_hub():
    """Un serveur monté sans multi-chat ne doit exposer aucune de ces routes."""
    srv = OverlayServer(lambda _rid: None, port=0)
    assert srv.start()
    try:
        assert srv.chat_url() == ""
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _get(srv, "/chat/nimporte-quoi")
        assert excinfo.value.code == 404
    finally:
        srv.stop()


def test_events_replay_history_then_stream_live(chat_server):
    """Une source rouverte doit retrouver le fil, pas un écran vide."""
    srv, hub = chat_server
    received: list[str] = []
    ready = threading.Event()

    def reader() -> None:
        with urllib.request.urlopen(srv.base_url() + "/chatevents/jeton-secret",
                                    timeout=8) as stream:
            ready.set()
            deadline = time.time() + 6
            while time.time() < deadline and len(received) < 2:
                line = stream.readline().decode("utf-8", "replace")
                if line.startswith("data: "):
                    received.append(line[6:].strip())

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)
    time.sleep(0.4)                      # laisse partir la salve d'historique
    hub.queue.put(json.dumps({"id": "m2", "platform": "youtube", "author": "Bob",
                              "text": "en direct", "color": "#FF0033",
                              "badges": [], "timestamp": 1.0}))
    thread.join(timeout=8)

    assert len(received) >= 2
    assert json.loads(received[0])["text"] == "message archive"
    assert json.loads(received[1])["text"] == "en direct"


def test_trigger_routes_still_work_alongside_chat(chat_server):
    """Non-régression : l'ajout du multi-chat ne doit pas manger les routes
    des déclencheurs ni /health."""
    srv, _hub = chat_server
    status, _, body = _get(srv, "/health")
    assert status == 200 and body == b"ok"
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(srv, "/overlay/regle-inconnue")
    assert excinfo.value.code == 404
