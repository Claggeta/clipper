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
    videos_dir: Path = Path("data/videos")
    # Ollama's default address, and the model chosen from eval results
    # (ADR 0014, 7c amendment).
    llm_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3.5:9b"
