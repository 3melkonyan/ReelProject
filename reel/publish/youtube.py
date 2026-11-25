"""YouTube adapter with chunk retries; job retries belong to the pipeline."""

from reel.i18n import t as _t
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from reel.google_auth import get_credentials
from reel.publish.errors import PublishError
from reel.publish._common import api_errors, text


def _service():
    return build("youtube", "v3", credentials=get_credentials([
        "https://www.googleapis.com/auth/youtube.force-ssl"
    ]), cache_discovery=False)


def _id(response: dict, name: str = "id") -> str:
    value = response.get(name)
    if not isinstance(value, str) or not value:
        raise PublishError("YouTube response has no expected identifier; reconcile before retry", False)
    return value


@api_errors
def upload(video_path: str, title: str, description: str,
           tags: list[str], privacy: str = "unlisted") -> dict:
    text(title, "title")
    if len(title) > 100 or not isinstance(description, str) or len(description) > 5000:
        raise PublishError("YouTube title/description exceeds limits or is invalid", False)
    if privacy not in {"private", "unlisted", "public"}:
        raise PublishError("Invalid YouTube privacy setting", False)
    if not isinstance(tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags):
        raise PublishError("tags must be a list of non-empty strings", False)
    path = Path(video_path)
    if not path.is_file() or not path.stat().st_size:
        raise PublishError("Video file is missing or empty", False)
    media = MediaFileUpload(str(path), mimetype="video/*", chunksize=8 * 1024 * 1024, resumable=True)
    try:
        request = _service().videos().insert(
            part="snippet,status",
            body={"snippet": {"title": title, "description": description, "tags": tags},
                  "status": {"privacyStatus": privacy}},
            media_body=media,
        )
        response = None
        while response is None:
            status, response = request.next_chunk(num_retries=5)
            if status is not None:
                from reel.progress import report
                report(status.resumable_progress, path.stat().st_size)
        video_id = _id(response)
        return {"video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}"}
    finally:
        media.stream().close()


@api_errors
def find_by_tag(tag: str) -> str | None:
    """Search up to 100 newest uploads per channel for an exact tag."""
    text(tag, "tag")
    max_pages = 2
    service = _service()
    channels = service.channels().list(part="contentDetails", mine=True, maxResults=50).execute(num_retries=0)
    if not channels.get("items"):
        raise PublishError("No YouTube channel available for this Google account", False)
    for channel in channels["items"]:
        playlist_id = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        token = None
        for _ in range(max_pages):
            page = service.playlistItems().list(part="contentDetails", playlistId=playlist_id,
                maxResults=50, pageToken=token).execute(num_retries=0)
            ids = [item["contentDetails"]["videoId"] for item in page.get("items", [])]
            if ids:
                videos = service.videos().list(part="snippet", id=",".join(ids), maxResults=50).execute(num_retries=0)
                for video in videos.get("items", []):
                    if tag in video.get("snippet", {}).get("tags", []):
                        return _id(video)
            token = page.get("nextPageToken")
            if not token:
                break
    return None


@api_errors
def add_to_playlist(video_id: str, playlist_id: str) -> str:
    text(video_id, "video_id")
    text(playlist_id, "playlist_id")
    response = _service().playlistItems().insert(part="snippet", body={"snippet": {
        "playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}
    }}).execute(num_retries=0)
    return _id(response)


@api_errors
def is_in_playlist(video_id: str, playlist_id: str) -> bool:
    text(video_id, "video_id")
    text(playlist_id, "playlist_id")
    response = _service().playlistItems().list(part="id", playlistId=playlist_id,
        videoId=video_id, maxResults=1).execute(num_retries=0)
    return bool(response.get("items"))


@api_errors
def processing_status(video_id: str) -> str:
    """YouTube's uploadStatus: 'uploaded' while still processing, 'processed' when playable,
    'failed' / 'rejected' / 'deleted' when it never will be; 'missing' if the id is not found."""
    text(video_id, "video_id")
    response = _service().videos().list(part="status", id=video_id).execute(num_retries=0)
    items = response.get("items") or []
    return str((items[0].get("status") or {}).get("uploadStatus") or "unknown") if items else "missing"


@api_errors
def set_thumbnail(video_id: str, image_path: str) -> None:
    text(video_id, "video_id")
    path = Path(image_path)
    if not path.is_file() or not 0 < path.stat().st_size < 2 * 1024 * 1024:
        raise PublishError("Thumbnail must exist and be smaller than 2 MiB", False)
    media = MediaFileUpload(str(path), mimetype="image/png" if path.suffix.lower() == ".png" else "image/jpeg")
    try:
        _service().thumbnails().set(videoId=video_id, media_body=media).execute(num_retries=0)
    finally:
        media.stream().close()


CAPTION_NAME = _t('language.hy_name', language='hy')

def caption_name(language):
    return CAPTION_NAME if language == 'hy' else 'English' if language == 'en' else language


@api_errors
def find_captions(video_id: str, language: str = "hy") -> str | None:
    """Id of our uploaded (standard, not automatic) caption track in this language, if any."""
    text(video_id, "video_id")
    response = _service().captions().list(part="snippet", videoId=video_id).execute(num_retries=0)
    for item in response.get("items") or []:
        snippet = item.get("snippet") or {}
        if (snippet.get("language") == language and snippet.get("trackKind", "standard").lower() == "standard"
                and snippet.get("name") == caption_name(language)):
            return _id(item)
    return None


@api_errors
def upload_captions(video_id: str, caption_path: str, language: str = "hy") -> str:
    """upload an SRT caption track (published, not a draft). Needs the youtube.force-ssl scope."""
    text(video_id, "video_id")
    path = Path(caption_path)
    if not path.is_file() or not 0 < path.stat().st_size < 50 * 1024 * 1024:
        raise PublishError("Caption file must exist and be smaller than 50 MiB", False)
    media = MediaFileUpload(str(path), mimetype="application/octet-stream", resumable=False)
    try:
        response = _service().captions().insert(
            part="snippet", media_body=media,
            body={"snippet": {"videoId": video_id, "language": language, "name": caption_name(language), "isDraft": False}},
        ).execute(num_retries=0)
        return _id(response)
    finally:
        media.stream().close()


@api_errors
def list_playlists() -> list[dict]:
    """List up to 200 playlists owned by the authenticated channel."""
    service = _service()
    playlists = []
    token = None
    for _ in range(4):
        page = service.playlists().list(
            part="snippet,status,contentDetails", mine=True, maxResults=50, pageToken=token,
        ).execute(num_retries=0)
        for item in page.get("items", []):
            playlists.append({
                "id": _id(item),
                "title": item.get("snippet", {}).get("title", ""),
                "privacy": item.get("status", {}).get("privacyStatus", ""),
                "count": item.get("contentDetails", {}).get("itemCount", 0),
            })
        token = page.get("nextPageToken")
        if not token:
            break
    return playlists


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.parse_args()
    from reel.publish._smoke import youtube_smoke
    youtube_smoke()


@api_errors
def delete_video(video_id: str) -> None:
    """Deleting an already absent video is a successful retry."""
    from googleapiclient.errors import HttpError
    text(video_id, 'video_id')
    try:
        _service().videos().delete(id=video_id).execute(num_retries=0)
    except HttpError as error:
        if int(error.resp.status) != 404: raise
