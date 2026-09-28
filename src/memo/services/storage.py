from typing import Any, Protocol

import aioboto3
from botocore.config import Config
from botocore.exceptions import ClientError

from memo.config import Settings

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


class ObjectStore(Protocol):
	async def presign_put(
		self, key: str, content_type: str, ttl_seconds: int
	) -> str: ...

	async def exists(self, key: str) -> bool: ...

	async def get(self, key: str) -> bytes: ...

	async def delete(self, key: str) -> None: ...


class S3ObjectStore:
	"""Any S3-compatible bucket: a Railway Bucket in prod, MinIO in compose."""

	def __init__(
		self,
		*,
		endpoint: str,
		bucket: str,
		access_key_id: str,
		secret_access_key: str,
		region: str = "auto",
		addressing_style: str = "auto",
	) -> None:
		self.bucket = bucket
		self._session = aioboto3.Session(
			aws_access_key_id=access_key_id,
			aws_secret_access_key=secret_access_key,
			region_name=region,
		)
		self._client_kwargs: dict[str, Any] = {
			"endpoint_url": endpoint,
			"config": Config(
				signature_version="s3v4", s3={"addressing_style": addressing_style}
			),
		}

	@classmethod
	def from_settings(cls, settings: Settings) -> "S3ObjectStore":
		return cls(
			endpoint=settings.s3_endpoint,
			bucket=settings.s3_bucket,
			access_key_id=settings.s3_access_key_id.get_secret_value(),
			secret_access_key=settings.s3_secret_access_key.get_secret_value(),
			region=settings.s3_region,
			addressing_style=settings.s3_addressing_style,
		)

	def _client(self) -> Any:
		return self._session.client("s3", **self._client_kwargs)

	async def presign_put(self, key: str, content_type: str, ttl_seconds: int) -> str:
		async with self._client() as s3:
			url: str = await s3.generate_presigned_url(
				"put_object",
				Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
				ExpiresIn=ttl_seconds,
			)
			return url

	async def exists(self, key: str) -> bool:
		async with self._client() as s3:
			try:
				await s3.head_object(Bucket=self.bucket, Key=key)
			except ClientError as exc:
				if exc.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES:
					return False
				raise
			return True

	async def get(self, key: str) -> bytes:
		async with self._client() as s3:
			obj = await s3.get_object(Bucket=self.bucket, Key=key)
			async with obj["Body"] as body:
				data: bytes = await body.read()
				return data

	async def delete(self, key: str) -> None:
		async with self._client() as s3:
			await s3.delete_object(Bucket=self.bucket, Key=key)

	async def ensure_bucket(self) -> None:
		"""Dev/test only: MinIO starts empty. A Railway Bucket already exists."""
		async with self._client() as s3:
			try:
				await s3.head_bucket(Bucket=self.bucket)
			except ClientError as exc:
				if exc.response.get("Error", {}).get("Code") not in _NOT_FOUND_CODES:
					raise
				await s3.create_bucket(Bucket=self.bucket)
