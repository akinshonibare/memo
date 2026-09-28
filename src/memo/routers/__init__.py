from fastapi import APIRouter, Depends

from memo.auth import require_token
from memo.routers import captures

v1 = APIRouter(prefix="/v1", dependencies=[Depends(require_token)])
v1.include_router(captures.router)
