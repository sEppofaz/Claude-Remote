# Claude Agent – Josef Fischer

## Überblick
Eigenständiger Python-Flask-KI-Agent mit direktem Dateizugriff auf Dropbox und Hetzner-Server. Unabhängig von Vereinskalender. Chat-Interface mit Write-Gate (Bestätigung vor jedem Schreibzugriff).

## Phasen

| Phase | Inhalt | Status |
|-------|--------|--------|
| 1 | Dropbox read/write/list für `/Apps/Claude/**` | ✅ umgesetzt |
| 2 | GitHub API: Repos erstellen, Dateien pushen, Commits | geplant |
| 3 | Hetzner Shell: `git pull`, `systemctl restart` per Tool | geplant |

## Lokaler Pfad
`~/Dropbox/Apps/Claude/Claude-Agent/`

## GitHub
Noch anlegen → `sEppofaz/Claude-Agent`

## Deployment auf Hetzner

- **Port:** 8082 (intern)
- **Service-Pfad:** `/opt/claude-agent/`
- **Systemd:** `claude-agent.service`
- **Nginx-Pfad:** `/agent/` → `http://127.0.0.1:8082/`
- **Secrets:** `/etc/pka/secrets.env` (gelesen via `_load_secrets()`)

### Nginx-Snippet (zu `/etc/nginx/sites-enabled/` hinzufügen)
```nginx
location /agent/ {
    proxy_pass http://127.0.0.1:8082/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

### Systemd Service (`/etc/systemd/system/claude-agent.service`)
```ini
[Unit]
Description=Claude Agent
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/claude-agent
ExecStart=/opt/claude-agent/bin/gunicorn -w 1 -b 127.0.0.1:8082 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

### Server-Setup (einmalig)
```bash
# Dateien hochladen:
scp -r ~/Dropbox/Apps/Claude/Claude-Agent root@89.167.104.145:/opt/claude-agent

# Venv + Pakete:
cd /opt/claude-agent
python3 -m venv .
bin/pip install -r requirements.txt

# Icons generieren:
bin/python3 generate_icons.py

# Service starten:
cp claude-agent.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now claude-agent

# Nginx neu laden:
nginx -t && systemctl reload nginx
```

### Aktualisierung
```bash
scp ~/Dropbox/Apps/Claude/Claude-Agent/app.py root@89.167.104.145:/opt/claude-agent/
scp ~/Dropbox/Apps/Claude/Claude-Agent/static/index.html root@89.167.104.145:/opt/claude-agent/static/
systemctl restart claude-agent
```

## Dateistruktur
```
Claude-Agent/
├── app.py              # Flask-App (Phase 1: Dropbox-Tools)
├── requirements.txt
├── generate_icons.py   # Icon-Generator (stdlib, kein Pillow)
├── CLAUDE.md
└── static/
    ├── index.html      # Chat-UI (PWA)
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
| `server:` | `/opt/claude-agent/` |
| `server:` | `/opt/traktoren/` |

## Pitfalls
- `_pending` Dict ist in-memory → offene Write-Gates gehen bei Service-Neustart verloren
- `write_file` überschreibt immer komplett (kein Patch) → immer erst `read_file` aufrufen
- Secrets werden bei jedem Request frisch geladen (kein Cache)
- Relative API-Pfade im Frontend (`api/chat`) → korrekt über nginx-Proxy mit trailing slash

## Phase 2 – GitHub API (geplant)
Neues Tool `github_api` mit Aktionen: `list_repos`, `create_repo`, `read_file`, `write_file`, `list_commits`.
Credentials: `GITHUB_TOKEN` in `/etc/pka/secrets.env`.

## Phase 3 – Hetzner Shell (geplant)
Neues Tool `run_shell` mit Whitelist erlaubter Befehle:
- `git -C /opt/{projekt} pull`
- `systemctl restart {service}`
- `systemctl status {service}`
Nur auf expliziten Wunsch + separate Bestätigung.
