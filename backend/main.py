import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.router import api_router
from api.websocket import ws_router
from services.resolve_service import resolve_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: try initial connection to Resolve
    resolve_service.try_connect()

    # Start background polling for Resolve state changes
    poll_task = asyncio.create_task(resolve_service.poll_loop())

    yield

    # Shutdown
    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="FinlayCut Backend",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}
