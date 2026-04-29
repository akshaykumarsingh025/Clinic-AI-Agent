import logging
from typing import Optional

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

BOT_URL = settings.WHATSAPP_BOT_URL


async def send_text(phone: str, text: str):
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{BOT_URL}/send/text",
                json={"phone": phone, "text": text},
            )
            response.raise_for_status()
            logger.info(f"Text sent to {phone}")
    except Exception as e:
        logger.error(f"Failed to send text to {phone}: {e}")
        raise


async def send_voice_note(phone: str, audio_path: str):
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{BOT_URL}/send/audio",
                json={"phone": phone, "audio_path": audio_path},
            )
            response.raise_for_status()
            logger.info(f"Voice note sent to {phone}")
    except Exception as e:
        logger.error(f"Failed to send voice note to {phone}: {e}")
        raise


async def send_button_message(phone: str, text: str, buttons: list[str]):
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{BOT_URL}/send/buttons",
                json={"phone": phone, "text": text, "buttons": buttons},
            )
            response.raise_for_status()
            logger.info(f"Button message sent to {phone}")
    except Exception as e:
        logger.error(f"Failed to send button message to {phone}: {e}")
        raise
