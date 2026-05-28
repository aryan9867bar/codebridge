# CodeBridge — Collaborative Code Editor API

A real-time, two-person collaborative coding backend built with **FastAPI** and **WebSockets**. Two participants share a room, edit code simultaneously, and can execute Python snippets — all in-memory with no database required.

---

## Table of Contents

1. [Features](#features)
2. [Architecture Overview](#architecture-overview)
3. [Prerequisites](#prerequisites)
4. [Local Setup (Step-by-Step)](#local-setup-step-by-step)
5. [Running the Server](#running-the-server)
6. [API Reference](#api-reference)
7. [WebSocket Protocol](#websocket-protocol)
8. [Testing the API](#testing-the-api)
9. [Deploying to Render](#deploying-to-render)
10. [Known Limitations & Roadmap](#known-limitations--roadmap)

---

## Features

- 🚪 **Room management** — create/join/leave ephemeral rooms with 6-character codes
- 🔄 **Real-time sync** — WebSocket-based code broadcasting between participants
- 🖱️ **Cursor sharing** — broadcast cursor position and text selection to peers
- ▶️ **Code execution** — run Python snippets server-side (stdout/stderr/exit code returned)
- ⏳ **Auto-expiry** — rooms expire after 24 hours; empty rooms are cleaned up immediately
- 🌐 **CORS-ready** — open CORS by default (lock it down before going to production)

---

## Architecture Overview

```
Client A  ──────┐
                ├──► POST /rooms/{code}/join
                │
                ├──► WS  /ws/rooms/{code}  ◄──────────────────┐
                │         │                                     │
                │    [INIT → STATE]                             │
                │    [EDIT → PATCH broadcast] ─────────────►   │
                │    [CURSOR broadcast]       ─────────────►   │
                │    [PING / PONG]                              │
                │                                               │
Client B  ──────┘                                    Client B connects
```

All state is held in a Python dict (`rooms: Dict[str, Room]`). There is **no database** — a server restart clears all rooms.

---

## Prerequisites

| Tool | Minimum version | Install |
|------|----------------|---------|
| Python | 3.10+ | [python.org](https://python.org) |
| pip | 23+ | bundled with Python |
| git | any | [git-scm.com](https://git-scm.com) |

> **Windows users:** use PowerShell or WSL2. The commands below work on macOS, Linux, and WSL2 identically.

---

## Local Setup (Step-by-Step)

### Step 1 — Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/code-sync-render.git
cd code-sync-render
```

### Step 2 — Create a virtual environment

```bash
# macOS / Linux / WSL2
python3 -m venv .venv
source .venv/bin/activate

# Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

You should see `(.venv)` prepended to your terminal prompt.

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

All packages are installed at pinned versions:

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi[all]` | 0.115.6 | API framework + Swagger UI |
| `uvicorn[standard]` | 0.34.0 | ASGI server |
| `pydantic` | 2.10.4 | Request/response validation |
| `websockets` | 14.1 | WebSocket transport |

### Step 4 — Verify the install

```bash
python -c "import fastapi, uvicorn, pydantic; print('All good!')"
```

Expected output: `All good!`

---

## Running the Server

### Development mode (auto-reload on file changes)

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Production mode

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

> ⚠️ Keep `--workers 1`. The in-memory room store is **not shared across processes** — using multiple workers will cause rooms to be invisible to some requests.

Once running, open:

- **Swagger UI** → http://localhost:8000/docs
- **ReDoc** → http://localhost:8000/redoc
- **Health check** → http://localhost:8000/

---

## API Reference

### `POST /rooms` — Create a room

**Request body:**
```json
{
  "language": "python",
  "initial_code": "print('hello world')"
}
```

Both fields are optional. Omitting `initial_code` inserts a Python starter template.

**Response (201):**
```json
{
  "room_id": "uuid-...",
  "room_code": "AB12CD",
  "language": "python",
  "code": "def solution():\n    ...",
  "max_participants": 2,
  "websocket_url": "ws://localhost:8000/ws/rooms/AB12CD"
}
```

---

### `GET /rooms/{room_code}/status` — Check room status

**Response (200):**
```json
{
  "exists": true,
  "room_id": "uuid-...",
  "language": "python",
  "participants": 1,
  "max_participants": 2,
  "is_full": false
}
```

---

### `POST /rooms/{room_code}/join` — Join a room

**Request body:**
```json
{ "client_id": "my-unique-client-id" }
```

`client_id` is optional — a UUID is generated if omitted.

**Response (200):**
```json
{
  "participant_id": "my-unique-client-id",
  "role": "owner",
  "code": "...",
  "version": 0
}
```

`role` is `"owner"` for the first participant, `"participant"` for the second.

---

### `POST /rooms/{room_code}/leave` — Leave a room

**Request body:**
```json
{ "participant_id": "my-unique-client-id" }
```

**Response (200):**
```json
{ "message": "Left room successfully", "participants_remaining": 1 }
```

---

### `POST /rooms/{room_code}/run` — Execute code

**Request body:**
```json
{
  "code": "print(2 + 2)",
  "language": "python"
}
```

Omit `code` to run the room's current shared code.

**Response (200):**
```json
{
  "stdout": "4\n",
  "stderr": "",
  "exitCode": 0,
  "timeMs": 12
}
```

---

### `GET /rooms` — List all active rooms *(admin/debug)*

### `DELETE /rooms/{room_code}` — Delete a room *(admin/cleanup)*

---

## WebSocket Protocol

Connect to `ws://localhost:8000/ws/rooms/{room_code}`.

### 1. Send INIT (required first message)

```json
{
  "type": "INIT",
  "clientId": "your-unique-id",
  "participantId": "your-unique-id"
}
```

### 2. Server responds with STATE

```json
{
  "type": "STATE",
  "code": "current code in the room",
  "version": 3,
  "participants": 2
}
```

### 3. Send EDIT when the user types

```json
{
  "type": "EDIT",
  "code": "full updated code string"
}
```

Other participants receive a PATCH:
```json
{
  "type": "PATCH",
  "code": "full updated code string",
  "version": 4,
  "clientId": "sender-id"
}
```

### 4. Send CURSOR to broadcast cursor position

```json
{
  "type": "CURSOR",
  "position": 42,
  "selection": { "start": 40, "end": 45 }
}
```

### 5. Keepalive

```json
{ "type": "PING" }
```

Server replies: `{ "type": "PONG" }`

### Server-push events

| Type | Trigger |
|------|---------|
| `PARTICIPANT_JOINED` | Another user connects |
| `PARTICIPANT_LEFT` | Another user disconnects |
| `ERROR` | Bad init, room full, room not found |

---

## Testing the API

### Quick smoke test with curl

```bash
# 1. Create a room
curl -s -X POST http://localhost:8000/rooms \
  -H "Content-Type: application/json" \
  -d '{"language":"python"}' | python3 -m json.tool

# 2. Check room status (replace AB12CD with your room_code)
curl -s http://localhost:8000/rooms/AB12CD/status | python3 -m json.tool

# 3. Run some code
curl -s -X POST http://localhost:8000/rooms/AB12CD/run \
  -H "Content-Type: application/json" \
  -d '{"code":"print(\"hello from CodeSync!\")"}' | python3 -m json.tool
```

### Interactive Swagger UI

Navigate to http://localhost:8000/docs — every endpoint has a **Try it out** button.

### WebSocket test with wscat

```bash
npm install -g wscat
wscat -c ws://localhost:8000/ws/rooms/AB12CD
# then paste:
{"type":"INIT","clientId":"test-001","participantId":"test-001"}
```

---

## Deploying to Render

### Step 1 — Push to GitHub

```bash
git add .
git commit -m "initial commit"
git push origin main
```

### Step 2 — Create a Web Service on Render

1. Go to https://dashboard.render.com → **New → Web Service**
2. Connect your GitHub repository
3. Render auto-detects Python. Confirm these settings:

| Field | Value |
|-------|-------|
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| **Plan** | Free |

4. Click **Create Web Service**

### Step 3 — One-click deploy (alternative)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/render-examples/fastapi)

### Step 4 — Update WebSocket URL

After deployment, update the `websocket_url` in `create_room()` inside `main.py` (~line 168):

```python
websocket_url = f"wss://YOUR-APP.onrender.com/ws/rooms/{room_code}"
```

> Use `wss://` (secure WebSocket) for all production Render URLs.

### Step 5 — Lock CORS for production

```python
# main.py  ~line 14
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-frontend.com"],  # replace * with your domain
    ...
)
```

---

## Known Limitations & Roadmap

| Limitation | Details |
|-----------|---------|
| **In-memory only** | Rooms are lost on server restart. Add Redis or a DB for persistence. |
| **Single worker** | Cannot scale horizontally. Use Redis pub/sub to share state across workers. |
| **Python-only execution** | The `run` endpoint only supports Python. Use subprocess sandboxing for other languages. |
| **No auth** | Anyone with a room code can join. Add JWT or API key auth for production. |
| **Max 2 participants** | Hardcoded. Change `max_participants` in the `Room` class to increase. |
| **No OT/CRDT** | Concurrent edits use last-write-wins. Consider `yjs` or operational transforms for true conflict-free editing. |

---

## Bug Fixes Applied

- **`leave_room` now correctly decrements `participants_count`** — the original code deleted the participant record but forgot to decrement the counter, causing rooms to permanently appear full after any participant left.
- **Dependency versions pinned** — `requirements.txt` now uses fixed version numbers to ensure reproducible installs.
