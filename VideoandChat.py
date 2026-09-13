import json
import os
from typing import Dict, List
from urllib.parse import quote, urlparse
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi import Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path
from fastapi import UploadFile, File, Form
from Transcriber.Transcribe import run_pipeline
from tutormatch import (
    save_pipeline_result,
    get_transcript,
    get_session_notes,
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


# The call page is served by the website but uploads its recording here. Browsers
# block that cross-address upload unless this app allows the website's origin.
# Websockets aren't subject to this, so the live chat and call need no entry.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_site_url()],
    allow_methods=["POST"],
    allow_headers=["*"],
)


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

# The home, chat and call pages now live on the website, which links two real
# people into a shared room. Anyone arriving at the old addresses goes there.
@app.get("/")
@app.get("/precall")
@app.get("/call")
async def moved_to_site():
    return RedirectResponse(_site_url())


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