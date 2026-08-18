import httpx

from memo.config import Settings, get_settings
from memo.main import app


async def test_health_uses_overridden_settings() -> None:
	app.dependency_overrides[get_settings] = lambda: Settings(
		database_url="postgresql://test:test@localhost/test",
		app_env="test",
	)

	try:
		transport = httpx.ASGITransport(app=app)
		async with httpx.AsyncClient(
			transport=transport, base_url="http://test"
		) as client:
			response = await client.get("/health")

		assert response.status_code == 200
		assert response.json()["app_env"] == "test"
	finally:
		app.dependency_overrides.clear()
