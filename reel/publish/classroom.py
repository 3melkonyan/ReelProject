"""Publish a Classroom announcement; retry and deduplication are external."""
from googleapiclient.discovery import build
from reel.google_auth import get_credentials
from reel.publish.errors import PublishError
from reel.publish._common import api_errors, text, link


def _service():
    return build("classroom", "v1", credentials=get_credentials([
        "https://www.googleapis.com/auth/classroom.announcements"
    ]), cache_discovery=False)


@api_errors
def announce(course_id: str, title: str, url: str) -> str:
    text(course_id, "course_id")
    text(title, "title")
    link(url)
    response = _service().courses().announcements().create(courseId=course_id, body={
        "text": f"{title}\n{url}",
        "materials": [{"link": {"url": url, "title": title}}],
        "state": "PUBLISHED",
    }).execute(num_retries=0)
    identifier = response.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise PublishError("Classroom response missing announcement id; reconcile before retry", False)
    return identifier


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.parse_args()
    from reel.publish._smoke import classroom_smoke
    classroom_smoke()
