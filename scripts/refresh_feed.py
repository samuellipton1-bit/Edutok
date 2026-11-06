import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

YOUTUBE_KEY = os.environ["YOUTUBE_API_KEY"]
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
FEED_PATH = Path("feed.json")
MAX_SECONDS = 120
MAX_NEW_VIDEOS = 25

# Broader searches cover the subjects EduTok is meant to teach.
# YouTube's "short" filter can include videos up to four minutes;
# get_video_details() enforces the actual two-minute limit.
SEARCH_TERMS = [
    "educational shorts explained",
    "science facts explained short",
    "history facts explained short",
    "math concepts explained short",
    "geography facts explained short",
    "psychology concepts explained short",
    "technology explained short",
    "economics explained short",
    "philosophy explained short",
    "mythology stories explained short",
    "baseball history facts short",
    "chess lessons for beginners short",
]

def get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))

def youtube_search(term):
    params = urllib.parse.urlencode({
        "part": "snippet",
        "type": "video",
        "q": term,
        "maxResults": 20,
        "order": "relevance",
        "videoDuration": "short",
        "safeSearch": "strict",
        "key": YOUTUBE_KEY
    })
    data = get_json("https://www.googleapis.com/youtube/v3/search?" + params)
    return data.get("items", [])

def parse_duration(value):
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 10**9
    hours, minutes, seconds = (int(x or 0) for x in match.groups())
    return hours * 3600 + minutes * 60 + seconds

def get_video_details(ids):
    found = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        params = urllib.parse.urlencode({
            "part": "snippet,contentDetails,status",
            "id": ",".join(chunk),
            "key": YOUTUBE_KEY
        })
        data = get_json("https://www.googleapis.com/youtube/v3/videos?" + params)
        for item in data.get("items", []):
            seconds = parse_duration(item.get("contentDetails", {}).get("duration"))
            if seconds <= MAX_SECONDS and item.get("status", {}).get("embeddable", True):
                snippet = item.get("snippet", {})
                found[item["id"]] = {
                    "id": item["id"],
                    "sourceTitle": snippet.get("title", "Educational video"),
                    "channel": snippet.get("channelTitle", ""),
                    "topic": "Other",
                    "title": snippet.get("title", "Educational video"),
                    "desc": "A short educational video.",
                    "durationSeconds": seconds,
                    "publishedAt": snippet.get("publishedAt", "")
                }
    return found

def ai_curate(candidates):
    if not candidates:
        return []
    prompt = {
        "task": (
            "Choose up to 25 useful videos for a general-audience educational feed. "
            "The candidates have already been verified as embeddable and no longer "
            "than 120 seconds. Be inclusive: keep a video if its title and channel "
            "make a genuine learning topic reasonably clear. Reject only clearly "
            "unrelated videos, advertisements, memes, or obviously misleading items. "
            "Do not require a perfect title. Use only supplied metadata and do not "
            "claim to have watched the videos. Assign one topic from History, Baseball, "
            "Chess, Politics · Civics, Philosophy, Psychology, Math, Mythology, Science, "
            "Geography, Technology, Other. Keep the original meaning in a concise title "
            "and write a cautious one-sentence description based only on the metadata."
        ),
        "output": {
            "videos": [
                {"id": "existing candidate ID", "topic": "topic",
                 "title": "concise title", "desc": "one sentence"}
            ]
        },
        "candidates": candidates
    }
    body = json.dumps({
        "model": "gpt-4.1-mini",
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You curate educational video metadata. Return valid JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}
        ]
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": "Bearer " + OPENAI_KEY,
            "Content-Type": "application/json"
        }
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))

    content = result["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    by_id = {v["id"]: v for v in candidates}
    curated = []
    allowed_topics = {
        "History", "Baseball", "Chess", "Politics · Civics", "Philosophy",
        "Psychology", "Math", "Mythology", "Science", "Geography", "Technology", "Other"
    }
    seen = set()
    for item in parsed.get("videos", []):
        original = by_id.get(item.get("id"))
        if not original or original["id"] in seen:
            continue
        seen.add(original["id"])
        video = dict(original)
        video["topic"] = item.get("topic") if item.get("topic") in allowed_topics else "Other"
        video["title"] = str(item.get("title") or original["sourceTitle"])[:100]
        video["desc"] = str(item.get("desc") or "A short educational video.")[:220]
        curated.append(video)
        if len(curated) >= MAX_NEW_VIDEOS:
            break
    return curated

def main():
    previous = json.loads(FEED_PATH.read_text("utf-8")) if FEED_PATH.exists() else {"videos": []}
    old_videos = previous.get("videos", [])
    old_ids = {v.get("id") for v in old_videos if v.get("id")}
    discovered = {}

    for term in SEARCH_TERMS:
        results = youtube_search(term)
        for item in results:
            video_id = item.get("id", {}).get("videoId")
            if video_id and video_id not in old_ids:
                discovered[video_id] = item

    print(f"YouTube search found {len(discovered)} unique new candidates.")
    candidates = get_video_details(list(discovered.keys()))
    print(f"{len(candidates)} candidates passed the 120-second and embeddable checks.")

    curated = ai_curate(list(candidates.values()))
    curated_ids = {v["id"] for v in curated}
    merged = curated + [v for v in old_videos if v.get("id") not in curated_ids]
    merged = merged[:100]

    output = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "videos": merged
    }
    FEED_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    print(f"Added {len(curated)} AI-curated videos; feed now contains {len(merged)} clips.")

if __name__ == "__main__":
    main()
