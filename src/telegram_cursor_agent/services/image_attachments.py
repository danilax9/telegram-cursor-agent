"""Image attachment helpers for Telegram → Cursor agent prompts."""

import json
import uuid
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.uploads import agent_visible_upload_path

PENDING_IMAGES_KEY_PREFIX = "tca:pending_images:"
PENDING_IMAGES_TTL_SECONDS = 1800


@dataclass(frozen=True)
class StoredImage:
    path: str
    filename: str
    mime_type: str | None = None


def build_prompt_with_images(
    user_text: str,
    images: list[StoredImage],
    *,
    settings: Settings | None = None,
) -> str:
    """Turn user text plus stored image paths into an agent prompt."""
    lines = [
        "Пользователь прислал изображение через Telegram.",
        "Открой и проанализируй файл(ы) по абсолютному пути:",
    ]
    for image in images:
        mime = image.mime_type or "unknown"
        path = (
            agent_visible_upload_path(image.path, settings)
            if settings is not None
            else image.path
        )
        lines.append(f"- `{path}` ({image.filename}, {mime})")
    lines.append("")
    text = user_text.strip()
    if text:
        lines.append(f"Запрос: {text}")
    else:
        lines.append("Опиши изображение и уточни, что сделать дальше.")
    return "\n".join(lines)


class PendingImageStore:
    """Redis-backed pending images waiting for a follow-up text message."""

    def __init__(self, redis_client: Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client

    def _key(self, user_id: uuid.UUID) -> str:
        return f"{PENDING_IMAGES_KEY_PREFIX}{user_id}"

    async def add(self, user_id: uuid.UUID, image: StoredImage) -> None:
        key = self._key(user_id)
        existing_raw = await self._redis.get(key)
        images: list[dict[str, str | None]] = []
        if existing_raw:
            images = json.loads(existing_raw)
        images.append(asdict(image))
        await self._redis.set(key, json.dumps(images), ex=PENDING_IMAGES_TTL_SECONDS)

    async def peek(self, user_id: uuid.UUID) -> list[StoredImage]:
        raw = await self._redis.get(self._key(user_id))
        if not raw:
            return []
        payload = json.loads(raw)
        return [
            StoredImage(
                path=str(item["path"]),
                filename=str(item["filename"]),
                mime_type=item.get("mime_type"),
            )
            for item in payload
        ]

    async def get_and_clear(self, user_id: uuid.UUID) -> list[StoredImage]:
        images = await self.peek(user_id)
        if images:
            await self._redis.delete(self._key(user_id))
        return images
