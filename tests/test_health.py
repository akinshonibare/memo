from httpx import AsyncClient


async def test_health_is_open_and_reports_env(client: AsyncClient) -> None:
	response = await client.get("/health", headers={"Authorization": ""})

	assert response.status_code == 200
	assert response.json()["app_env"] == "test"
	assert response.headers["x-request-id"]
