"""Local browser UI for human-memory.

Serves a zero-dependency dashboard over the same state files read by the
`human-memory` CLI. The server is deliberately local-only and read-mostly: it
surfaces live sessions, known projects, and the selected HUMAN_MEMORY.md file.
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


MEMORY_FILE = "HUMAN_MEMORY.md"


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _short_path(path: str) -> str:
    home = str(Path.home())
    if path.startswith(home):
        return "~" + path[len(home):]
    return path


def known_projects(memory_store, sessions: list[dict]) -> list[dict]:
    """Return projects known from metadata plus any currently live sessions."""
    projects: dict[str, dict] = {}
    home = memory_store.agent_memory_home()
    projects_dir = home / "projects"
    if projects_dir.exists():
        for meta_path in sorted(projects_dir.glob("*/metadata.json")):
            meta = _read_json(meta_path)
            if not meta:
                continue
            memory_path = str(meta.get("memory_file") or meta_path.parent / MEMORY_FILE)
            cwd = str(meta.get("cwd") or "")
            key = str(meta.get("project_id") or meta_path.parent.name)
            projects[key] = {
                "project_id": key,
                "cwd": cwd,
                "label": Path(cwd).name if cwd else key,
                "display_cwd": _short_path(cwd) if cwd else "",
                "memory_path": memory_path,
                "storage": str(meta.get("storage") or "central"),
                "updated": meta.get("updated"),
                "live_sessions": 0,
                "stale_sessions": 0,
            }

    for s in sessions:
        key = str(s.get("project_id") or s.get("cwd") or s.get("memory_path"))
        cwd = str(s.get("cwd") or "")
        project = projects.setdefault(key, {
            "project_id": s.get("project_id"),
            "cwd": cwd,
            "label": Path(cwd).name if cwd else "Unknown project",
            "display_cwd": _short_path(cwd) if cwd else "",
            "memory_path": str(s.get("memory_path") or ""),
            "storage": str(s.get("memory_storage") or "project-file"),
            "updated": None,
            "live_sessions": 0,
            "stale_sessions": 0,
        })
        if not project.get("memory_path") and s.get("memory_path"):
            project["memory_path"] = str(s["memory_path"])
        project["live_sessions"] += 1
        if s.get("stale"):
            project["stale_sessions"] += 1

    return sorted(projects.values(), key=lambda p: (p["stale_sessions"] == 0, p["label"].lower()))


def read_memory(path: str) -> dict:
    p = Path(path)
    try:
        text = p.read_text()
    except OSError as e:
        return {"ok": False, "path": str(p), "error": str(e), "text": ""}
    try:
        stat = p.stat()
        mtime = int(stat.st_mtime)
    except OSError:
        mtime = None
    return {"ok": True, "path": str(p), "text": text, "mtime": mtime}


def make_handler(cli, memory_store):
    class Handler(BaseHTTPRequestHandler):
        server_version = "human-memory-ui/0.1"

        def log_message(self, fmt: str, *args) -> None:
            if getattr(self.server, "quiet", False):
                return
            super().log_message(fmt, *args)

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(_json_safe(payload), separators=(",", ":")).encode()
            self._send(status, "application/json; charset=utf-8", body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlparse(self.path)
            if parsed.path in {"/", "/index.html"}:
                self._send(200, "text/html; charset=utf-8", HTML.encode())
                return
            if parsed.path == "/api/snapshot":
                sessions = cli.load_sessions()
                self._json({
                    "sessions": sessions,
                    "projects": known_projects(memory_store, sessions),
                    "state_dir": cli.state_dir(),
                })
                return
            if parsed.path == "/api/memory":
                qs = parse_qs(parsed.query)
                path = (qs.get("path") or [""])[0]
                cwd = (qs.get("cwd") or [""])[0]
                if not path and cwd:
                    storage, _note = memory_store.load_storage()
                    path = str(memory_store.resolve(Path(cwd), storage).path)
                if not path:
                    self._json({"ok": False, "error": "missing path or cwd"}, status=400)
                    return
                self._json(read_memory(path))
                return
            self._json({"error": "not found"}, status=404)

    return Handler


def serve(cli, memory_store, host: str = "127.0.0.1", port: int = 8765,
          open_browser: bool = False, quiet: bool = False) -> int:
    httpd = ThreadingHTTPServer((host, port), make_handler(cli, memory_store))
    httpd.quiet = quiet
    url = f"http://{host}:{httpd.server_port}/"
    print(f"human-memory UI: {url}")
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
    return 0


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>human-memory</title>
<style>
:root {
  color-scheme: light;
  --bg: #f6f7f6;
  --panel: #ffffff;
  --panel-soft: #eef3f1;
  --ink: #20201d;
  --muted: #636963;
  --line: #d6ddd8;
  --accent: #0e6f68;
  --accent-ink: #ffffff;
  --danger: #b3261e;
  --danger-bg: #fff0ed;
  --ok: #287a42;
  --warn: #9a5b00;
  --focus: #a46a00;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--ink);
}
button, input {
  font: inherit;
}
.app {
  display: grid;
  grid-template-columns: minmax(280px, 360px) minmax(0, 1fr);
  min-height: 100vh;
}
.sidebar {
  border-right: 1px solid var(--line);
  background: #fbfcfb;
  padding: 18px;
  overflow: auto;
}
.brand {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: center;
  margin-bottom: 18px;
}
.brand h1 {
  font-size: 21px;
  line-height: 1.1;
  margin: 0;
  letter-spacing: 0;
}
.refresh {
  width: 38px;
  height: 38px;
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--ink);
  border-radius: 8px;
  cursor: pointer;
}
.refresh:hover { border-color: var(--accent); color: var(--accent); }
.refresh:focus-visible, .project:focus-visible, .session:focus-visible {
  outline: 3px solid color-mix(in srgb, var(--focus), transparent 65%);
  outline-offset: 2px;
}
.summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-bottom: 16px;
}
.metric {
  min-width: 0;
  padding: 10px;
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
}
.metric strong {
  display: block;
  font-size: 24px;
  line-height: 1;
}
.metric span {
  display: block;
  margin-top: 5px;
  color: var(--muted);
  font-size: 12px;
}
.section-title {
  margin: 18px 0 8px;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
}
.list {
  display: grid;
  gap: 8px;
}
.project, .session {
  display: grid;
  gap: 5px;
  width: 100%;
  padding: 11px;
  text-align: left;
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
  cursor: pointer;
}
.project:hover, .session:hover { border-color: var(--accent); }
.project.active, .session.active {
  border-color: var(--accent);
  box-shadow: inset 3px 0 0 var(--accent);
}
.row {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
}
.name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 700;
}
.path, .meta {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--muted);
  font-size: 12px;
}
.pill {
  display: inline-flex;
  align-items: center;
  min-height: 23px;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--panel-soft);
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}
.pill.stale { border-color: #efb1aa; background: var(--danger-bg); color: var(--danger); }
.pill.fresh { border-color: #b9d6c2; background: #edf8ef; color: var(--ok); }
.pill.warn { border-color: #efd19b; background: #fff7e8; color: var(--warn); }
.main {
  display: grid;
  grid-template-rows: auto 1fr;
  min-width: 0;
}
.topbar {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  padding: 22px 28px;
  border-bottom: 1px solid var(--line);
  background: rgba(255,255,255,.72);
}
.title h2 {
  margin: 0 0 6px;
  font-size: 24px;
  line-height: 1.15;
  letter-spacing: 0;
}
.title p {
  margin: 0;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.content {
  min-width: 0;
  padding: 20px 28px 28px;
  overflow: auto;
}
.empty {
  max-width: 620px;
  padding: 24px;
  border: 1px dashed var(--line);
  background: rgba(255,255,255,.62);
  border-radius: 8px;
  color: var(--muted);
}
.memory-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.memory-section {
  min-width: 0;
  padding: 16px;
  border: 1px solid var(--line);
  background: var(--panel);
  border-radius: 8px;
}
.memory-section.full { grid-column: 1 / -1; }
.memory-section h3 {
  margin: 0 0 10px;
  font-size: 14px;
  letter-spacing: 0;
}
.memory-section pre {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: #2e2d29;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.55;
}
.raw {
  margin-top: 12px;
}
.raw summary {
  cursor: pointer;
  color: var(--accent);
  font-weight: 700;
}
.raw pre {
  margin: 10px 0 0;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #191815;
  color: #f5f7f4;
  overflow: auto;
  white-space: pre-wrap;
}
.error {
  color: var(--danger);
}
@media (max-width: 820px) {
  .app { grid-template-columns: 1fr; }
  .sidebar {
    border-right: 0;
    border-bottom: 1px solid var(--line);
    max-height: none;
    overflow: visible;
  }
  .topbar {
    padding: 18px;
    display: grid;
  }
  .content { padding: 16px 18px 22px; }
  .memory-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <h1>human-memory</h1>
      <button class="refresh" id="refresh" title="Refresh" aria-label="Refresh">↻</button>
    </div>
    <div class="summary">
      <div class="metric"><strong id="metric-projects">0</strong><span>projects</span></div>
      <div class="metric"><strong id="metric-live">0</strong><span>live</span></div>
      <div class="metric"><strong id="metric-stale">0</strong><span>stale</span></div>
    </div>
    <div class="section-title">Live Sessions</div>
    <div class="list" id="sessions"></div>
    <div class="section-title">Projects</div>
    <div class="list" id="projects"></div>
  </aside>
  <main class="main">
    <header class="topbar">
      <div class="title">
        <h2 id="title">Select a project</h2>
        <p id="subtitle">Live agent state and HUMAN_MEMORY.md appear here.</p>
      </div>
      <div class="actions" id="badges"></div>
    </header>
    <section class="content" id="content">
      <div class="empty">No project selected.</div>
    </section>
  </main>
</div>
<script>
const sections = ["Current State", "What Just Happened", "Pending", "Key Decisions", "Where I Left Off"];
let snapshot = {sessions: [], projects: []};
let selectedPath = "";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
  }[c]));
}

function statusPill(text, cls) {
  return `<span class="pill ${cls || ""}">${esc(text)}</span>`;
}

function parseMemory(text) {
  const result = [];
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const match = /^##\s+(.+?)\s*$/.exec(lines[i]);
    if (!match || !sections.includes(match[1])) continue;
    const title = match[1];
    const start = i + 1;
    let end = lines.length;
    for (let j = start; j < lines.length; j++) {
      if (/^##\s+/.test(lines[j]) || /^<!-- \/hm:session=/.test(lines[j]) || /^<!-- hm:session=/.test(lines[j])) {
        end = j;
        break;
      }
    }
    result.push({title, body: lines.slice(start, end).join("\n").trim() || "(empty)"});
  }
  return result;
}

async function getJSON(url) {
  const res = await fetch(url, {cache: "no-store"});
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return await res.json();
}

function renderLists() {
  document.getElementById("metric-projects").textContent = snapshot.projects.length;
  document.getElementById("metric-live").textContent = snapshot.sessions.length;
  document.getElementById("metric-stale").textContent = snapshot.sessions.filter(s => s.stale).length;

  const sessions = document.getElementById("sessions");
  sessions.innerHTML = snapshot.sessions.length ? snapshot.sessions.map(s => {
    const state = !s.whiteboard_exists ? statusPill("no file", "warn") : s.stale ? statusPill("stale", "stale") : statusPill("fresh", "fresh");
    const path = s.cwd || s.memory_path || "";
    return `<button class="session ${selectedPath === s.memory_path ? "active" : ""}" data-path="${esc(s.memory_path)}" data-label="${esc(s.agent)} ${esc(s.pid)}">
      <div class="row"><span class="name">${esc(s.agent)} · ${esc(s.pid)}</span>${state}</div>
      <div class="path">${esc(path)}</div>
      <div class="meta">${esc(s.unrecorded_edits || 0)} edit(s) behind · updated ${esc(Math.round(s._age ?? 0))}s ago</div>
    </button>`;
  }).join("") : `<div class="empty">No live agent sessions.</div>`;

  const projects = document.getElementById("projects");
  projects.innerHTML = snapshot.projects.length ? snapshot.projects.map(p => {
    const state = p.stale_sessions ? statusPill(`${p.stale_sessions} stale`, "stale") :
      p.live_sessions ? statusPill(`${p.live_sessions} live`, "fresh") : statusPill(p.storage || "project", "");
    return `<button class="project ${selectedPath === p.memory_path ? "active" : ""}" data-path="${esc(p.memory_path)}" data-label="${esc(p.label)}">
      <div class="row"><span class="name">${esc(p.label)}</span>${state}</div>
      <div class="path">${esc(p.display_cwd || p.cwd || p.memory_path)}</div>
      <div class="meta">${esc(p.storage || "")}${p.project_id ? " · " + esc(p.project_id) : ""}</div>
    </button>`;
  }).join("") : `<div class="empty">No known projects yet.</div>`;

  document.querySelectorAll("[data-path]").forEach(btn => {
    btn.addEventListener("click", () => selectMemory(btn.dataset.path, btn.dataset.label));
  });
}

async function selectMemory(path, label) {
  selectedPath = path;
  renderLists();
  document.getElementById("title").textContent = label || "HUMAN_MEMORY.md";
  document.getElementById("subtitle").textContent = path || "";
  document.getElementById("content").innerHTML = `<div class="empty">Loading memory...</div>`;
  document.getElementById("badges").innerHTML = "";
  if (!path) {
    document.getElementById("content").innerHTML = `<div class="empty error">No memory path is available for this item.</div>`;
    return;
  }
  try {
    const data = await getJSON(`/api/memory?path=${encodeURIComponent(path)}`);
    if (!data.ok) throw new Error(data.error || "Unable to read memory");
    const parsed = parseMemory(data.text);
    document.getElementById("badges").innerHTML = statusPill("read-only", "") + statusPill(`${data.text.length} bytes`, "");
    if (!parsed.length) {
      document.getElementById("content").innerHTML = `<div class="empty">This file does not contain the five standard sections yet.</div>
        <details class="raw" open><summary>Raw file</summary><pre>${esc(data.text)}</pre></details>`;
      return;
    }
    document.getElementById("content").innerHTML = `<div class="memory-grid">${
      parsed.map((s, i) => `<article class="memory-section ${i === 0 ? "full" : ""}">
        <h3>${esc(s.title)}</h3><pre>${esc(s.body)}</pre>
      </article>`).join("")
    }</div><details class="raw"><summary>Raw file</summary><pre>${esc(data.text)}</pre></details>`;
  } catch (err) {
    document.getElementById("content").innerHTML = `<div class="empty error">${esc(err.message)}</div>`;
  }
}

async function refresh(keepSelection = true) {
  snapshot = await getJSON("/api/snapshot");
  renderLists();
  if (keepSelection && selectedPath) {
    const label = [...document.querySelectorAll("[data-path]")]
      .find(el => el.dataset.path === selectedPath)?.dataset.label;
    await selectMemory(selectedPath, label || "HUMAN_MEMORY.md");
  } else if (!selectedPath && snapshot.sessions[0]?.memory_path) {
    await selectMemory(snapshot.sessions[0].memory_path, `${snapshot.sessions[0].agent} ${snapshot.sessions[0].pid}`);
  } else if (!selectedPath && snapshot.projects[0]?.memory_path) {
    await selectMemory(snapshot.projects[0].memory_path, snapshot.projects[0].label);
  }
}

document.getElementById("refresh").addEventListener("click", () => refresh(true));
refresh(false).catch(err => {
  document.getElementById("content").innerHTML = `<div class="empty error">${esc(err.message)}</div>`;
});
setInterval(() => refresh(true).catch(() => {}), 5000);
</script>
</body>
</html>
"""
