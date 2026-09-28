import logging
import re
import sys
import time

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from memo.ids import uuid7

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def configure_logging(level: str = "INFO") -> None:
	shared: list[structlog.typing.Processor] = [
		structlog.contextvars.merge_contextvars,
		structlog.processors.add_log_level,
		structlog.processors.TimeStamper(fmt="iso", utc=True),
	]
	structlog.configure(
		processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
		logger_factory=structlog.stdlib.LoggerFactory(),
		wrapper_class=structlog.stdlib.BoundLogger,
		cache_logger_on_first_use=True,
	)

	# Stdlib loggers (uvicorn, sqlalchemy) go through the same renderer, so every line
	# is JSON and carries the request id bound in contextvars.
	formatter = structlog.stdlib.ProcessorFormatter(
		foreign_pre_chain=shared,
		processors=[
			structlog.stdlib.ProcessorFormatter.remove_processors_meta,
			structlog.processors.format_exc_info,
			structlog.processors.JSONRenderer(),
		],
	)
	handler = logging.StreamHandler(sys.stdout)
	handler.setFormatter(formatter)
	root = logging.getLogger()
	root.handlers = [handler]
	root.setLevel(level.upper())
	for name in ("uvicorn", "uvicorn.error"):
		logging.getLogger(name).handlers = []
		logging.getLogger(name).propagate = True
	# The middleware below emits one structured access line per request instead.
	logging.getLogger("uvicorn.access").disabled = True


class RequestIdMiddleware:
	"""Bind a request id into structlog's contextvars for the life of the request."""

	def __init__(self, app: ASGIApp) -> None:
		self.app = app

	async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
		if scope["type"] != "http":
			await self.app(scope, receive, send)
			return

		incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
		request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid7().hex
		status_code = 0

		async def send_with_id(message: Message) -> None:
			nonlocal status_code
			if message["type"] == "http.response.start":
				status_code = message["status"]
				message.setdefault("headers", []).append(
					(b"x-request-id", request_id.encode())
				)
			await send(message)

		structlog.contextvars.clear_contextvars()
		structlog.contextvars.bind_contextvars(request_id=request_id)
		started = time.perf_counter()
		try:
			await self.app(scope, receive, send_with_id)
		finally:
			structlog.get_logger("memo.access").info(
				"request",
				method=scope["method"],
				path=scope["path"],
				status=status_code,
				duration_ms=round((time.perf_counter() - started) * 1000, 1),
			)
			structlog.contextvars.clear_contextvars()
