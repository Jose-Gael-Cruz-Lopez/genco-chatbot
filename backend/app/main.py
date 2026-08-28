from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.config import get_settings

settings = get_settings()
app = FastAPI(title="Genco Intel Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


from app.chat.router import router as chat_router
app.include_router(chat_router)

from app.agent.router import router as agent_router
app.include_router(agent_router)

from app.live.router import router as live_router
app.include_router(live_router)

_widget_dir = Path(__file__).resolve().parent.parent.parent / "widget" / "dist"
if _widget_dir.exists():
    app.mount("/widget", StaticFiles(directory=str(_widget_dir)), name="widget")

# The portal is a single self-contained file, served at /agent so the session
# cookie's Path=/agent covers the page and its API calls alike.
_portal_file = (Path(__file__).resolve().parent.parent.parent
                / "portal" / "dist" / "portal.html")


@app.get("/agent", include_in_schema=False)
def agent_portal() -> FileResponse:
    return FileResponse(str(_portal_file), media_type="text/html")
