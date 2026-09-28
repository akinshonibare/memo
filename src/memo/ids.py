import uuid

import uuid6


def uuid7() -> uuid.UUID:
	# Re-wrap as the stdlib class for drivers that check `type(x) is uuid.UUID`.
	return uuid.UUID(bytes=uuid6.uuid7().bytes)
