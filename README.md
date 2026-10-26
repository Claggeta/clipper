# Clipper

AI-assisted tool that finds clip-worthy moments in long-form video.

**Status:** pre-alpha. Version 1 is under development and is not usable yet.

## What it will do (Version 1)

Given a local video file, Clipper will:

1. Extract the audio
2. Transcribe it
3. Identify the top 5 clip-worthy timestamps
4. Display the results and save them as JSON

Rendering clips, captions, uploads, and analytics are planned for later
versions. See [docs/roadmap.md](docs/roadmap.md).

## Documentation

- [Vision](docs/vision.md)
- [Roadmap](docs/roadmap.md)
- [Architecture](docs/architecture.md)
- [Decision records](docs/decisions/)

## Requirements

- Windows, macOS, or Linux
- [Git](https://git-scm.com)
- [uv](https://docs.astral.sh/uv/) (installs Python 3.12+ and dependencies)
- [ffmpeg](https://ffmpeg.org) available on your PATH

## Setup

```powershell
git clone https://github.com/claggeta/clipper.git
cd clipper
uv sync
uv run pre-commit install
```

## Development

Run the same checks that CI runs:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Commits follow [Conventional Commits](https://www.conventionalcommits.org)
(`feat:`, `fix:`, `docs:`, `chore:`). Work happens on short-lived branches
and is merged through pull requests.

## License

To be decided.
