from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings. Override with CLIPPER_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="CLIPPER_")

    whisper_model: str = "base"
    top_n: int = 5
    cache_dir: Path = Path("data/cache")
    output_dir: Path = Path("data/output")
    clips_dir: Path = Path("data/clips")
    labels_dir: Path = Path("data/labels")
