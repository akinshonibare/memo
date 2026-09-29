from fastapi import APIRouter

from memo.routers import (
	auth,
	captures,
	conversations,
	derivations,
	faith,
	habits,
	health,
	me,
	messages,
	mindmap,
	morning,
	news,
	people,
	push,
	recordings,
	review,
	search,
	updates,
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
v1.include_router(search.router)
v1.include_router(review.router)
v1.include_router(people.router)
v1.include_router(people.mentions_router)
v1.include_router(faith.router)
v1.include_router(habits.router)
v1.include_router(health.router)
v1.include_router(push.router)
v1.include_router(updates.router)
v1.include_router(mindmap.router)
v1.include_router(news.router)
