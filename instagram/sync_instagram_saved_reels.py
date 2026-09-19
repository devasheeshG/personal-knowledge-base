#!/usr/bin/env python3
"""Synchronize saved Instagram Reels into a local Markdown knowledge base.

The authenticated Instagram request is read from ``instagram.curl``.
The script never writes that request, its cookies, or a raw API JSON dump.
Set OPENAI_API_KEY and OPENAI_BASE_URL in the project-root ``.env`` for
transcription and summaries.  ``OPENAI_ASR_MODEL`` may be set when the gateway
uses a different ElevenLabs Scribe model name (the default is
``elevenlabs/scribe_v2`` so Bifrost can route it to ElevenLabs).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from openai import OpenAI


GRAPHQL_FRIENDLY_NAME = "PolarisProfileSavedPostsTabContentQuery_connection"
PAGE_SIZE = 12
REQUEST_DELAY_SECONDS = 1.0
INSTAGRAM_API = "https://www.instagram.com/api/v1"
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
SOURCE_CURL = SCRIPT_DIR / "instagram.curl"
REELS_DIR = SCRIPT_DIR / "reels"
INDEX_FILE = SCRIPT_DIR / "Instagram Saved Reels.md"
LONG_INDEX_FILE = SCRIPT_DIR / "Instagram Saved Reels Long.md"


def _request_json(request: Request) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {error.code} from {request.full_url}: {detail}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Expected a JSON object from the remote service")
    return payload


def _safe_slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value or "reel").strip(".-")
    return value[:100] or "reel"


def _markdown_text(value: Any) -> str:
    return str(value or "").replace("\r", "").strip()


def main() -> int:
    # Load the optional local .env without adding a dependency or printing secrets.
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    # Read credentials from the environment; neither key is ever written to disk.
    openai_base = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    asr_model = os.environ.get("OPENAI_ASR_MODEL", "elevenlabs/scribe_v2")
    summary_model = os.environ.get("OPENAI_MODEL", os.environ.get("OPENAI_SUMMARY_MODEL", "Codex Proxy/gpt-5.6-luna"))
    if not openai_base or not openai_key:
        raise RuntimeError("OPENAI_BASE_URL and OPENAI_API_KEY are required for transcription")
    openai_client = OpenAI(api_key=openai_key, base_url=openai_base)
    if not SOURCE_CURL.exists():
        raise FileNotFoundError(f"Missing authenticated curl export: {SOURCE_CURL}")
    # The curl export contains session credentials; keep its local permissions private.
    try:
        SOURCE_CURL.chmod(0o600)
    except OSError as error:
        print(f"Warning: could not restrict curl-file permissions: {error}", file=sys.stderr)
    REELS_DIR.mkdir(exist_ok=True)

    # Fetch every saved-media page from newest to oldest, resetting the captured cursor.
    # This parsing is kept inline because the curl export is consumed only here.
    tokens = shlex.split(SOURCE_CURL.read_text(encoding="utf-8"))
    graphql_url = "https://www.instagram.com/api/graphql"
    cookie = ""
    body = ""
    instagram_headers: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"--url", "-b", "--cookie", "-H", "--header", "--data-raw", "--data"}:
            if i + 1 >= len(tokens):
                raise ValueError(f"Missing value after {token} in curl export")
            value = tokens[i + 1]
            if token == "--url":
                graphql_url = value
            elif token in {"-b", "--cookie"}:
                cookie = value
            elif token in {"-H", "--header"}:
                name, separator, header_value = value.partition(":")
                if separator:
                    instagram_headers[name.strip().lower()] = header_value.strip()
            else:
                body = value
            i += 2
        else:
            i += 1
    if not cookie or not body:
        raise ValueError("The curl export must contain cookies and form data")
    instagram_headers["cookie"] = cookie
    form = {key: values[-1] for key, values in parse_qs(body, keep_blank_values=True).items()}
    variables = json.loads(form.get("variables", "{}"))
    variables = dict(variables)
    variables["after"] = None
    variables["first"] = PAGE_SIZE
    all_items: list[dict[str, Any]] = []
    seen_cursors: set[str] = set()
    pages = 0
    while True:
        form["variables"] = json.dumps(variables, separators=(",", ":"))
        request_headers = dict(instagram_headers)
        request_headers.setdefault("x-fb-friendly-name", GRAPHQL_FRIENDLY_NAME)
        request_headers.setdefault("referer", "https://www.instagram.com/saved/")
        payload = _request_json(Request(graphql_url, data=urlencode(form).encode("utf-8"), headers=request_headers, method="POST"))
        if payload.get("errors"):
            raise RuntimeError("Instagram GraphQL returned an error; refresh the curl export")
        connection = payload.get("data", {}).get("viewer", {}).get("saved_media")
        if not isinstance(connection, dict):
            raise RuntimeError("Instagram response did not contain saved media")
        edges = connection.get("edges") or []
        pages += 1
        for edge in edges:
            node = edge.get("node") if isinstance(edge, dict) else None
            if not isinstance(node, dict):
                continue
            product_type = str(node.get("product_type") or "").lower()
            if product_type not in {"clips", "reels", "reel"} and node.get("media_type") != 2:
                continue
            all_items.append({
                "code": node.get("code") or node.get("shortcode"),
                "media_id": node.get("id") or node.get("pk"),
                "pk": node.get("pk"),
                "username": (node.get("user") or {}).get("username"),
                "caption": (node.get("caption") or {}).get("text") if isinstance(node.get("caption"), dict) else node.get("caption"),
                "product_type": node.get("product_type"),
                "media_type": node.get("media_type"),
                "graphql_cursor": edge.get("cursor"),
            })
        page_info = connection.get("page_info") or {}
        next_cursor = page_info.get("end_cursor") or (edges[-1].get("cursor") if edges else None)
        if not next_cursor or next_cursor in seen_cursors or page_info.get("has_next_page") is False:
            break
        seen_cursors.add(next_cursor)
        variables["after"] = next_cursor
        time.sleep(REQUEST_DELAY_SECONDS)

    # De-duplicate by media ID/code before requesting the richer media-info payload.
    unique_items: dict[str, dict[str, Any]] = {}
    for item in all_items:
        unique_items[str(item.get("code") or item.get("media_id"))] = item

    # Production runs sync the complete account. Set SYNC_LIMIT (for example,
    # 10) explicitly when testing a bounded sample.
    limit_text = os.environ.get("SYNC_LIMIT", "all").strip().lower()
    selected_items = list(unique_items.values()) if limit_text in {"all", "unlimited"} else list(unique_items.values())[: int(limit_text)]
    force_reprocess = os.environ.get("SYNC_REPROCESS", "").strip().lower() in {"1", "true", "yes"}
    synced: list[dict[str, Any]] = []
    skipped = 0
    failed: list[str] = []
    existing_local_by_code: dict[str, Path] = {}
    for existing_metadata_path in REELS_DIR.rglob("metadata.json"):
        try:
            existing_metadata = json.loads(existing_metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if existing_metadata.get("code"):
            existing_local_by_code[str(existing_metadata["code"])] = existing_metadata_path.parent
    for position, item in enumerate(selected_items, start=1):
        code = item.get("code")
        media_id = item.get("pk") or item.get("media_id")
        if not code or not media_id:
            failed.append(str(code or media_id or "unknown"))
            continue
        existing_folder = existing_local_by_code.get(str(code))
        if not force_reprocess and existing_folder:
            video_path = existing_folder / "video.mp4"
            transcript_path = existing_folder / "transcript.md"
            metadata_path = existing_folder / "metadata.json"
            note_path = existing_folder / "summary.md"
        else:
            video_path = transcript_path = metadata_path = note_path = None
        if not force_reprocess and video_path and transcript_path and metadata_path and note_path and video_path.exists() and transcript_path.exists() and metadata_path.exists() and note_path.exists():
            synced.append({"item": item, "folder": existing_folder, "note_path": note_path, "metadata_path": metadata_path, "video_path": video_path, "transcript_path": transcript_path, "summary": "Existing local artifact"})
            skipped += 1
            continue
        try:
            # Instagram's GraphQL connection omits CDN video URLs; media-info supplies them.
            # The mobile REST media-info endpoint rejects the browser UA from the
            # GraphQL export; this compatible Instagram app UA is accepted with
            # the same authenticated cookie.
            media_info = _request_json(Request(f"{INSTAGRAM_API}/media/{media_id}/info/", headers={"cookie": instagram_headers["cookie"], "user-agent": "Instagram 300.0.0.0.0 Android"}, method="GET"))
            media = (media_info.get("items") or [])[0]
            timestamp = media.get("taken_at")
            posted_at = datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if isinstance(timestamp, (int, float)) else None
            if posted_at:
                posted_datetime = datetime.fromisoformat(posted_at)
                folder = REELS_DIR / f"year={posted_datetime:%Y}" / f"month={posted_datetime:%m}" / f"day={posted_datetime:%d}" / _safe_slug(str(code))
            else:
                folder = REELS_DIR / "unknown-upload-date" / _safe_slug(str(code))
            video_path = folder / "video.mp4"
            transcript_path = folder / "transcript.md"
            metadata_path = folder / "metadata.json"
            note_path = folder / "summary.md"
            versions = media.get("video_versions") or []
            if not versions:
                raise RuntimeError("Instagram did not return a downloadable video URL")
            video_url = max(versions, key=lambda version: (version.get("width") or 0, version.get("height") or 0)).get("url")
            if not video_url:
                raise RuntimeError("Instagram returned an empty video URL")
            folder.mkdir(parents=True, exist_ok=True)
            # Download to a temporary file so an interrupted transfer never looks complete.
            temporary_video = folder / ".video.mp4.part"
            with urlopen(Request(video_url, headers={"user-agent": "Mozilla/5.0"}), timeout=120) as response, temporary_video.open("wb") as output:
                shutil.copyfileobj(response, output)
            temporary_video.replace(video_path)

            # Transcription API uploads the MP4 directly when it is <=25 MB; otherwise extract compressed audio.
            transcription_input = video_path
            temporary_audio: Path | None = None
            if video_path.stat().st_size > 24 * 1024 * 1024:
                temporary_audio = Path(tempfile.mkstemp(prefix="instagram-reel-", suffix=".mp3")[1])
                subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-b:a", "64k", str(temporary_audio)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                transcription_input = temporary_audio
            detected_language = ""
            translation_applied = False
            try:
                with transcription_input.open("rb") as audio_file:
                    transcription_response = openai_client.audio.transcriptions.create(model=asr_model, file=audio_file)
                transcript = _markdown_text(getattr(transcription_response, "text", ""))
                detected_language = str(getattr(transcription_response, "language", "") or getattr(transcription_response, "language_code", ""))
                translation_response = openai_client.chat.completions.create(model=summary_model, temperature=0, messages=[{"role": "system", "content": "Translate this transcript into clear, natural English. Preserve every factual detail, name, number, and nuance. Do not summarize, omit content, or add information."}, {"role": "user", "content": transcript}])
                transcript = _markdown_text(translation_response.choices[0].message.content)
                if not transcript:
                    raise RuntimeError("Translation returned an empty result")
                translation_applied = True
            finally:
                if temporary_audio:
                    temporary_audio.unlink(missing_ok=True)
            if not transcript:
                raise RuntimeError("ASR returned an empty transcript")
            transcript_path.write_text(f"# Transcript\n\n{transcript}\n", encoding="utf-8")

            # Generate a short knowledge-base summary from the transcript, with a safe fallback.
            summary_response = openai_client.chat.completions.create(model=summary_model, temperature=0.2, messages=[{"role": "system", "content": "Summarize this Instagram Reel transcript in 2-4 concise sentences for a personal knowledge base. Preserve the important names, numbers, claims, steps, caveats, and calls to action. Do not invent facts or omit the central point."}, {"role": "user", "content": transcript}])
            summary = _markdown_text(summary_response.choices[0].message.content)
            if not summary:
                raise RuntimeError("Summary returned an empty result")

            metadata = {"instagram_url": f"https://www.instagram.com/reel/{code}/", "code": code, "username": (media.get("user") or {}).get("username") or item.get("username"), "posted_at": posted_at, "synced_at": datetime.now(timezone.utc).isoformat(), "caption": (media.get("caption") or {}).get("text") if isinstance(media.get("caption"), dict) else item.get("caption"), "like_count": media.get("like_count"), "comment_count": media.get("comment_count"), "view_count": media.get("view_count") or media.get("play_count") or media.get("ig_play_count"), "video_duration_seconds": media.get("video_duration"), "video_width": media.get("original_width"), "video_height": media.get("original_height"), "transcription_model": asr_model, "detected_language": detected_language or "unknown", "translation_applied": translation_applied, "transcript_language": "en", "summary_model": summary_model, "files": {"video": "video.mp4", "transcript": "transcript.md", "metadata": "metadata.json"}}
            metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            note_path.write_text(f"# Reel by @{metadata['username'] or 'unknown'}\n\n{summary}\n\n## Files\n\n- [Video](video.mp4)\n- [Transcript](transcript.md)\n- [Metadata](metadata.json)\n", encoding="utf-8")
            synced.append({"item": item, "folder": folder, "note_path": note_path, "metadata_path": metadata_path, "video_path": video_path, "transcript_path": transcript_path, "summary": summary})
            print(f"[{position}/{len(selected_items)}] synced {code}")
        except Exception as error:
            failed.append(str(code))
            print(f"[{position}/{len(selected_items)}] failed {code}: {error}", file=sys.stderr)

    # Rebuild the root index from every complete local folder, not just this run.
    # This keeps prior syncs visible and prevents duplicate rows if Instagram returns
    # the same saved Reel more than once.
    local_by_identity: dict[str, dict[str, Any]] = {}
    for metadata_path in REELS_DIR.rglob("metadata.json"):
        folder = metadata_path.parent
        video_path = folder / "video.mp4"
        transcript_path = folder / "transcript.md"
        note_path = folder / "summary.md"
        if not (video_path.exists() and transcript_path.exists() and note_path.exists()):
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        identity = str(metadata.get("code") or metadata.get("instagram_url") or folder.name)
        candidate = {"metadata": metadata, "metadata_path": metadata_path, "video_path": video_path, "transcript_path": transcript_path, "note_path": note_path}
        previous = local_by_identity.get(identity)
        if previous is None or str(metadata.get("synced_at") or "") > str(previous["metadata"].get("synced_at") or ""):
            local_by_identity[identity] = candidate

    index_header = ["# Instagram Saved Reels", "", f"Last sync: {datetime.now(timezone.utc).isoformat()}", "", f"Reels in local knowledge base: {len(local_by_identity)}", ""]
    index_lines = list(index_header)
    long_index_lines = ["# Instagram Saved Reels Long", "", f"Last sync: {datetime.now(timezone.utc).isoformat()}", "", f"Reels in local knowledge base: {len(local_by_identity)}", ""]
    grouped_by_upload_date: dict[str, list[dict[str, Any]]] = {}
    for entry in local_by_identity.values():
        posted_at = entry["metadata"].get("posted_at")
        upload_date = str(posted_at)[:10] if posted_at else "Unknown upload date"
        grouped_by_upload_date.setdefault(upload_date, []).append(entry)
    for upload_date in sorted(grouped_by_upload_date, reverse=True):
        index_lines.extend([f"## {upload_date}", ""])
        long_index_lines.extend([f"## {upload_date}", ""])
        for entry in sorted(grouped_by_upload_date[upload_date], key=lambda candidate: candidate["metadata"].get("posted_at") or "", reverse=True):
            metadata = entry["metadata"]
            summary_text = entry["note_path"].read_text(encoding="utf-8").strip()
            summary_text = summary_text.split("## Files", 1)[0].removeprefix(f"# Reel by @{metadata.get('username') or 'unknown'}").strip()
            transcript_text = entry["transcript_path"].read_text(encoding="utf-8").strip().removeprefix("# Transcript").strip()
            username = metadata.get("username") or "unknown"
            profile_link = f"https://www.instagram.com/{username}/" if username != "unknown" else "https://www.instagram.com/"
            heading = f"### [{metadata.get('code')}]({entry['note_path'].relative_to(SCRIPT_DIR).as_posix()}) by [@{username}]({profile_link})"
            index_lines.extend([heading, "", summary_text, "", f"- [Video]({entry['video_path'].relative_to(SCRIPT_DIR).as_posix()})", f"- [Transcript]({entry['transcript_path'].relative_to(SCRIPT_DIR).as_posix()})", f"- [Metadata]({entry['metadata_path'].relative_to(SCRIPT_DIR).as_posix()})", ""])
            long_index_lines.extend([f"### {metadata.get('code')} by [@{username}]({profile_link})", "", transcript_text, ""])
    INDEX_FILE.write_text("\n".join(index_lines), encoding="utf-8")
    LONG_INDEX_FILE.write_text("\n".join(long_index_lines), encoding="utf-8")
    print(f"Sync complete: {len(synced)} Reel(s), {skipped} skipped, {len(failed)} failed.")
    print(f"Knowledge base index: {INDEX_FILE}")
    print(f"Long transcript index: {LONG_INDEX_FILE}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Instagram Reel sync failed: {error}", file=sys.stderr)
        raise SystemExit(1)
