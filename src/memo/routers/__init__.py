from fastapi import APIRouter

from memo.routers import (
	auth,
	captures,
	conversations,
	derivations,
	me,
	messages,
	morning,
	recordings,
	usage,
	voice,
)

# Every route resolves its user through CurrentUser (ADR 0019); only /auth/apple
# and /health are reachable without a session.
v1 = APIRouter(prefix="/v1")
v1.include_router(auth.router)
v1.include_router(captures.router)
v1.include_router(recordings.router)
v1.include_router(derivations.router)
v1.include_router(usage.router)
v1.include_router(conversations.router)
v1.include_router(messages.router)
v1.include_router(morning.router)
v1.include_router(me.router)
v1.include_router(voice.router)
