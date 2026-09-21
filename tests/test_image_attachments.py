"""Image attachment prompt helpers."""

from telegram_cursor_agent.services.image_attachments import (
    StoredImage,
    build_prompt_with_images,
)


def test_build_prompt_with_images_includes_paths() -> None:
    prompt = build_prompt_with_images(
        "сделай лендинг в этом стиле",
        [
            StoredImage(
                path="/data/uploads/abc_photo.jpg",
                filename="photo.jpg",
                mime_type="image/jpeg",
            )
        ],
    )
    assert "/data/uploads/abc_photo.jpg" in prompt
    assert "сделай лендинг в этом стиле" in prompt


def test_build_prompt_maps_container_paths_for_host_worker(test_settings) -> None:
    settings = test_settings.model_copy(
        update={
            "upload_storage_path": "/data/uploads",
            "upload_host_path": "/root/telegram-cursor-agent/data/uploads",
        }
    )
    prompt = build_prompt_with_images(
        "баг снова",
        [
            StoredImage(
                path="/data/uploads/abc_photo.jpg",
                filename="photo.jpg",
                mime_type="image/jpeg",
            )
        ],
        settings=settings,
    )
    assert "`/root/telegram-cursor-agent/data/uploads/abc_photo.jpg`" in prompt
    assert "`/data/uploads/abc_photo.jpg`" not in prompt


def test_build_prompt_without_user_text() -> None:
    prompt = build_prompt_with_images(
        "",
        [
            StoredImage(
                path="/data/uploads/abc_photo.jpg",
                filename="photo.jpg",
                mime_type="image/jpeg",
            )
        ],
    )
    assert "Опиши изображение" in prompt
