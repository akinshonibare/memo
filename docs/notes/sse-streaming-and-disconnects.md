# SSE vs WebSocket vs long-poll, and the client that walks away

> **DRAFT written by Claude for issue #36 (curriculum Phase 5).** Rewrite in your own
> words on the learning pass.

## In one sentence

For one-way token streaming SSE is the right transport — plain HTTP, resumable by
design, `EventSource` in every browser — and the part that actually costs money is not
the stream, it's forgetting to cancel the model call when the client disappears.

## What problem does it solve / what did people do before it

An answer takes seconds to generate. Making the user wait for the whole thing feels
broken; the fix is to send tokens as they arrive. Three ways:

- **Long-polling**: the client asks, the server holds the request until there is
  something, the client asks again. Simple, but a request per chunk and awkward
  ordering.
- **WebSocket**: a full-duplex socket. Right when both sides talk continuously — a
  chat with typing indicators, a game. Costs a protocol upgrade, its own auth story,
  and proxies and load balancers that need to understand it.
- **Server-Sent Events**: a normal HTTP response that never ends, `text/event-stream`,
  `event:`/`data:` lines. One direction, server to client, which is exactly the shape of
  "here are tokens". Browsers reconnect automatically; it goes through every proxy that
  handles HTTP.

memo's ask is *create-then-stream* (design doc, Step 3): `POST` creates the assistant
message row, then `GET /v1/messages/{id}/stream` streams it. Two reasons that beat one
`POST` that returns a stream: `EventSource` is GET-only, and the row existing first is
what makes the stream *resumable* — reconnect to the same URL and a finished answer is
replayed from the database rather than re-generated and re-billed.

The event order is the design's most important detail: `retrieval` (the citations) is
sent **before** the first `token`. Citations are chosen by the system from the
retrieval result, so they can be emitted the moment retrieval finishes, and the model is
never asked to invent one.

## The thing I got wrong first

Treating a dropped connection as the client's problem. When the phone goes into a
tunnel mid-answer, the server-side generator is cancelled — but the request to the model
provider keeps producing tokens nobody will read unless something closes it. In memo
the provider stream is opened under `aclosing(...)` inside the generator, so
cancellation closes the upstream HTTP stream too; the partial text is persisted with a
visible `failed` status and the reason "client disconnected after N tokens". Without
that one line, every tunnel is a full-price answer to nobody.

The second one: opening the database session as a FastAPI dependency for a streaming
route. The dependency's exit can run before the streaming body is done with it. The
stream route opens its own session inside the generator.

## How I'd explain it to someone in an interview (30 seconds, out loud)

SSE for token streaming: it's one-way, which is the shape of the problem, it's plain
HTTP so every proxy and load balancer already handles it, and `EventSource` reconnects
for free. WebSockets earn their upgrade dance only when the client also streams. I
split ask into create-then-stream so the answer row exists before generation — that's
what makes reconnecting replay instead of re-pay — and I send the citations as the
first event, before any token, because they're chosen by retrieval, not by the model.
The thing I'd volunteer: on client disconnect you must cancel the upstream model call,
or you're paying for tokens nobody reads; I close the provider stream on cancellation
and persist the partial answer as a visible failure.
