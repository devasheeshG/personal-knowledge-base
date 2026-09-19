# Personal Knowledge Base

This repository is a personal knowledge-management project. The first supported service is Instagram Saved Reels, with additional services planned later.

## Current scope

The [`instagram/`](instagram/) folder contains the current Instagram sync implementation. It downloads saved Reels, stores local MP4s, produces English transcripts, generates concise summaries, records metadata, and maintains Markdown indexes grouped by upload date.

## Future contributions

Contributions are welcome. Future work may include a web UI that visualizes saved content, automatically clusters it by topic, and presents a knowledge-management view or mind map for finding relevant information at the right time. A retrieval-augmented generation (RAG) layer over transcripts, summaries, and metadata could provide semantic search and contextual answers. Additional service integrations can follow the same pattern.

Please see [`CONTRIBUTING.md`](CONTRIBUTING.md) for contribution guidelines.

## Instagram setup

The Instagram sync requires:

- Python 3.10 or newer
- The OpenAI Python SDK: `pip install openai`
- `ffmpeg`, an audio/video command-line tool used to extract a small compressed audio track when an MP4 is too large for the transcription upload limit
- An authenticated `instagram.curl` export at `instagram/instagram.curl`
- A private root `.env` copied from [`.env.template`](.env.template)

The environment file accepts `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, and `OPENAI_ASR_MODEL`. Every transcript is translated to English before it is saved.

Run a normal full-account sync with:

```bash
python3 instagram/sync_instagram_saved_reels.py
```

Use `SYNC_LIMIT=10` (or another positive number) for a bounded test. Generated videos, transcripts, metadata, indexes, cookies, and keys are intentionally ignored by Git.
