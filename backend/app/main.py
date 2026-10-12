from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.api import health, index, llm, repo

settings = get_settings()

app = FastAPI(title=settings.app_name, version=settings.version)

# The Electron UI is served from another origin (Vite), so the browser
# needs CORS permission to call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(llm.router)
app.include_router(repo.router)
app.include_router(index.router)