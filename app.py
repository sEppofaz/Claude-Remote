#!/usr/bin/env python3
"""Claude Agent – Flask-Service (Phase 1: Dropbox, Phase 3: Hetzner Shell)."""
import functools
import hmac as _hmac
import re
import subprocess
import uuid as _uuid
from datetime import timedelta
from pathlib import Path

import anthropic
import dropbox
from flask import Flask, jsonify, redirect, request, send_from_directory, session

app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"))

BASE_PATH = "/claude-remote"
_SECRETS_FILE = "/etc/pka/secrets.env"
_SESSION_KEY_FILE = Path(__file__).parent / ".session_key"
_HTPASSWD_FILE = "/etc/nginx/claude-remote.htpasswd"
_ALLOWED_DROPBOX_PREFIX = "/Apps/Claude"
_ALLOWED_SERVER_ROOTS = (
    "/opt/rename-webhook",
    "/opt/kargl-invoice",
    "/opt/project-insight",
    "/opt/autoquartett",
    "/opt/claude-remote",
    "/opt/traktoren",
)

# Phase 3 – Shell-Whitelist
_VENVS = {
    "rename-webhook": {"pip": "/opt/rename-webhook/bin/pip",  "sudo": True},
    "claude-remote":  {"pip": "/opt/claude-remote/bin/pip",   "sudo": False},
    "kargl-invoice":  {"pip": "/opt/kargl-invoice/bin/pip",   "sudo": True},
    "life-doku":      {"pip": "/opt/life-doku/venv/bin/pip",  "sudo": True},
    "rechnungen":     {"pip": "/opt/rechnungen/venv/bin/pip", "sudo": True},
}
_SERVICES = {"rename-webhook", "claude-remote", "kargl-invoice", "life-doku", "rechnungen"}
_GIT_PROJECTS = {
    "rename-webhook": "/opt/rename-webhook",
    "claude-remote":  "/opt/claude-remote",
    "kargl-invoice":  "/opt/kargl-invoice",
    "life-doku":      "/opt/life-doku",
    "rechnungen":     "/opt/rechnungen",
    "project-insight": "/opt/project-insight",
}
_MAX_FILE_BYTES = 100_000
_MODEL = "claude-sonnet-4-6"

# In-Memory – geht bei Service-Neustart verloren (bekanntes Pitfall)
_pending: dict = {}


def _get_or_create_session_key() -> str:
    if _SESSION_KEY_FILE.exists():
        return _SESSION_KEY_FILE.read_text().strip()
    import secrets as _sec
    key = _sec.token_hex(32)
    _SESSION_KEY_FILE.write_text(key)
    _SESSION_KEY_FILE.chmod(0o600)
    return key


app.secret_key = _get_or_create_session_key()
app.permanent_session_lifetime = timedelta(minutes=15)


@app.before_request
def _refresh_session():
    if session.get("authenticated"):
        session.modified = True


def _check_password(password: str) -> bool:
    try:
        from passlib.apache import HtpasswdFile
        ht = HtpasswdFile(_HTPASSWD_FILE)
        users = list(ht.users())
        if not users:
            return False
        return bool(ht.check_password(users[0], password))
    except Exception:
        return False


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(BASE_PATH + "/login")
        return f(*args, **kwargs)
    return decorated


def _load_secrets() -> dict:
    out: dict = {}
    with open(_SECRETS_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


SYSTEM_PROMPT = """\
Du bist Josefs persönlicher Programm-Assistent mit direktem Zugriff auf seine Projektdateien und den Hetzner-Server.

Josefs Projekte auf Dropbox (Präfix dropbox:):
- PKA / Logbuch / Todos:       dropbox:/Apps/Claude/PKA/
- Vereinskalender:             dropbox:/Apps/Claude/Vereinskalender/
- Vokabeltrainer:              dropbox:/Apps/Claude/Vokabeltrainer/
- Stundensatzkalkulation:      dropbox:/Apps/Claude/Stundensatzkalkulation/
- Life-Doku:                   dropbox:/Apps/Claude/Life-Doku/
- Traktor Karten:              dropbox:/Apps/Claude/Traktorquartett/
- Auto Quartett:               dropbox:/Apps/Claude/AutoQuartett/
- Claude Agent:                dropbox:/Apps/Claude/Claude-Agent/
- Todo-App:                    dropbox:/Apps/Claude/ToDo-App/

Josefs GitHub-Repos (github_api Tool):
- sEppofaz/Vereinskalender, sEppofaz/Claude-Remote, sEppofaz/Vokabeltrainer
- sEppofaz/Messwerte-sEpp, sEppofaz/PKA-Todos, sEppofaz/Project-Insight-App
- sEppofaz/Rauchmelder, sEppofaz/Traktoren (weitere via list_repos ermitteln)

Josefs Projekte auf Hetzner-Server (Präfix server:):
- Vereinskalender / Claude Remote: server:/opt/rename-webhook/
- Kargl Rechnungen:               server:/opt/kargl-invoice/
- Project-Insight-App:            server:/opt/project-insight/
- Auto Quartett:                  server:/opt/autoquartett/
- Claude Agent:                   server:/opt/claude-agent/
- Traktor Karten:                 server:/opt/traktoren/

Vorgehen bei Änderungen:
1. Datei zuerst lesen (read_file)
2. Änderung kurz beschreiben
3. write_file aufrufen – Josef bestätigt dann im Browser

Regeln:
- Immer auf Deutsch antworten, kurz und präzise
- Bei unklaren Aufträgen nachfragen
- Vor write_file immer read_file aufrufen
- run_shell nur für klar beschriebene Aktionen aufrufen
- Nach pip_upgrade empfehlen ob service_restart nötig ist
- Bei „Kernel updaten" oder „Kernel-Update": apt_upgrade mit target='kernel' aufrufen\
"""

TOOLS = [
    {
        "name": "list_files",
        "description": (
            "Listet Dateien und Unterordner in einem erlaubten Pfad auf. "
            "Präfix 'dropbox:' für Dropbox (z.B. dropbox:/Apps/Claude/PKA/). "
            "Präfix 'server:' für Hetzner-Server (z.B. server:/opt/claude-agent/)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Liest den vollständigen Inhalt einer Textdatei (max. 100 KB). "
            "Präfix 'dropbox:' oder 'server:'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Schreibt neuen Inhalt in eine Datei (überschreibt komplett). "
            "Erfordert explizite Bestätigung durch Josef. "
            "Präfix 'dropbox:' oder 'server:'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string", "description": "Vollständiger neuer Dateiinhalt"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "github_api",
        "description": (
            "Zugriff auf Josefs GitHub-Repos. "
            "Aktionen: "
            "'list_repos' – alle Repos auflisten (sofort); "
            "'read_file' – Datei aus Repo lesen (sofort); "
            "'list_commits' – letzte Commits (sofort); "
            "'write_file' – Datei erstellen/überschreiben (erfordert Bestätigung); "
            "'create_repo' – neues Repo anlegen (erfordert Bestätigung)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list_repos", "read_file", "list_commits", "write_file", "create_repo"],
                },
                "repo": {"type": "string", "description": "Repo-Name, z.B. 'sEppofaz/Vereinskalender'"},
                "path": {"type": "string", "description": "Dateipfad im Repo, z.B. 'src/app.py'"},
                "content": {"type": "string", "description": "Vollständiger Dateiinhalt für write_file"},
                "message": {"type": "string", "description": "Commit-Message für write_file"},
                "name": {"type": "string", "description": "Repo-Name für create_repo"},
                "private": {"type": "boolean", "description": "Privates Repo? (default: true)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "run_shell",
        "description": (
            "Führt einen whitelisted Server-Befehl aus. "
            "Aktionen: "
            "'pip_upgrade' – Paket upgraden (erfordert Bestätigung); "
            "'service_restart' – Service neustarten (erfordert Bestätigung); "
            "'service_status' – Status lesen (sofort, keine Bestätigung); "
            "'git_pull' – git pull für ein Projekt (erfordert Bestätigung); "
            "'apt_upgrade' – Kernel-Update einspielen via apt dist-upgrade (erfordert Bestätigung, target='kernel'). "
            "Apps/Services: rename-webhook, claude-remote, kargl-invoice, life-doku, rechnungen. "
            "Git-Projekte zusätzlich: project-insight."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pip_upgrade", "service_restart", "service_status", "git_pull", "apt_upgrade"],
                },
                "target": {"type": "string", "description": "App- oder Service-Name; für apt_upgrade: 'kernel'"},
                "package": {"type": "string", "description": "Paketname für pip_upgrade"},
            },
            "required": ["action", "target"],
        },
    },
]


def _validate_path(raw: str) -> tuple[str, bool]:
    """Parst Pfad, prüft Whitelist. Gibt (pfad, is_dropbox) zurück. Wirft ValueError."""
    if raw.startswith("dropbox:"):
        p = "/" + raw[len("dropbox:"):].strip("/")
        if not (p == _ALLOWED_DROPBOX_PREFIX or p.startswith(_ALLOWED_DROPBOX_PREFIX + "/")):
            raise ValueError(f"Dropbox-Pfad nicht erlaubt: {p}")
        return p, True
    if raw.startswith("server:"):
        p = str(Path(raw[len("server:"):]).resolve())
        if not any(p == r or p.startswith(r + "/") for r in _ALLOWED_SERVER_ROOTS):
            raise ValueError(f"Server-Pfad nicht erlaubt: {p}")
        return p, False
    raise ValueError("Pfad muss mit 'dropbox:' oder 'server:' beginnen")


def _dbx(secrets: dict) -> dropbox.Dropbox:
    return dropbox.Dropbox(
        oauth2_refresh_token=secrets["DROPBOX_REFRESH_TOKEN"],
        app_key=secrets["DROPBOX_APP_KEY"],
        app_secret=secrets["DROPBOX_APP_SECRET"],
    )


def _tool_list_files(path_raw: str, secrets: dict) -> str:
    try:
        path, is_dropbox = _validate_path(path_raw)
    except ValueError as e:
        return f"Fehler: {e}"
    if is_dropbox:
        try:
            res = _dbx(secrets).files_list_folder(path)
            lines = [
                f"{'📁' if isinstance(e, dropbox.files.FolderMetadata) else '📄'} {e.name}"
                for e in sorted(res.entries, key=lambda x: x.name)
            ]
            return "\n".join(lines) or "(leer)"
        except Exception as e:
            return f"Dropbox-Fehler: {e}"
    p = Path(path)
    if not p.exists():
        return f"Existiert nicht: {path}"
    lines = [f"{'📁' if i.is_dir() else '📄'} {i.name}" for i in sorted(p.iterdir())]
    return "\n".join(lines) or "(leer)"


def _tool_read_file(path_raw: str, secrets: dict) -> str:
    try:
        path, is_dropbox = _validate_path(path_raw)
    except ValueError as e:
        return f"Fehler: {e}"
    if is_dropbox:
        try:
            _, resp = _dbx(secrets).files_download(path)
            data = resp.content
            if len(data) > _MAX_FILE_BYTES:
                return f"Datei zu groß ({len(data)} Bytes, max {_MAX_FILE_BYTES})"
            return data.decode("utf-8", errors="replace")
        except Exception as e:
            return f"Dropbox-Fehler: {e}"
    p = Path(path)
    if not p.is_file():
        return f"Datei nicht gefunden: {path}"
    data = p.read_bytes()
    if len(data) > _MAX_FILE_BYTES:
        return f"Datei zu groß ({len(data)} Bytes, max {_MAX_FILE_BYTES})"
    return data.decode("utf-8", errors="replace")


def _do_write(path: str, content: str, is_dropbox: bool, secrets: dict) -> str:
    if is_dropbox:
        try:
            _dbx(secrets).files_upload(
                content.encode("utf-8"),
                path,
                mode=dropbox.files.WriteMode.overwrite,
            )
            return "OK"
        except Exception as e:
            return f"Fehler: {e}"
    try:
        Path(path).write_text(content, encoding="utf-8")
        return "OK"
    except Exception as e:
        return f"Fehler: {e}"


def _build_shell_cmd(action: str, target: str, package: str | None) -> tuple[list[str], str]:
    """Baut whitelisted Befehl. Wirft ValueError bei Verstoß."""
    if action == "pip_upgrade":
        if target not in _VENVS:
            raise ValueError(f"App '{target}' nicht in Whitelist")
        if not package or not re.match(r"^[a-zA-Z0-9_\-\.]+$", package):
            raise ValueError("Ungültiger Paketname")
        info = _VENVS[target]
        cmd = (["sudo"] if info["sudo"] else []) + [info["pip"], "install", "--upgrade", package]
        return cmd, f"pip install --upgrade {package}  [{target}]"

    if action == "service_restart":
        if target not in _SERVICES:
            raise ValueError(f"Service '{target}' nicht in Whitelist")
        return ["sudo", "systemctl", "restart", target], f"systemctl restart {target}"

    if action == "service_status":
        if target not in _SERVICES:
            raise ValueError(f"Service '{target}' nicht in Whitelist")
        return (
            ["systemctl", "status", "--no-pager", "--lines=20", target],
            f"systemctl status {target}",
        )

    if action == "git_pull":
        if target not in _GIT_PROJECTS:
            raise ValueError(f"Projekt '{target}' nicht in Whitelist")
        path = _GIT_PROJECTS[target]
        return (
            ["sudo", "git", "-c", f"safe.directory={path}", "-C", path, "pull"],
            f"git -C {path} pull",
        )

    if action == "apt_upgrade":
        if target != "kernel":
            raise ValueError("apt_upgrade: target muss 'kernel' sein")
        return (
            ["sudo", "apt-get", "dist-upgrade", "-y",
             "-o", "Dpkg::Options::=--force-confdef",
             "-o", "Dpkg::Options::=--force-confold"],
            "apt-get dist-upgrade (Kernel-Update)",
        )

    raise ValueError(f"Unbekannte Aktion: {action}")


def _run_shell_cmd(cmd: list[str], timeout: int = 120) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (result.stdout + result.stderr).strip()
        return out[:3000] if out else "(kein Output)"
    except subprocess.TimeoutExpired:
        return "Timeout nach 120s"
    except Exception as e:
        return f"Fehler: {e}"


def _gh(secrets):
    from github import Github
    return Github(secrets["GITHUB_TOKEN"])


def _tool_github(action: str, inp: dict, secrets: dict) -> str:
    try:
        g = _gh(secrets)
        if action == "list_repos":
            repos = sorted(g.get_user().get_repos(), key=lambda r: r.updated_at, reverse=True)
            return "\n".join(
                f"{'🔒' if r.private else '🌐'} {r.full_name} – {r.description or '–'}"
                for r in repos
            ) or "(keine Repos)"
        if action == "read_file":
            repo, path = inp.get("repo", ""), inp.get("path", "")
            fc = g.get_repo(repo).get_contents(path)
            if isinstance(fc, list):
                return "Verzeichnis: " + ", ".join(f.name for f in fc)
            data = fc.decoded_content
            if len(data) > _MAX_FILE_BYTES:
                return f"Datei zu groß ({len(data)} Bytes)"
            return data.decode("utf-8", errors="replace")
        if action == "list_commits":
            repo = inp.get("repo", "")
            commits = list(g.get_repo(repo).get_commits()[:15])
            return "\n".join(
                f"{c.sha[:7]} {c.commit.message.splitlines()[0][:60]} ({c.commit.author.date.strftime('%Y-%m-%d')})"
                for c in commits
            ) or "(keine Commits)"
        return f"Unbekannte Aktion: {action}"
    except Exception as e:
        return f"GitHub-Fehler: {e}"


def _do_github_write(repo: str, path: str, content: str, message: str, secrets: dict) -> str:
    try:
        r = _gh(secrets).get_repo(repo)
        try:
            existing = r.get_contents(path)
            r.update_file(path, message, content.encode("utf-8"), existing.sha)
        except Exception:
            r.create_file(path, message, content.encode("utf-8"))
        return "OK"
    except Exception as e:
        return f"Fehler: {e}"


def _do_github_create_repo(name: str, private: bool, secrets: dict) -> str:
    try:
        repo = _gh(secrets).get_user().create_repo(name, private=private)
        return repo.html_url
    except Exception as e:
        return f"Fehler: {e}"


def _blk(b) -> dict:
    return b.model_dump() if hasattr(b, "model_dump") else b


def _serialize(messages: list) -> list:
    out = []
    for m in messages:
        role = m["role"] if isinstance(m, dict) else m.role
        c = m["content"] if isinstance(m, dict) else m.content
        if isinstance(c, list):
            out.append({"role": role, "content": [_blk(b) for b in c]})
        else:
            out.append({"role": role, "content": c})
    return out


def _run_loop(messages: list, secrets: dict, max_rounds: int = 12):
    """Agentic Loop. Pausiert bei write_file/run_shell für Bestätigung.
    Rückgabe: (text, pending_write, pending_shell, messages)"""
    client = anthropic.Anthropic(api_key=secrets["CLAUDE_API_KEY"])

    for _ in range(max_rounds):
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )
        messages = _serialize(messages) + [
            {"role": "assistant", "content": [_blk(b) for b in resp.content]}
        ]

        if resp.stop_reason == "end_turn":
            text = " ".join(b.text for b in resp.content if hasattr(b, "text")).strip()
            return text, None, None, messages

        if resp.stop_reason == "tool_use":
            tool_results = []
            for block in resp.content:
                if not (hasattr(block, "type") and block.type == "tool_use"):
                    continue

                if block.name == "write_file":
                    path_raw = block.input["path"]
                    new_content = block.input["content"]
                    try:
                        path, is_dropbox = _validate_path(path_raw)
                    except ValueError as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Pfad-Fehler: {e}",
                        })
                        continue
                    old_content = _tool_read_file(path_raw, secrets)
                    write_id = str(_uuid.uuid4())
                    _pending[write_id] = {
                        "type": "write",
                        "path": path,
                        "path_raw": path_raw,
                        "is_dropbox": is_dropbox,
                        "old_content": old_content,
                        "new_content": new_content,
                        "tool_use_id": block.id,
                        "messages": messages,
                    }
                    prefix = " ".join(
                        b.text for b in resp.content if hasattr(b, "text")
                    ).strip()
                    return (
                        prefix or "Ich möchte eine Datei ändern – bitte bestätigen:",
                        {
                            "write_id": write_id,
                            "path": path,
                            "old_content": old_content,
                            "new_content": new_content,
                        },
                        None,
                        messages,
                    )

                if block.name == "github_api":
                    gh_action = block.input.get("action", "")

                    if gh_action in ("list_repos", "read_file", "list_commits"):
                        result = _tool_github(gh_action, block.input, secrets)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                        continue

                    if gh_action == "write_file":
                        repo = block.input.get("repo", "")
                        path = block.input.get("path", "")
                        new_content = block.input.get("content", "")
                        message = block.input.get("message", "Claude Remote: Datei aktualisiert")
                        old_content = _tool_github("read_file", {"repo": repo, "path": path}, secrets)
                        write_id = str(_uuid.uuid4())
                        _pending[write_id] = {
                            "type": "github_write",
                            "repo": repo,
                            "path": path,
                            "old_content": old_content,
                            "new_content": new_content,
                            "message": message,
                            "tool_use_id": block.id,
                            "messages": messages,
                        }
                        prefix = " ".join(b.text for b in resp.content if hasattr(b, "text")).strip()
                        return (
                            prefix or "GitHub-Datei ändern – bitte bestätigen:",
                            {
                                "write_id": write_id,
                                "path": f"github:{repo}/{path}",
                                "old_content": old_content,
                                "new_content": new_content,
                            },
                            None,
                            messages,
                        )

                    if gh_action == "create_repo":
                        name = block.input.get("name", "")
                        private = block.input.get("private", True)
                        shell_id = str(_uuid.uuid4())
                        display = f"GitHub Repo erstellen: {name} ({'privat' if private else 'öffentlich'})"
                        _pending[shell_id] = {
                            "type": "github_create_repo",
                            "name": name,
                            "private": private,
                            "tool_use_id": block.id,
                            "messages": messages,
                        }
                        prefix = " ".join(b.text for b in resp.content if hasattr(b, "text")).strip()
                        return (
                            prefix or "GitHub-Repo erstellen – bitte bestätigen:",
                            None,
                            {"shell_id": shell_id, "cmd_display": display},
                            messages,
                        )

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Unbekannte github_api-Aktion: {gh_action}",
                    })
                    continue

                if block.name == "run_shell":
                    action  = block.input.get("action", "")
                    target  = block.input.get("target", "")
                    package = block.input.get("package")
                    try:
                        cmd, display = _build_shell_cmd(action, target, package)
                    except ValueError as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Fehler: {e}",
                        })
                        continue

                    # Lesend: sofort ausführen
                    if action == "service_status":
                        result = _run_shell_cmd(cmd)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                        continue

                    # Schreibend: Gate
                    shell_id = str(_uuid.uuid4())
                    _pending[shell_id] = {
                        "type": "shell",
                        "action": action,
                        "cmd": cmd,
                        "display": display,
                        "tool_use_id": block.id,
                        "messages": messages,
                    }
                    prefix = " ".join(
                        b.text for b in resp.content if hasattr(b, "text")
                    ).strip()
                    return (
                        prefix or "Befehl ausführen – bitte bestätigen:",
                        None,
                        {"shell_id": shell_id, "cmd_display": display},
                        messages,
                    )

                if block.name == "list_files":
                    result = _tool_list_files(block.input["path"], secrets)
                elif block.name == "read_file":
                    result = _tool_read_file(block.input["path"], secrets)
                else:
                    result = f"Unbekanntes Tool: {block.name}"

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            if tool_results:
                messages = messages + [{"role": "user", "content": tool_results}]

    return "Maximale Runden erreicht.", None, None, messages


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(BASE_PATH + "/")
    if request.method == "POST":
        if _check_password(request.form.get("password", "")):
            session.permanent = True
            session["authenticated"] = True
            return redirect(BASE_PATH + "/")
        return redirect(BASE_PATH + "/login?error=1")
    return send_from_directory(app.static_folder, "login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(BASE_PATH + "/login")


@app.route("/")
@app.route("/index.html")
@login_required
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:filename>")
def static_file(filename):
    return send_from_directory(app.static_folder, filename)


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json(silent=True) or {}
    user_text = (data.get("message") or "").strip()
    messages = list(data.get("messages") or [])
    if not user_text:
        return jsonify({"error": "Leere Nachricht"}), 400
    messages.append({"role": "user", "content": user_text})
    secrets = _load_secrets()
    reply, pending_write, pending_shell, messages_out = _run_loop(messages, secrets)
    return jsonify({
        "reply": reply,
        "pending_write": pending_write,
        "pending_shell": pending_shell,
        "messages": messages_out,
    })


@app.route("/api/confirm-write", methods=["POST"])
@login_required
def confirm_write():
    data = request.get_json(silent=True) or {}
    write_id = data.get("write_id", "")
    confirmed = bool(data.get("confirmed", False))
    pending = _pending.pop(write_id, None)
    if not pending:
        return jsonify({"error": "Write-ID unbekannt oder abgelaufen (Service-Neustart?)"}), 404
    secrets = _load_secrets()
    if pending.get("type") == "github_write":
        if confirmed:
            result = _do_github_write(
                pending["repo"], pending["path"],
                pending["new_content"], pending["message"], secrets,
            )
            msg = (
                f"GitHub-Datei geschrieben: {pending['repo']}/{pending['path']}"
                if result == "OK"
                else f"Fehler: {result}"
            )
        else:
            msg = "GitHub-Schreiboperation abgelehnt."
    elif confirmed:
        result = _do_write(
            pending["path"], pending["new_content"], pending["is_dropbox"], secrets
        )
        msg = (
            f"Datei erfolgreich geschrieben: {pending['path']}"
            if result == "OK"
            else f"Fehler beim Schreiben: {result}"
        )
    else:
        msg = "Schreiboperation vom User abgelehnt."
    messages = pending["messages"] + [{
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": pending["tool_use_id"], "content": msg}],
    }]
    reply, new_write, new_shell, messages_out = _run_loop(messages, secrets)
    return jsonify({
        "reply": reply,
        "pending_write": new_write,
        "pending_shell": new_shell,
        "messages": messages_out,
    })


@app.route("/api/confirm-shell", methods=["POST"])
@login_required
def confirm_shell():
    data = request.get_json(silent=True) or {}
    shell_id = data.get("shell_id", "")
    confirmed = bool(data.get("confirmed", False))
    pending = _pending.pop(shell_id, None)
    if not pending:
        return jsonify({"error": "Shell-ID unbekannt oder abgelaufen (Service-Neustart?)"}), 404
    secrets = _load_secrets()
    if pending.get("type") == "github_create_repo":
        if confirmed:
            url = _do_github_create_repo(pending["name"], pending["private"], secrets)
            msg = (
                f"Repo erstellt: {url}"
                if url.startswith("https://")
                else url
            )
        else:
            msg = "Repo-Erstellung abgelehnt."
    elif confirmed:
        timeout = 300 if pending.get("action") == "apt_upgrade" else 120
        output = _run_shell_cmd(pending["cmd"], timeout=timeout)
        msg = f"Befehl ausgeführt:\n{output}"
    else:
        msg = "Befehl vom User abgelehnt."
    messages = pending["messages"] + [{
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": pending["tool_use_id"], "content": msg}],
    }]
    reply, new_write, new_shell, messages_out = _run_loop(messages, secrets)
    return jsonify({
        "reply": reply,
        "pending_write": new_write,
        "pending_shell": new_shell,
        "messages": messages_out,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082, debug=False)
