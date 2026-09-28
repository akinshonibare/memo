from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI

from memo import __version__
from memo.config import Settings, get_settings
from memo.db import make_engine, make_session_factory
from memo.logging import RequestIdMiddleware, configure_logging
from memo.routers import v1
from memo.services.queue import make_queue
from memo.services.storage import S3ObjectStore


def create_app(settings: Settings | None = None) -> FastAPI:
	resolved = settings or get_settings()
	configure_logging(resolved.log_level)

	@asynccontextmanager
	async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
		engine = make_engine(resolved.database_url)
		app.state.engine = engine
		app.state.session_factory = make_session_factory(engine)
		store = S3ObjectStore.from_settings(resolved)
		if resolved.app_env in ("local", "test"):
			# MinIO starts empty; a Railway Bucket already exists.
			await store.ensure_bucket()
		app.state.object_store = store
		queue = await make_queue(resolved)
		app.state.queue = queue
		yield
		await queue.aclose()
		await engine.dispose()

	app = FastAPI(title="memo", version=__version__, lifespan=lifespan)
	app.add_middleware(RequestIdMiddleware)
	if settings is not None:
		app.dependency_overrides[get_settings] = lambda: settings
	app.include_router(v1)

	@app.get("/health")
	async def health(
		settings: Annotated[Settings, Depends(get_settings)],
	) -> dict[str, str]:
		return {"status": "ok", "version": __version__, "app_env": settings.app_env}

	return app


app = create_app()
