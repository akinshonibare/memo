"""Sign in with Apple: verify the identity token the app hands us (ADR 0019).

Apple signs the token with RS256; the public keys are published at a JWKS URL and
rotate. We check signature, issuer, audience (our bundle id) and expiry, and read
the stable `sub`. Nothing here talks to Apple beyond fetching those keys."""

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
import structlog
from jwt import PyJWKClient

log = structlog.get_logger("memo.apple")

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"


class InvalidIdentityToken(Exception):
	pass


@dataclass(frozen=True)
class AppleIdentity:
	sub: str
	email: str | None


class IdentityVerifier(Protocol):
	async def verify(self, identity_token: str) -> AppleIdentity: ...


class AppleIdentityVerifier:
	def __init__(self, *, bundle_id: str, jwks_url: str = APPLE_JWKS_URL) -> None:
		self._bundle_id = bundle_id
		# Caches the key set; a kid we have not seen triggers one refetch.
		self._jwks = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)

	def _decode(self, identity_token: str) -> dict[str, Any]:
		key = self._jwks.get_signing_key_from_jwt(identity_token)
		claims: dict[str, Any] = jwt.decode(
			identity_token,
			key.key,
			algorithms=["RS256"],
			audience=self._bundle_id,
			issuer=APPLE_ISSUER,
			options={"require": ["exp", "iat", "sub", "aud", "iss"]},
		)
		return claims

	async def verify(self, identity_token: str) -> AppleIdentity:
		try:
			# PyJWKClient fetches with urllib, synchronously; keep it off the loop.
			claims = await asyncio.to_thread(self._decode, identity_token)
		except (jwt.PyJWTError, ValueError) as exc:
			log.info("apple.identity_rejected", reason=type(exc).__name__)
			raise InvalidIdentityToken(str(exc)) from exc
		email = claims.get("email")
		return AppleIdentity(
			sub=str(claims["sub"]), email=str(email) if email else None
		)
