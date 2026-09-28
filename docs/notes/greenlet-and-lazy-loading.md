# greenlet, and why lazy-loading explodes under async

> **DRAFT written by Claude for issue #6.** The notes directory is Akin's proof of work —
> rewrite this in your own words on the learning pass, and delete anything you can't say
> out loud without looking.

## In one sentence

SQLAlchemy's ORM is synchronous code that expects to be able to run a query in the middle of
an attribute access; `greenlet` is the trick that lets that synchronous code call an async
driver without being rewritten, and lazy-loading is the one place the trick cannot save you.

## What problem does it solve / what did people do before it

The ORM was written years before `asyncio`. Its internals — the unit of work, relationship
loading, `session.flush()` — are deeply synchronous: they call `connection.execute()` and
expect the result *right there*. Rewriting all of that as `async def` would have meant two
ORMs.

`greenlet` gives a way out. A greenlet is a coroutine-like context that can be *switched
into and out of* without `await`. SQLAlchemy's `AsyncSession.execute()` runs the synchronous
ORM code inside a greenlet; when that code hits the driver, `await_only()` switches *out*,
the real `await` happens on `asyncpg` in the event loop, and the result is switched back
*in*. The synchronous ORM never knew it paused. That is why `greenlet` is a hard dependency
of `sqlalchemy[asyncio]` and why the "sync" ORM works at all under `await`.

Before this, the options were: use a sync ORM in a thread pool (throughput dies, connection
pools get confusing), or use a different, async-first ORM with a fraction of the features.

## The thing I got wrong first

Assuming that because `await session.execute(...)` works, `capture.derivations` would too.

Lazy-loading is an attribute access that runs a query: `capture.derivations` looks at the
instance, sees the relationship isn't loaded, and calls the database *from inside
`__getattr__`*. There is no `await` in an attribute access, so there is no greenlet context
to switch out of — the ORM code is running on the event loop thread directly, not inside
`AsyncSession`'s greenlet. SQLAlchemy raises `MissingGreenlet: greenlet_spawn has not been
called; can't call await_only() here`. The error message is the mechanism, once you can read
it.

The fixes are all "load it while you still have a greenlet": `selectinload()` /
`joinedload()` on the query, `await session.refresh(obj, ["derivations"])`, or
`AsyncAttrs` and `await capture.awaitable_attrs.derivations`. Same rule for
`expire_on_commit`: after a commit, expired attributes reload lazily on next access — so the
session factory sets `expire_on_commit=False`, or the first attribute read after a commit
blows up in the response serialiser.

The other half of the issue — **one session per request, never a global** — is a different
failure. An `AsyncSession` is not thread- or task-safe; two coroutines sharing one session
interleave their statements on one connection and one transaction. The second request's
`SET LOCAL app.user_id` overwrites the first's mid-flight, and now the tenant boundary is
leaking through the thing that was supposed to enforce it. The dependency in `memo.db`
creates a session per request *and* binds the user inside that session's transaction so
the two can't come apart.

## How I'd explain it to someone in an interview (30 seconds, out loud)

SQLAlchemy's ORM is sync code; `greenlet` lets it call an async driver by switching out at
the point where it would block and switching back with the result, so `await
session.execute()` runs the whole synchronous ORM path underneath one `await`. The place
that breaks is lazy-loading: an attribute access that needs a query has no `await` and no
greenlet to switch out of, so you get `MissingGreenlet`. Rule: load relationships eagerly
on the query, or explicitly `await` a refresh — never rely on attribute access to hit the
database. And one session per request, because a session is a single connection and a
single transaction, and sharing it across concurrent requests interleaves their state.
