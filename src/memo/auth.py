import secrets
import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from memo.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


def require_token(
	credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
	settings: Annotated[Settings, Depends(get_settings)],
) -> None:
	"""Gate: may this request reach the API at all? (ADR 0018)"""
	presented = credentials.credentials if credentials else ""
	expected = settings.api_token.get_secret_value()
	if not secrets.compare_digest(presented.encode(), expected.encode()):
		raise HTTPException(
			status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Bearer"}
		)


def get_current_user(settings: Annotated[Settings, Depends(get_settings)]) -> uuid.UUID:
	"""Identity: which user is this request acting as? Never read from the request."""
	return settings.current_user_id


CurrentUser = Annotated[uuid.UUID, Depends(get_current_user)]
