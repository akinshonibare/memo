"""Sign in with Apple → a memo session (ADR 0019)."""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memo.auth import BearerToken, CurrentUser
from memo.config import SEED_USER_ID, Settings, get_settings
from memo.db import DbSession, bind_system
from memo.models import User
from memo.routers.me import MeOut
from memo.services import sessions
from memo.services.apple import (
	AppleIdentity,
	IdentityVerifier,
	InvalidIdentityToken,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class AppleLogin(BaseModel):
	identity_token: str = Field(min_length=1)
	# Apple sends the name only on the very first authorisation; the app forwards it.
	full_name: str | None = Field(default=None, max_length=200)
	device_name: str | None = Field(default=None, max_length=120)


class LoginOut(BaseModel):
	token: str
	user: MeOut


def get_identity_verifier(request: Request) -> IdentityVerifier:
	verifier: IdentityVerifier = request.app.state.identity_verifier
	return verifier


async def _user_for(
	session: AsyncSession, identity: AppleIdentity, settings: Settings
) -> User:
	user = await session.scalar(select(User).where(User.apple_sub == identity.sub))
	if user is not None:
		return user
	if settings.claim_seed_user_on_first_login:
		seed = await session.get(User, SEED_USER_ID)
		if seed is not None and seed.apple_sub is None:
			seed.apple_sub = identity.sub
			if seed.email is None:
				seed.email = identity.email
			await session.flush()
			return seed
	user = User(apple_sub=identity.sub, email=identity.email)
	session.add(user)
	await session.flush()
	return user


@router.post("/apple", response_model=LoginOut)
async def login_with_apple(
	body: AppleLogin,
	request: Request,
	settings: Annotated[Settings, Depends(get_settings)],
	verifier: Annotated[IdentityVerifier, Depends(get_identity_verifier)],
) -> LoginOut:
	try:
		identity = await verifier.verify(body.identity_token)
	except InvalidIdentityToken:
		raise HTTPException(
			status.HTTP_401_UNAUTHORIZED, "Apple identity token was not accepted"
		) from None
	factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
	async with factory() as session:
		await bind_system(session)
		user = await _user_for(session, identity, settings)
		token = await sessions.create(
			session,
			user.id,
			ttl=dt.timedelta(days=settings.session_ttl_days),
			device_name=body.device_name,
		)
		out = LoginOut(token=token, user=MeOut.model_validate(user))
		await session.commit()
	return out


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(session: DbSession, user_id: CurrentUser, token: BearerToken) -> None:
	"""Revokes this session only. Other devices stay signed in."""
	await sessions.revoke(session, token)
