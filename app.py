#!/usr/bin/env python3
"""Claude Agent – eigenständiger Flask-Service (Phase 1: Dropbox-Zugriff)."""
import uuid as _uuid
from pathlib import Path

import anthropic
import dropbox
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"))

_SECRETS_FILE = "/etc/pka/secrets.env"
_ALLOWED_DROPBOX_PREFIX = "/Apps/Claude"
_ALLOWED_SERVER_ROOTS = (
    "/opt/rename-webhook",
    "/opt/kargl-invoice",
    "/opt/project-insight",
    "/opt/autoquartett",
    "/opt/claude-agent",
    "/opt/traktoren",
)
_MAX_FILE_BYTES = 100_000
_MODEL = "claude-sonnet-4-6"

# In-Memory – geht bei Service-Neustart verloren (bekanntes Pitfall)
_pending: dict = {}


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
Du bist Josefs persönlicher Programm-Assistent mit direktem Zugriff auf seine Projektdateien.

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
- Vor write_file immer read_file aufrufen\
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
    """Agentic Loop. Pausiert bei write_file für Bestätigung."""
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
            return text, None, messages

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

    return "Maximale Runden erreicht.", None, messages


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
@app.route("/index.html")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:filename>")
def static_file(filename):
    return send_from_directory(app.static_folder, filename)


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    user_text = (data.get("message") or "").strip()
    messages = list(data.get("messages") or [])
    if not user_text:
        return jsonify({"error": "Leere Nachricht"}), 400
    messages.append({"role": "user", "content": user_text})
    secrets = _load_secrets()
    reply, pending_write, messages_out = _run_loop(messages, secrets)
    return jsonify({"reply": reply, "pending_write": pending_write, "messages": messages_out})


@app.route("/api/confirm-write", methods=["POST"])
def confirm_write():
    data = request.get_json(silent=True) or {}
    write_id = data.get("write_id", "")
    confirmed = bool(data.get("confirmed", False))
    pending = _pending.pop(write_id, None)
    if not pending:
        return jsonify({"error": "Write-ID unbekannt oder abgelaufen (Service-Neustart?)"}), 404
    secrets = _load_secrets()
    if confirmed:
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
    reply, new_pending, messages_out = _run_loop(messages, secrets)
    return jsonify({"reply": reply, "pending_write": new_pending, "messages": messages_out})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082, debug=False)
