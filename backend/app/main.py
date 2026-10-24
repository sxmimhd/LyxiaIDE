from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agent, edit, health, index, llm, repo, tools
from app.config import get_settings
from app.indexing.store import close_store

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    close_store()  # release the vector database lock cleanly


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

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
app.include_router(edit.router)
app.include_router(tools.router)
app.include_router(agent.router)