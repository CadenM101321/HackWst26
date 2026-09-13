import json
import os
from typing import Dict, List
from urllib.parse import quote, urlparse
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path
from fastapi import UploadFile, File, Form
from Transcriber.Transcribe import run_pipeline
from tutormatch import (
    save_pipeline_result,
    get_transcript,
    get_session_notes,
    attach_participant,
    get_session_participants,
)
from ice_servers import get_ice_servers
from websitefiles.session_bridge import user_from_cookies

app = FastAPI(title="TutorMatch Backend")


# Login lives in one place: the Flask site, through Auth0. This app never logs
# anyone in - it reads the Auth0 user from the site's session cookie, so every
# user["sub"] below is a real Auth0 ID, matching what TigerData keys users by.
def get_current_user(request: Request) -> dict | None:
    return user_from_cookies(request.cookies)


def _site_url() -> str:
    """The Flask site, where the Auth0 login routes live."""
    return os.getenv("APP_BASE_URL", "http://localhost:5000").strip().rstrip("/")


def _wrong_host(request: Request) -> RedirectResponse | None:
    """Switch to the site's hostname if the browser used a different one.

    The login cookie is only sent back to the hostname that set it, so a visit
    to 127.0.0.1 can't see a login made on localhost. Keeps port and path.
    """
    site_host = urlparse(_site_url()).hostname
    if site_host and request.url.hostname != site_host:
        return RedirectResponse(str(request.url.replace(hostname=site_host)))
    return None


def _login_redirect(request: Request, route: str = "login", back: str | None = None) -> RedirectResponse:
    """Send the browser to Auth0 login on the site, returning here afterwards."""
    return RedirectResponse(f"{_site_url()}/{route}?next={quote(back or str(request.url), safe='')}")


# ============================================================
# CONNECTION MANAGER
# ============================================================
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, room_id: str):
        await websocket.accept()
        self.active_connections.setdefault(room_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket, room_id: str):
        if room_id in self.active_connections:
            if websocket in self.active_connections[room_id]:
                self.active_connections[room_id].remove(websocket)
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]

    async def broadcast(self, message: str, room_id: str, sender: WebSocket):
        for connection in self.active_connections.get(room_id, []):
            if connection != sender:
                await connection.send_text(message)
VIDEO_FOLDER = Path(__file__).parent / "Video_Folder"
VIDEO_FOLDER.mkdir(exist_ok=True)


@app.post("/upload_recording")
async def upload_recording(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    role: str = Form(...),
):
    filename = f"{session_id}_{role}.webm"
    save_path = VIDEO_FOLDER / filename
    with open(save_path, "wb") as f:
        f.write(await file.read())

    transcription = None
    if role == "tutor":
      output_json_path = VIDEO_FOLDER / f"{session_id}_{role}_output.json"
      transcription = run_pipeline(str(save_path), output_path=str(output_json_path))
      if transcription:
        try:
          save_pipeline_result(session_id, transcription, recording_url=str(save_path))
        except Exception as db_err:
          print(f"Warning: could not save transcript to database: {db_err}")

    return {"status": "ok", "saved_to": str(save_path), "transcription": transcription}

manager = ConnectionManager()


# ============================================================
# SHARED CSS
# ============================================================
SHARED_CSS = """
:root {
  --green: #25A36F;
  --teal: #069494;
  --deep: #00637c;
  --deep-2: #004a5e;
  --deep-3: #00333f;
  --text: #e8f4f5;
  --text-dim: #a8c4c8;
  --glass: rgba(255, 255, 255, 0.08);
  --glass-hover: rgba(255, 255, 255, 0.14);
  --shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, sans-serif;
  background: linear-gradient(160deg, var(--deep) 0%, var(--deep-2) 55%, var(--deep-3) 100%);
  color: var(--text);
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 32px 16px;
}
h1, h2 {
  font-weight: 600;
  letter-spacing: -0.01em;
  margin: 0 0 8px 0;
}
h1 { font-size: 28px; }
h2 { font-size: 22px; }
p.subtitle {
  color: var(--text-dim);
  margin: 0 0 24px 0;
  font-size: 14px;
}
.card {
  background: var(--glass);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 18px;
  padding: 24px;
  box-shadow: var(--shadow);
  width: 100%;
  max-width: 760px;
}
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.02em;
  text-transform: uppercase;
}
.badge.tutee { background: var(--green); color: white; }
.badge.tutor { background: var(--teal); color: white; }
.badge.unknown { background: #666; color: white; }
.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 18px;
}
.meta-pill {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 10px;
  padding: 6px 12px;
  font-size: 13px;
  color: var(--text-dim);
}
input[type="text"], select {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 10px;
  color: var(--text);
  padding: 10px 14px;
  font-size: 14px;
  outline: none;
  transition: border-color 0.15s ease, background 0.15s ease;
  width: 100%;
}
input[type="text"]:focus, select:focus {
  border-color: var(--teal);
  background: rgba(255, 255, 255, 0.12);
}
input::placeholder { color: var(--text-dim); }
button {
  border: none;
  border-radius: 10px;
  padding: 10px 18px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: transform 0.08s ease, filter 0.15s ease, background 0.15s ease;
  color: white;
  background: var(--teal);
}
button:hover { filter: brightness(1.12); }
button:active { transform: scale(0.97); }
button.primary { background: var(--green); }
button.ghost {
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.15);
}
button.ghost:hover { background: var(--glass-hover); }
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  filter: none;
  transform: none;
}
.status-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #888;
  margin-right: 6px;
  vertical-align: middle;
}
.status-dot.live { background: var(--green); box-shadow: 0 0 8px var(--green); }
"""


# ============================================================
# 1. PRE-CALL CHAT WEBSOCKET
# ============================================================
@app.websocket("/ws/precall/{match_id}/{client_id}")
async def precall_chat(websocket: WebSocket, match_id: str, client_id: str):
    room_id = f"precall-{match_id}"
    await manager.connect(websocket, room_id)

    await manager.broadcast(
        json.dumps({"type": "user_joined", "client_id": client_id}),
        room_id,
        websocket,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            if data.get("type") == "chat":
                await manager.broadcast(
                    json.dumps({
                        "type": "chat",
                        "client_id": client_id,
                        "content": data.get("content", ""),
                    }),
                    room_id,
                    websocket,
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast(
            json.dumps({"type": "user_left", "client_id": client_id}),
            room_id,
            websocket,
        )


# ============================================================
# 2. IN-CALL WEBSOCKET
# ============================================================
@app.websocket("/ws/call/{session_id}/{client_id}")
async def call_channel(websocket: WebSocket, session_id: str, client_id: str):
    room_id = f"call-{session_id}"
    await manager.connect(websocket, room_id)

    await manager.broadcast(
        json.dumps({"type": "user_joined", "client_id": client_id}),
        room_id,
        websocket,
    )

    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type in ("offer", "answer", "ice-candidate"):
                await manager.broadcast(raw, room_id, websocket)
            elif msg_type == "chat":
                await manager.broadcast(
                    json.dumps({
                        "type": "chat",
                        "client_id": client_id,
                        "content": data.get("content", ""),
                    }),
                    room_id,
                    websocket,
                )
            elif msg_type == "screen_share_toggle":
                await manager.broadcast(
                    json.dumps({
                        "type": "screen_share_toggle",
                        "client_id": client_id,
                        "sharing": data.get("sharing", False),
                    }),
                    room_id,
                    websocket,
                )
    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
        await manager.broadcast(
            json.dumps({"type": "user_left", "client_id": client_id}),
            room_id,
            websocket,
        )


# ============================================================
# 3. ROUTES
# ============================================================
@app.get("/login")
async def login(request: Request):
    """Log in through Auth0 on the main site, then return to this app's home."""
    if (bounce := _wrong_host(request)) is not None:
        return bounce
    return _login_redirect(request, back=str(request.url.replace(path="/", query="")))


@app.get("/signup")
async def signup(request: Request):
    """Sign up through Auth0 on the main site, then return to this app's home."""
    if (bounce := _wrong_host(request)) is not None:
        return bounce
    return _login_redirect(request, route="signup", back=str(request.url.replace(path="/", query="")))


@app.get("/logout")
async def logout():
    """Log out of Auth0 through the main site."""
    return RedirectResponse(f"{_site_url()}/logout")

@app.get("/")
async def root():
    return HTMLResponse(INDEX_HTML)


@app.get("/precall")
async def precall_page():
    return HTMLResponse(PRECALL_HTML)


@app.get("/call")
async def call_page(request: Request, session: str = "", role: str = ""):
    if (bounce := _wrong_host(request)) is not None:
        return bounce
    user = get_current_user(request)
    if not user:
        return _login_redirect(request)
    if session and role:
        try:
            attach_participant(session, role, user["sub"])
        except Exception as db_err:
            print(f"Warning: could not attach participant to session: {db_err}")
    return HTMLResponse(CALL_HTML)


@app.get("/ice-servers")
def ice_servers():
    """STUN + TURN servers for the call page. Credentials come from Cloudflare."""
    return {"iceServers": get_ice_servers()}


@app.get("/transcript/{session_id}")
async def transcript_page(session_id: str, request: Request):
    if (bounce := _wrong_host(request)) is not None:
        return bounce
    user = get_current_user(request)
    if not user:
        return _login_redirect(request)
    participants = get_session_participants(session_id) or {}
    if user["sub"] not in (participants.get("student_id"), participants.get("tutor_id")):
        raise HTTPException(status_code=403, detail="You weren't a participant in this session")
    return HTMLResponse(TRANSCRIPT_HTML)


@app.get("/api/transcript/{session_id}")
async def api_transcript(session_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    participants = get_session_participants(session_id) or {}
    if user["sub"] not in (participants.get("student_id"), participants.get("tutor_id")):
        raise HTTPException(status_code=403, detail="You weren't a participant in this session")
    segments = get_transcript(session_id)
    notes = get_session_notes(session_id)
    return {
        "session_id": session_id,
        "ready": bool(segments),
        "segments": segments,
        "notes": notes,
    }

# ============================================================
# HOME PAGE
# ============================================================
INDEX_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>TutorMatch</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
""" + SHARED_CSS + """
.hero {
  text-align: center;
  margin-bottom: 28px;
}
.hero h1 {
  font-size: 34px;
  background: linear-gradient(90deg, var(--green), var(--teal));
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 8px;
}
@media (max-width: 600px) { .grid { grid-template-columns: 1fr; } }
.launch-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 18px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  text-decoration: none;
  color: var(--text);
  transition: transform 0.12s ease, background 0.15s ease, border-color 0.15s ease;
}
.launch-card:hover {
  transform: translateY(-3px);
  background: rgba(255, 255, 255, 0.11);
  border-color: var(--teal);
}
.launch-card .title { font-weight: 600; font-size: 16px; }
.launch-card .desc { color: var(--text-dim); font-size: 13px; }
</style>
</head>
<body>
<div class="card" style="max-width: 720px;">
  <div class="hero">
    <h1>TutorMatch</h1>
    <p class="subtitle">Peer tutoring with AI-powered, timestamped session notes.</p>
  </div>

  <h2 style="font-size:16px; color: var(--text-dim); font-weight:500; margin-bottom:10px;">
    Pre-call chat (schedule + questions)
  </h2>
  <div class="grid">
    <a class="launch-card" href="/precall?role=tutee&match=match-123">
      <span class="title">Join as Tutee</span>
      <span class="desc">Ask questions, coordinate a meeting time.</span>
    </a>
    <a class="launch-card" href="/precall?role=tutor&match=match-123">
      <span class="title">Join as Tutor</span>
      <span class="desc">Answer questions, confirm your availability.</span>
    </a>
  </div>

  <h2 style="font-size:16px; color: var(--text-dim); font-weight:500; margin:22px 0 10px 0;">
    Video call (with hidden chat + screen share)
  </h2>
  <div class="grid">
    <a class="launch-card" href="/call?role=tutee&session=session-123">
      <span class="title">Join as Tutee</span>
      <span class="desc">Start the video call as the learner.</span>
    </a>
    <a class="launch-card" href="/call?role=tutor&session=session-123">
      <span class="title">Join as Tutor</span>
      <span class="desc">Start the video call as the teacher.</span>
    </a>
  </div>
</div>
</body>
</html>
"""


# ============================================================
# PRE-CALL PAGE
# ============================================================
PRECALL_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>TutorMatch - Pre-call Chat</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
""" + SHARED_CSS + """
.chat-shell {
  display: flex;
  flex-direction: column;
  height: 60vh;
  min-height: 380px;
  background: rgba(0, 0, 0, 0.22);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 14px;
  overflow: hidden;
  margin-top: 16px;
}
.chat-log {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.chat-log::-webkit-scrollbar { width: 8px; }
.chat-log::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.15);
  border-radius: 8px;
}
.msg {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.45;
  word-wrap: break-word;
}
.msg.mine {
  align-self: flex-end;
  background: var(--green);
  color: white;
  border-bottom-right-radius: 4px;
}
.msg.theirs {
  align-self: flex-start;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-bottom-left-radius: 4px;
}
.msg.system {
  align-self: center;
  background: transparent;
  color: var(--text-dim);
  font-size: 12px;
  font-style: italic;
  padding: 2px 8px;
}
.msg .who {
  display: block;
  font-size: 11px;
  font-weight: 700;
  opacity: 0.75;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 3px;
}
.composer {
  display: flex;
  gap: 8px;
  padding: 12px;
  background: rgba(0, 0, 0, 0.3);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}
.composer input { flex: 1; }
</style>
</head>
<body>
<div class="card" style="max-width: 720px;">
  <div class="meta-row">
    <span id="roleBadge" class="badge unknown">-</span>
    <span class="meta-pill">Match: <span id="matchLabel">match-123</span></span>
    <span class="meta-pill"><span id="statusDot" class="status-dot"></span><span id="statusText">Disconnected</span></span>
  </div>
  <h2>Pre-call Chat</h2>
  <p class="subtitle">Coordinate a time and ask any questions before your session.</p>

  <div class="chat-shell">
    <div id="messages" class="chat-log"></div>
    <div class="composer">
      <input id="input" type="text" placeholder="Type a message and press Enter..." autocomplete="off">
      <button class="primary" onclick="send()">Send</button>
    </div>
  </div>
</div>

<script>
const params = new URLSearchParams(window.location.search);
const myClientId = (params.get('role') || 'tutee').toLowerCase();
const matchId = params.get('match') || 'match-123';

// Derive ws:// or wss:// based on the current page protocol
const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
const wsHost = window.location.host;

document.getElementById('roleBadge').textContent = myClientId === 'tutor' ? 'Tutor' : 'Tutee';
document.getElementById('roleBadge').className = 'badge ' + (myClientId === 'tutor' ? 'tutor' : 'tutee');
document.getElementById('matchLabel').textContent = matchId;

let ws = null;

function setStatus(live, text) {
  document.getElementById('statusDot').className = 'status-dot' + (live ? ' live' : '');
  document.getElementById('statusText').textContent = text;
}

function appendMessage(kind, text, who) {
  const box = document.getElementById('messages');
  const div = document.createElement('div');
  div.className = 'msg ' + kind;
  if (who) {
    const whoEl = document.createElement('span');
    whoEl.className = 'who';
    whoEl.textContent = who;
    div.appendChild(whoEl);
  }
  div.appendChild(document.createTextNode(text));
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    ws.close();
  }
  ws = new WebSocket(`${wsProtocol}${wsHost}/ws/precall/${matchId}/${myClientId}`);

  ws.addEventListener('open', () => {
    setStatus(true, 'Connected');
    appendMessage('system', `You joined as ${myClientId}.`);
  });

  ws.addEventListener('close', () => setStatus(false, 'Disconnected'));

  ws.addEventListener('message', (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'chat') {
      if (data.client_id !== myClientId) {
        appendMessage('theirs', data.content, data.client_id);
      }
    } else if (data.type === 'user_joined') {
      if (data.client_id !== myClientId)
        appendMessage('system', `${data.client_id} joined the chat.`);
    } else if (data.type === 'user_left') {
      appendMessage('system', `${data.client_id} left the chat.`);
    }
  });
}

function send() {
  const input = document.getElementById('input');
  const text = input.value.trim();
  if (!text) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  appendMessage('mine', text, 'You');
  ws.send(JSON.stringify({ type: 'chat', content: text }));
  input.value = '';
  input.focus();
}

document.getElementById('input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); send(); }
});

connect();
</script>
</body>
</html>
"""


# ============================================================
# CALL PAGE
# ============================================================
CALL_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>TutorMatch - Video Call</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
""" + SHARED_CSS + """
.card.wide { max-width: 1100px; }
.stage {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
  border-radius: 16px;
  overflow: hidden;
  box-shadow: var(--shadow);
  border: 1px solid rgba(255, 255, 255, 0.08);
}
#remoteVideo {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  background: #000;
}
#remoteVideo.contain { object-fit: contain; }
.local-wrap {
  position: absolute;
  bottom: 14px;
  right: 14px;
  width: 180px;
  aspect-ratio: 16 / 9;
  border-radius: 12px;
  overflow: hidden;
  border: 2px solid var(--teal);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
  background: #000;
  z-index: 5;
}
#localVideo { width: 100%; height: 100%; object-fit: cover; display: block; }
.chat-panel {
  position: absolute;
  top: 0;
  right: 0;
  width: 280px;
  height: 100%;
  background: rgba(0, 40, 50, 0.9);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-left: 1px solid rgba(255, 255, 255, 0.1);
  display: flex;
  flex-direction: column;
  transform: translateX(100%);
  transition: transform 0.25s ease;
  z-index: 10;
}
.chat-panel.open { transform: translateX(0); }
.chat-panel .log {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  font-size: 13px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chat-panel .log::-webkit-scrollbar { width: 6px; }
.chat-panel .log::-webkit-scrollbar-thumb {
  background: rgba(255,255,255,0.18); border-radius: 6px;
}
.chat-panel .composer {
  display: flex;
  gap: 6px;
  padding: 10px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}
.chat-panel input { font-size: 13px; padding: 8px 10px; }
.chat-panel button { padding: 8px 12px; font-size: 13px; }

.cmsg { max-width: 90%; padding: 7px 10px; border-radius: 10px; font-size: 13px; line-height: 1.4; word-wrap: break-word; }
.cmsg.mine { align-self: flex-end; background: var(--green); color: white; border-bottom-right-radius: 3px; }
.cmsg.theirs { align-self: flex-start; background: rgba(255, 255, 255, 0.12); border-bottom-left-radius: 3px; }
.cmsg.system { align-self: center; color: var(--text-dim); font-size: 11px; font-style: italic; }

.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  justify-content: center;
  margin-top: 18px;
}
.controls button { min-width: 140px; }
.share-pill {
  position: absolute;
  top: 14px;
  left: 14px;
  background: rgba(37, 163, 111, 0.9);
  color: white;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  display: none;
  z-index: 6;
}
.share-pill.on { display: inline-block; }
</style>
</head>
<body>
<div class="card wide">
  <div class="meta-row">
    <span id="roleBadge" class="badge unknown">-</span>
    <span class="meta-pill">Session: <span id="sessionLabel">session-123</span></span>
    <span class="meta-pill"><span id="statusDot" class="status-dot"></span><span id="statusText">Not connected</span></span>
  </div>
  <h2>Video Session</h2>
  <p class="subtitle">Chat, screen-share, and take notes - all in one place.</p>

  <div class="stage" id="stage">
    <video id="remoteVideo" autoplay playsinline></video>
    <div class="local-wrap"><video id="localVideo" autoplay playsinline muted></video></div>
    <div id="sharePill" class="share-pill">You are sharing your screen</div>

    <div id="chatPanel" class="chat-panel">
      <div id="chatLog" class="log"></div>
      <div class="composer">
        <input id="chatInput" type="text" placeholder="Message..." autocomplete="off">
        <button class="primary" onclick="sendChat()">Send</button>
      </div>
    </div>
  </div>

  <div class="controls">
    <button class="primary" id="joinBtn" onclick="start()">Join Call</button>
    <button class="ghost" id="shareBtn" onclick="toggleShare()" disabled>Share Screen</button>
    <button class="ghost" id="chatBtn" onclick="toggleChat()" disabled>Toggle Chat</button>
        <button class="ghost" id="endBtn" onclick="endCall()" disabled>End Call</button>
        <button class="ghost" id="transcriptBtn" onclick="viewTranscript()" style="display:none;">View Transcript</button>
  </div>
</div>

<script>
const params = new URLSearchParams(window.location.search);
const myClientId = (params.get('role') || 'tutee').toLowerCase();
const sessionId = params.get('session') || 'session-123';

// Derive ws:// or wss:// based on the current page protocol
const wsProtocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
const wsHost = window.location.host;

document.getElementById('roleBadge').textContent = myClientId === 'tutor' ? 'Tutor' : 'Tutee';
document.getElementById('roleBadge').className = 'badge ' + (myClientId === 'tutor' ? 'tutor' : 'tutee');
document.getElementById('sessionLabel').textContent = sessionId;

let ws = null;
let pc = null;
let localStream = null;
let screenStream = null;
let makingOffer = false;
let ignoreOffer = false;
let mediaRecorder = null;
let recordedChunks = [];
const isPolite = myClientId === 'tutor';
const pendingCandidates = [];

// Used if /ice-servers can't be reached. Direct connections still work on most
// networks; this only loses the TURN relay for restrictive ones.
const FALLBACK_ICE = {
  iceServers: [
    { urls: 'stun:stun.l.google.com:19302' },
    { urls: 'stun:stun1.l.google.com:19302' },
  ]
};

// STUN + TURN from the server, which trades our Cloudflare token for
// short-lived relay credentials so the token never reaches the browser.
async function loadIceServers() {
  try {
    const resp = await fetch('/ice-servers');
    if (resp.ok) return await resp.json();
  } catch (err) {
    console.warn('Could not load ICE servers, using STUN only:', err);
  }
  return FALLBACK_ICE;
}

function setStatus(live, text) {
  document.getElementById('statusDot').className = 'status-dot' + (live ? ' live' : '');
  document.getElementById('statusText').textContent = text;
}

function appendChat(kind, text, who) {
  const log = document.getElementById('chatLog');
  const div = document.createElement('div');
  div.className = 'cmsg ' + kind;
  if (who) {
    const w = document.createElement('b');
    w.textContent = who + ': ';
    div.appendChild(w);
  }
  div.appendChild(document.createTextNode(text));
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function showRemoteAsCamera() {
  document.getElementById('remoteVideo').classList.remove('contain');
}
function showRemoteAsScreen() {
  document.getElementById('remoteVideo').classList.add('contain');
}

async function start() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    ws.close();
  }

  try {
    localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
  } catch (err) {
    alert('Camera/microphone access is required. ' + err.message);
    return;
  }
  document.getElementById('localVideo').srcObject = localStream;
    if (myClientId === 'tutor') {
    startRecording();
  }

  pc = new RTCPeerConnection(await loadIceServers());
  localStream.getTracks().forEach(t => pc.addTrack(t, localStream));

  pc.ontrack = (e) => {
    const remoteVideo = document.getElementById('remoteVideo');
    if (!remoteVideo.srcObject) {
      remoteVideo.srcObject = e.streams[0];
    }
    if (e.track.kind === 'video') {
      const check = () => {
        if (remoteVideo.videoWidth && remoteVideo.videoHeight) {
          const ratio = remoteVideo.videoWidth / remoteVideo.videoHeight;
          if (ratio > 1.5) showRemoteAsScreen();
          else showRemoteAsCamera();
        }
      };
      e.track.addEventListener('unmute', check);
      remoteVideo.addEventListener('loadedmetadata', check);
      setTimeout(check, 500);
    }
  };

  pc.onicecandidate = (e) => {
    if (e.candidate && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'ice-candidate', candidate: e.candidate }));
    }
  };

  pc.onnegotiationneeded = async () => {
    try {
      makingOffer = true;
      await pc.setLocalDescription();
      ws.send(JSON.stringify({ type: 'offer', sdp: pc.localDescription.sdp }));
    } catch (err) {
      console.error(err);
    } finally {
      makingOffer = false;
    }
  };

  pc.onconnectionstatechange = () => {
    if (pc.connectionState === 'connected') setStatus(true, 'Connected');
    if (pc.connectionState === 'failed') setStatus(false, 'Connection failed');
  };

  ws = new WebSocket(`${wsProtocol}${wsHost}/ws/call/${sessionId}/${myClientId}`);

  ws.addEventListener('open', () => {
    setStatus(true, 'Signaling connected');
    appendChat('system', `You joined as ${myClientId}.`);
    document.getElementById('joinBtn').disabled = true;
    document.getElementById('shareBtn').disabled = false;
    document.getElementById('chatBtn').disabled = false;
        document.getElementById('endBtn').disabled = false;
  });

  ws.addEventListener('close', () => setStatus(false, 'Disconnected'));

  ws.addEventListener('message', async (e) => {
    const data = JSON.parse(e.data);
    const type = data.type;

    if (type === 'offer' || type === 'answer') {
      const desc = { type, sdp: data.sdp };
      const offerCollision = type === 'offer' && (makingOffer || pc.signalingState !== 'stable');
      ignoreOffer = !isPolite && offerCollision;

      if (ignoreOffer) return;

      if (offerCollision) {
        await Promise.all([
          pc.setLocalDescription({ type: 'rollback' }),
          pc.setRemoteDescription(desc),
        ]);
      } else {
        await pc.setRemoteDescription(desc);
      }

      while (pendingCandidates.length) {
        const cand = pendingCandidates.shift();
        try { await pc.addIceCandidate(cand); } catch (err) { console.error(err); }
      }

      if (type === 'offer') {
        await pc.setLocalDescription();
        ws.send(JSON.stringify({ type: 'answer', sdp: pc.localDescription.sdp }));
      }
    } else if (type === 'ice-candidate') {
      const cand = data.candidate;
      if (!pc.remoteDescription) {
        pendingCandidates.push(cand);
      } else {
        try { await pc.addIceCandidate(cand); } catch (err) { console.error(err); }
      }
    } else if (type === 'chat') {
      if (data.client_id !== myClientId) appendChat('theirs', data.content, data.client_id);
    } else if (type === 'screen_share_toggle') {
      if (data.client_id !== myClientId) {
        if (data.sharing) {
          showRemoteAsScreen();
          appendChat('system', `${data.client_id} started screen sharing.`);
        } else {
          showRemoteAsCamera();
          appendChat('system', `${data.client_id} stopped screen sharing.`);
        }
      }
    }
  });
}

async function toggleShare() {
  if (!pc) return;
  const pill = document.getElementById('sharePill');
  const shareBtn = document.getElementById('shareBtn');

  if (screenStream) {
    screenStream.getTracks().forEach(t => t.stop());
    screenStream = null;
    const camTrack = localStream.getVideoTracks()[0];
    const sender = pc.getSenders().find(s => s.track && s.track.kind === 'video');
    if (sender) await sender.replaceTrack(camTrack);
    ws.send(JSON.stringify({ type: 'screen_share_toggle', sharing: false }));
    pill.classList.remove('on');
    shareBtn.textContent = 'Share Screen';
  } else {
    try {
      screenStream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 30 },
        audio: false,
      });
    } catch {
      return;
    }
    const screenTrack = screenStream.getVideoTracks()[0];
    const sender = pc.getSenders().find(s => s.track && s.track.kind === 'video');
    if (sender) await sender.replaceTrack(screenTrack);
    ws.send(JSON.stringify({ type: 'screen_share_toggle', sharing: true }));
    pill.classList.add('on');
    shareBtn.textContent = 'Stop Sharing';

    screenTrack.onended = () => {
      if (screenStream) toggleShare();
    };
  }
}
function startRecording() {
  recordedChunks = [];
  try {
    mediaRecorder = new MediaRecorder(localStream, { mimeType: 'video/webm;codecs=vp8,opus' });
  } catch (err) {
    console.error('MediaRecorder not supported:', err);
    return;
  }
  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) recordedChunks.push(e.data);
  };
  mediaRecorder.start();
}

async function endCall() {
  const endBtn = document.getElementById('endBtn');
  endBtn.disabled = true;
  endBtn.textContent = 'Saving...';

  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    const stopped = new Promise((resolve) => { mediaRecorder.onstop = resolve; });
    mediaRecorder.stop();
    await stopped;

    const blob = new Blob(recordedChunks, { type: 'video/webm' });
    const formData = new FormData();
    formData.append('file', blob, `${sessionId}_${myClientId}.webm`);
    formData.append('session_id', sessionId);
    formData.append('role', myClientId);

    try {
      const resp = await fetch('/upload_recording', { method: 'POST', body: formData });
      const result = await resp.json();
      console.log('Recording uploaded:', result);
      appendChat('system', 'Recording saved and sent for processing.');
    } catch (err) {
      console.error('Upload failed:', err);
      appendChat('system', 'Recording upload failed.');
    }
  }

  if (ws) ws.close();
  if (pc) pc.close();
  if (localStream) localStream.getTracks().forEach(t => t.stop());
  document.getElementById('transcriptBtn').style.display = 'inline-block';
  endBtn.textContent = 'Call Ended';
}

function viewTranscript() {
  window.open(`/transcript/${sessionId}`, '_blank');
}

function toggleChat() {
  const panel = document.getElementById('chatPanel');
  panel.classList.toggle('open');
}

function sendChat() {
  const input = document.getElementById('chatInput');
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  appendChat('mine', text, 'You');
  ws.send(JSON.stringify({ type: 'chat', content: text }));
  input.value = '';
  input.focus();
}

document.getElementById('chatInput').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); sendChat(); }
});
</script>
</body>
</html>
"""


TRANSCRIPT_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>TutorMatch - Transcript</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
""" + SHARED_CSS + """
.card.wide { max-width: 900px; }
.moment {
  border-left: 3px solid var(--green);
  padding: 10px 14px;
  margin-bottom: 10px;
  background: rgba(255,255,255,0.04);
  border-radius: 8px;
}
.moment .t { color: var(--green); font-weight: 600; margin-right: 8px; }
.segment { padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
.segment .t { color: #9fd; opacity: 0.8; margin-right: 8px; font-variant-numeric: tabular-nums; }
#status { opacity: 0.8; margin-bottom: 16px; }
</style>
</head>
<body>
<div class="card wide">
  <h2>Session Transcript</h2>
  <div id="status">Loading...</div>
  <div id="notesSection" style="display:none;">
    <h3>Highlights</h3>
    <div id="moments"></div>
  </div>
  <div id="transcriptSection" style="display:none;">
    <h3>Full Transcript</h3>
    <div id="segments"></div>
  </div>
</div>

<script>
function fmt(ms) {
  const total = Math.floor((ms || 0) / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

async function load() {
  const sessionId = window.location.pathname.split('/').pop();
  const statusEl = document.getElementById('status');
  try {
    const resp = await fetch(`/api/transcript/${sessionId}`);
    const data = await resp.json();

    if (!data.ready) {
      statusEl.textContent = 'Transcript is still processing - check back in a moment.';
      setTimeout(load, 4000);
      return;
    }

    statusEl.style.display = 'none';

    const moments = (data.notes && data.notes.key_moments) || [];
    if (moments.length) {
      document.getElementById('notesSection').style.display = 'block';
      document.getElementById('moments').innerHTML = moments.map(m => `
        <div class="moment"><span class="t">${fmt(m.tMs)}</span><strong>${m.title || ''}</strong><div>${m.why || ''}</div></div>
      `).join('');
    }

    const segments = data.segments || [];
    if (segments.length) {
      document.getElementById('transcriptSection').style.display = 'block';
      document.getElementById('segments').innerHTML = segments.map(s => `
        <div class="segment"><span class="t">${fmt(s.start_ms)}</span>${s.text || ''}</div>
      `).join('');
    }

    if (!moments.length && !segments.length) {
      statusEl.style.display = 'block';
      statusEl.textContent = 'No transcript found for this session.';
    }
  } catch (err) {
    console.error(err);
    statusEl.textContent = 'Could not load transcript.';
  }
}

load();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))