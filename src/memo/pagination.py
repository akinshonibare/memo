"""Keyset cursors over (created_at DESC, id DESC). Offset pagination over a table with
constant inserts skips and duplicates rows as the user scrolls; a keyset does not."""

import base64
import binascii
import datetime as dt
import uuid

from fastapi import HTTPException, status


def encode_cursor(created_at: dt.datetime, row_id: uuid.UUID) -> str:
	raw = f"{created_at.isoformat()}|{row_id}".encode()
	return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
	try:
		created_at, row_id = (
			base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
		)
		return dt.datetime.fromisoformat(created_at), uuid.UUID(row_id)
	except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
		raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc
