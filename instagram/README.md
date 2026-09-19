# Sync Instagram Saved Reels

This folder contains a local, incremental knowledge-base sync for saved Instagram Reels.
The sync downloads each Reel as an MP4, creates an English transcript, generates a short summary, and writes useful metadata. The root `Instagram Saved Reels.md` file is rebuilt from the local Reel folders after every sync and groups entries by upload date.

Project-wide requirements and the `ffmpeg` explanation are documented in the [root README](../README.md). Keep the authenticated curl export in this folder; credentials live in the root `.env` and remain private.

## Usage

From the parent directory, run:

```bash
python3 instagram/sync_instagram_saved_reels.py
```

Normal runs sync the complete saved-Reels account. For a bounded test, set `SYNC_LIMIT=10` (or another positive number). Set `SYNC_REPROCESS=1` to re-download and regenerate the selected sample.

## Local layout

Each completed Reel is partitioned by its Instagram upload time:

```text
reels/year=2026/month=09/day=17/<code>/
```

Each Reel folder contains:

```text
summary.md      # short knowledge-base summary and local links
transcript.md   # English transcript
metadata.json   # Instagram link, upload date, metrics, and file names
video.mp4       # downloaded source video
```

Local Reel folders are never deleted by the sync if a Reel later disappears from Instagram.

## Credentials and personal data

`instagram.curl`, `.env`, downloaded videos, transcripts, metadata, and generated indexes are ignored by Git. Do not commit credentials or personal Reel data to a public repository.
