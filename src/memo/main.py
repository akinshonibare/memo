from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from memo import __version__
from memo.config import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
	get_settings()
	yield


app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, str]:
	return {
		"status": "ok",
		"version": __version__,
		"app_env": settings.app_env,
	}
