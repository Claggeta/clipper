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
    # Ollama's default address. The model is unset until one is chosen from
    # eval results (ADR 0014).
    llm_url: str = "http://127.0.0.1:11434"
    llm_model: str | None = None
