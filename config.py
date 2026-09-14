import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    allowed_user_ids: set[int]


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "BOT_TOKEN is not set. Copy .env.example to .env and add the BotFather token."
        )
    raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    allowed = {int(x.strip()) for x in raw.split(",") if x.strip()}
    return Config(token, allowed)