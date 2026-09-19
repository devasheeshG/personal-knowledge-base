# Contributing

Thank you for helping improve this personal knowledge-base project.

## Before you start

- Never commit credentials, cookies, downloaded videos, transcripts, metadata, or generated indexes.
- Do not include private Reel URLs, API keys, or personal account data in issues or pull requests.
- Keep changes scoped and preserve the incremental sync behavior.

## Development checks

Install the OpenAI Python SDK and ensure `ffmpeg` is available. Run a bounded sync while testing:

```bash
SYNC_LIMIT=1 SYNC_REPROCESS=1 python3 instagram/sync_instagram_saved_reels.py
python3 -m py_compile instagram/sync_instagram_saved_reels.py
```

Do not run a full-account sync during development unless explicitly required.

## Pull requests

Explain what changed, how it was tested, and whether generated local data was used. Keep personal data out of the patch.
