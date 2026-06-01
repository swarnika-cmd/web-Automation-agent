"""
Dashboard Server — FastAPI server with WebSockets for live status updates.

Streams agent event logs in real-time, displays agent thinking process, and serves
the current browser screenshot. Runs on http://localhost:8765.
"""

import asyncio
import io
import threading
from pathlib import Path
from typing import Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks
from fastapi.responses import HTMLResponse, Response
from PIL import Image
import os

from event_log import EventLog

app = FastAPI()

# Global state
current_event_log: Optional[EventLog] = None
connected_websockets: Set[WebSocket] = set()
loop: Optional[asyncio.AbstractEventLoop] = None
_server_thread: Optional[threading.Thread] = None

# A cached placeholder image to display before browser screenshot is available.
_placeholder_bytes: Optional[bytes] = None


def get_placeholder_bytes() -> bytes:
    """Generate a clean dark-themed placeholder image."""
    global _placeholder_bytes
    if _placeholder_bytes is not None:
        return _placeholder_bytes

    img = Image.new("RGB", (1024, 768), color=(17, 24, 39))
    # Draw simple text in the middle
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    text = "Waiting for browser to launch..."
    # Simple bounding box / text positioning (using default font)
    # Default font is very small, but it's better than nothing.
    draw.text((380, 360), text, fill=(156, 163, 175))
    
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=60)
    _placeholder_bytes = buffer.getvalue()
    return _placeholder_bytes


@app.on_event("startup")
async def startup_event():
    """Capture the running event loop on startup."""
    global loop
    loop = asyncio.get_running_loop()


@app.get("/")
def get_dashboard():
    """Serve the dashboard HTML page."""
    html_path = Path(__file__).parent / "static" / "dashboard.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Dashboard HTML template not found.</h1>", status_code=404)


@app.get("/screenshot")
def get_screenshot():
    """Serve the latest browser screenshot as JPEG."""
    from browser_manager import browser_manager
    screenshot_bytes = browser_manager.get_latest_screenshot_bytes()
    if screenshot_bytes:
        return Response(content=screenshot_bytes, media_type="image/jpeg")
    return Response(content=get_placeholder_bytes(), media_type="image/jpeg")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Manage live agent event log streaming."""
    await websocket.accept()
    connected_websockets.add(websocket)
    try:
        # If there's an active event log, send it immediately as initialization data
        if current_event_log:
            await websocket.send_json({
                "type": "init",
                "task_description": current_event_log.task_description,
                "status": current_event_log.status,
                "start_time": current_event_log.start_time,
                "events": [event.to_dict() for event in current_event_log.events]
            })
        while True:
            # Keep-alive loop, receive and ignore client messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        connected_websockets.discard(websocket)


def broadcast_event(event_msg: dict) -> None:
    """
    Broadcast a message to all connected WebSockets.
    Can be safely called from other threads.
    """
    if loop and connected_websockets:
        asyncio.run_coroutine_threadsafe(_send_to_all(event_msg), loop)


async def _send_to_all(event_msg: dict) -> None:
    """Coroutine to execute the websocket send calls."""
    for ws in list(connected_websockets):
        try:
            await ws.send_json(event_msg)
        except Exception:
            connected_websockets.discard(ws)


# Task execution lock
task_execution_running = False

def execute_agent_task(task_desc: str):
    global task_execution_running
    try:
        from agent import run_agent_loop, create_backend, GeminiBackend
        
        # Initialize backends
        backend = create_backend()
        gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()
        gemini_backend = None
        if gemini_key and not gemini_key.startswith("your_"):
            try:
                gemini_backend = GeminiBackend(gemini_key)
            except Exception as e:
                print(f"Failed to initialize Gemini backend: {e}")
                
        chat_history = []
        run_agent_loop(task_desc, chat_history, backend, gemini_backend)
    except Exception as e:
        print(f"Error running agent loop: {e}")
        broadcast_event({"type": "status", "status": "failed"})
    finally:
        task_execution_running = False

@app.post("/run")
def trigger_task(payload: dict, background_tasks: BackgroundTasks):
    global task_execution_running
    from browser_manager import browser_manager
    
    if task_execution_running:
        return {"status": "error", "message": "Another task is already running. Please wait."}
        
    task_desc = payload.get("task", "").strip()
    if not task_desc:
        return {"status": "error", "message": "Task description cannot be empty."}
        
    # Reset browser for a clean run
    if browser_manager.is_open():
        browser_manager.close()
        
    task_execution_running = True
    background_tasks.add_task(execute_agent_task, task_desc)
    return {"status": "success", "message": f"Task started: '{task_desc}'"}


def run_server() -> None:
    """Run the FastAPI application with Uvicorn."""
    import uvicorn
    # Read port dynamically for Render (defaults to 8765)
    port = int(os.environ.get("PORT", 8765))
    # Bind to 0.0.0.0 to receive traffic in cloud env
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def start_dashboard() -> None:
    """Launch the dashboard FastAPI server in a background thread."""
    global _server_thread
    if _server_thread is not None:
        return

    _server_thread = threading.Thread(target=run_server, daemon=True)
    _server_thread.start()


if __name__ == "__main__":
    run_server()
