# Claude Remote – Josef Fischer

## Überblick
Eigenständiger Python-Flask-KI-Agent mit direktem Dateizugriff auf Dropbox und Hetzner-Server. Unabhängig von Vereinskalender. Chat-Interface mit Write-Gate (Bestätigung vor jedem Schreibzugriff). Nachfolger des alten Claude Remote Blueprints in Vereinskalender.

## Phasen

| Phase | Inhalt | Status |
|-------|--------|--------|
| 1 | Dropbox read/write/list für `/Apps/Claude/**` | ✅ umgesetzt |
| 2 | GitHub API: Repos erstellen, Dateien pushen, Commits | ✅ umgesetzt |
| 3 | Hetzner Shell: `pip upgrade`, `git pull`, `systemctl restart` per Tool | ✅ umgesetzt |

## Lokaler Pfad
`~/Dropbox/Apps/Claude/Claude-Remote/`

## GitHub
`https://github.com/sEppofaz/Claude-Remote`

## Deployment auf Hetzner

- **Port:** 8082 (intern)
- **Service-Pfad:** `/opt/claude-remote/`
- **Systemd:** `claude-remote.service`
- **Nginx-Pfad:** `/claude-remote/` → `http://127.0.0.1:8082/`
- **Nginx-Config:** `/etc/nginx/sites-enabled/rename-webhook`
- **Auth:** Flask-Session-Login (`/login`), Session-Cookie 30 Tage; Passwort via passlib gegen `/etc/nginx/claude-remote.htpasswd`; Session-Key auto-generiert in `/opt/claude-remote/.session_key` (chmod 600)
- **Logout:** `https://umbenennen.duckdns.org/claude-remote/logout`
- **Secrets:** `/etc/pka/secrets.env` (gelesen via `_load_secrets()`)

### Systemd Service (`/etc/systemd/system/claude-remote.service`)
```ini
[Unit]
Description=Claude Remote
After=network.target

[Service]
Environment=HOME=/tmp
User=www-data
WorkingDirectory=/opt/claude-remote
ExecStart=/opt/claude-remote/bin/gunicorn -w 1 -b 127.0.0.1:8082 --worker-tmp-dir /tmp app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Aktualisierung (Standard-Flow)
```bash
# 1. Lokal ändern + pushen:
git -C ~/Dropbox/Apps/Claude/Claude-Remote push

# 2. Auf Server deployen:
ssh root@89.167.104.145 "git -C /opt/claude-remote pull && systemctl restart claude-remote"
```

### Icons neu generieren (lokal)
```bash
cd ~/Dropbox/Apps/Claude/Claude-Remote
python3 generate_icons.py
# dann: git add static/*.png && commit + push + server pull
```

## Dateistruktur
```
Claude-Remote/
├── app.py              # Flask-App (Phase 1: Dropbox-Tools)
├── requirements.txt
├── generate_icons.py   # Icon-Generator (stdlib, kein Pillow) – CR-Monogramm
├── CLAUDE.md
└── static/
    ├── index.html      # Chat-UI (PWA)
    ├── login.html      # Login-Formular (Flask-Session, autocomplete/Face ID)
    ├── manifest.json
    ├── sw.js
    ├── icon-192.png    # generiert via generate_icons.py
    ├── icon-512.png
    └── apple-touch-icon.png
```

## Erlaubte Pfade (Phase 1)
| Präfix | Pfad |
|--------|------|
| `dropbox:` | `/Apps/Claude/**` (alle Claude-Projekte) |
| `server:` | `/opt/rename-webhook/` |
| `server:` | `/opt/kargl-invoice/` |
| `server:` | `/opt/project-insight/` |
| `server:` | `/opt/autoquartett/` |
| `server:` | `/opt/claude-remote/` |
| `server:` | `/opt/traktoren/` |

## Pitfalls
- **⚠️ nginx location-Block** `/claude-remote/` in `/etc/nginx/sites-enabled/rename-webhook` kann bei Aufräumaktionen versehentlich gelöscht werden → App gibt 404, Service läuft weiter. Fix: Block wiederherstellen (proxy_pass 8082, ohne auth_basic) + `nginx -t && systemctl reload nginx`
- **`.session_key`** in `/opt/claude-remote/.session_key` – wird beim ersten Start auto-generiert (chmod 600). Wenn gelöscht → alle Sessions ungültig, neue Logins nötig. Nicht in git.
- **Auth ist jetzt Flask-seitig** – nginx hat keine BasicAuth mehr. Wenn Service down ist, gibt nginx 502 (nicht 401).
- `_pending` Dict ist in-memory → offene Write-Gates gehen bei Service-Neustart verloren
- `write_file` überschreibt immer komplett (kein Patch) → immer erst `read_file` aufrufen
- Secrets werden bei jedem Request frisch geladen (kein Cache)
- Relative API-Pfade im Frontend (`api/chat`) → korrekt über nginx-Proxy mit trailing slash
- **⚠️ NIEMALS `python3 -m venv --clear .` in `/opt/claude-remote/` ausführen** → löscht alle App-Dateien im Verzeichnis (Vorfall 2026-05-25). Stattdessen: frische venv außerhalb anlegen oder Dateien vorher sichern.

## Phase 2 – GitHub API ✅
Tool `github_api` mit Aktionen: `list_repos`, `read_file`, `list_commits` (sofort) + `write_file` (Write-Gate) + `create_repo` (Shell-Gate).
Credentials: `GITHUB_TOKEN` in `/etc/pka/secrets.env` (muss Josef selbst eintragen).
Dependency: `PyGithub>=2.0`.

## Phase 3 – Hetzner Shell ✅

Tool `run_shell` mit Whitelist-Aktionen:
- `service_status` – sofort (kein Gate), alle Services in `_SERVICES`
- `pip_upgrade` – Shell-Gate (Bestätigung), alle Apps in `_VENVS`
- `service_restart` – Shell-Gate, alle Services in `_SERVICES`
- `git_pull` – Shell-Gate, alle Projekte in `_GIT_PROJECTS`
- `apt_upgrade` – Shell-Gate, target=`kernel` → führt `apt-get dist-upgrade -y` aus (300s Timeout)

**Sudo-Regeln:** `/etc/sudoers.d/claude-remote` (chmod 0440) – www-data darf whitelisted pip/systemctl/git/apt-get als root.
**Pitfall git pull:** Erfordert `-c safe.directory=...` (ownership-Check seit git 2.35).
**Endpoint:** `POST /api/confirm-shell` mit `{shell_id, confirmed}`.
