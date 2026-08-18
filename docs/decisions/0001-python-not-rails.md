# 0001 — Python, not Rails or Node

**Status:** provisional — but unlikely to move
**Date:** 2026-08-17

## Problem

memo is a CRUD app wrapped around a lot of model inference: speech-to-text, embeddings,
LLM structured extraction, vector search. The CRUD half is trivial in any language. The
inference half is not portable.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Rails** | Fastest path to CRUD. Migrations, ActiveRecord, jobs, admin — all decided for you. Akin already knows it. | The AI ecosystem in Ruby is thin wrappers over HTTP APIs. There is no Ruby `faster-whisper`, no `sentence-transformers`, no local inference story. Every "run a model yourself" moment becomes a shell-out to Python anyway. |
| **Node / TypeScript** | Excellent streaming primitives, one language across API and Expo app, Vercel AI SDK is genuinely good. | Same wall: local model inference is Python-first. `onnxruntime-node` exists but the ecosystem, the model cards, the eval tooling, and every research repo assume Python. You'd be translating constantly. |
| **Python** | Where the dependencies live. faster-whisper (CTranslate2), sentence-transformers, every eval framework, every provider SDK first-class, pgvector clients, notebook-based experimentation. | Async Python is less paved than Rails. No conventions handed to you — you assemble FastAPI + SQLAlchemy + Alembic + arq yourself, and the pieces don't know about each other. Packaging and env management are worse than `bundle install`. |

## Decision

Python — because the hard half of this app (inference, retrieval, evals) has its
dependencies in Python, and the easy half (CRUD) is easy everywhere.

## What we're accepting

We are giving up Rails' productivity floor. There is no generator, no admin, no
convention. Expect to spend real evenings on plumbing that Rails would have handed over —
session lifecycle, migration wiring, worker bootstrapping. For this project that's a
**feature** (it's the learning), but on a deadline-driven product it would be a real cost
and we should say so honestly rather than pretending Python is strictly better.

## Revisit when

Never, for this repo. If a future project is CRUD-dominant with only hosted-API AI calls
(no local inference, no embeddings pipeline), Rails or Node is the better answer and this
ADR shouldn't be cited as precedent.

## Interview angle

> **"Why Python for this and not the language your team already uses?"**

Choose the language your *dependencies* live in, not the one you like. The decision hinges
on one question: are you calling hosted APIs over HTTP, or are you running models? If it's
purely HTTP, language is a free choice and you should pick your team's. The moment you
self-host inference, do embedding pipelines, or want the eval and experimentation tooling,
Python stops being a preference and becomes the substrate. Naming that boundary — rather
than saying "Python is the AI language" — is the answer that lands.
