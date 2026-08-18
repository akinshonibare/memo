# 0003 — FastAPI, not Django or Flask

**Status:** provisional
**Date:** 2026-08-17

## Problem

The API does three things: accept a capture and return immediately, stream an LLM answer
token-by-token, and serve read queries. Two of those three are I/O-bound with long-lived
connections. It also needs to validate LLM structured output against a schema, which is the
same validation problem as request validation.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Django + DRF** | Batteries: ORM, migrations, auth, and an **admin** that would genuinely be useful for eyeballing captures and derivations early on. Huge ecosystem, very hard to get wrong. | Async is retrofitted. The ORM's async API is a wrapper with real sharp edges, DRF is sync-first, and long-lived SSE streams fight the request/response model. You'd be running the whole framework against its grain for the two hardest endpoints. |
| **Flask / Quart** | Minimal, unopinionated, easy to reason about. Quart is the async fork. | You assemble everything anyway (validation, serialization, DI, OpenAPI) — same assembly cost as FastAPI, less benefit. Quart's community is a fraction of FastAPI's. |
| **Litestar** | Arguably a cleaner design than FastAPI: better DI, first-class SQLAlchemy integration, less magic in dependency resolution. Async-native. | Much smaller community. Fewer answers when stuck, fewer examples, less interview currency. |
| **FastAPI** | Async-native (ASGI) — SSE streaming and fan-out to slow LLM APIs are the happy path, not the exception. **Pydantic is the same validation layer for request bodies and for LLM structured output**, which is a real conceptual saving. Dependency injection for session/config. OpenAPI schema free, which the Expo client can consume. | No ORM, no migrations, no auth, no admin — you wire SQLAlchemy + Alembic yourself and they don't know FastAPI exists. Dependency injection is implicit and can get tangled. No admin means building your own review UI or living in `psql` early on. |

## Decision

FastAPI, because the two endpoints that matter (streamed answers, immediate-return
capture) are exactly what an async framework is for, and because Pydantic gives us one
validation model for both HTTP input and LLM output.

## What we're accepting

No admin. Early on, inspecting captures and derivations means `psql` or a throwaway
endpoint — a real friction cost in the first weeks that Django would have removed for free.
We also accept assembling and owning the SQLAlchemy session lifecycle, which is the single
most common source of async-Python bugs. Nobody hands us a correct one.

## Revisit when

Not for memo. Worth reconsidering for a project where an admin panel is a product
requirement and streaming isn't — Django wins there, decisively.

## Interview angle

> **"Why async? Isn't it more complexity for no gain?"**

Async pays off exactly when you're I/O-bound with high concurrency per process — which is
what an LLM app is: a request spends 2–20 seconds waiting on a network call doing nothing.
A sync worker holds an OS thread for that whole time; an async one holds a coroutine and
yields. The trap to name: async gives you *nothing* if you make one blocking call inside a
handler — a sync DB driver, `requests`, `time.sleep`, a CPU-bound loop — because it blocks
the event loop and stalls *every* concurrent request in that process, not just yours. So
"we use FastAPI" is not the answer; "we're async end to end, and here's how we keep
blocking calls out of the loop" is.
