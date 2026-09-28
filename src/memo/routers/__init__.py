from fastapi import APIRouter, Depends

from memo.auth import require_token
from memo.routers import (
	captures,
	conversations,
	derivations,
	me,
	messages,
	morning,
	recordings,
	usage,
)

v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])
v1.include_router(captures.router)
v1.include_router(recordings.router)
v1.include_router(derivations.router)
v1.include_router(usage.router)
v1.include_router(conversations.router)
v1.include_router(messages.router)
v1.include_router(morning.router)
v1.include_router(me.router)
