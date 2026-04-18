#!/usr/bin/env python3
"""
agntdata Reddit Listener
Monitors subreddits for people experiencing pain your product solves.
Scores posts with Claude Haiku via Anthropic API.

Config: set env vars or edit defaults below.
  AGNTDATA_API_KEY, ANTHROPIC_API_KEY, SLACK_BOT_TOKEN, SLACK_CHANNEL

Usage:
  python3 agntdata-reddit-listener.py
"""

import os
import json
import time
import requests
from datetime import datetime
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────

AGNTDATA_API_KEY  = os.environ.get("AGNTDATA_API_KEY",  "")
SLACK_BOT_TOKEN   = os.environ.get("SLACK_BOT_TOKEN",   "")
SLACK_CHANNEL     = os.environ.get("SLACK_CHANNEL",     "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SEEN_POSTS_FILE = os.environ.get("SEEN_POSTS_FILE", "./state/reddit_seen.json")

MAX_TO_SCORE       = 80
MAX_POST_AGE_HOURS = 48  # ignore posts older than this

# ── Targeting (trimmed for testing — expand for production) ───────────────────

SUBREDDITS = [
    "SaaS",
    "indiehackers",
    "coldemail",
    "sales",
    "webscraping",
]

KEYWORDS = [
    "clay alternative",
    "linkedin scraping",
    "rapidapi",
    "ai sdr",
    "lead enrichment",
    "apollo alternative",
    "data enrichment",
    "outbound agent",
]

# Strong prefilter — one match = pass
PREFILTER_STRONG = [
    "linkedin api", "linkedin scraping", "linkedin scraper", "linkedin data",
    "linkedin blocked", "linkedin banned", "linkedin rate limit",
    "clay alternative", "clay too expensive", "clay pricing",
    "apollo alternative", "apollo too expensive", "zoominfo alternative",
    "zoominfo too expensive", "rapidapi", "rapid api",
    "lead enrichment", "prospect enrichment", "data enrichment agent",
    "social data api", "outbound agent", "ai sdr", "gtm agent",
    "scraping blocked", "scraping banned", "scraping rate limit",
    "twitter api expensive", "x api expensive", "multiple apis", "juggling apis",
    "unified api", "one api key", "tiktok api", "instagram api",
    "social media api", "mcp server", "mcp tool",
    "enrich leads", "enrich contacts",
]

# Weak fallback — need 2+ hits
PREFILTER_WEAK = [
    "linkedin", "clay", "apollo", "zoominfo", "scraping", "scraper",
    "enrichment", "api expensive", "rate limit", "blocked", "banned",
    "outbound", "sdr", "data api", "agent", "mcp",
    "social data", "twitter api", "instagram", "tiktok", "rapidapi",
    "cold email", "prospecting", "lead gen",
]

# ── Scoring prompt ────────────────────────────────────────────────────────────

SCORING_PROMPT_TEMPLATE = """\
You are evaluating Reddit posts for relevance to agnt_ — a unified social data API for AI agents and GTM builders.

agnt_ ideal customer: someone using Claude Code, n8n, GPT, or other AI agents to build outbound systems, \
LinkedIn automation, influencer outreach, lead generation, or GTM infrastructure. They need data from \
LinkedIn, X, Instagram, TikTok, Reddit, etc. and are either writing scrapers, using multiple APIs, \
or looking for a cleaner data source.

Rate relevance 1-10:
- 9-10 = Actively building GTM automation with AI agents and needs social/profile data (perfect prospect)
- 7-8 = Building AI-powered outbound, lead gen, or influencer systems — data sourcing will be a need
- 5-6 = Talking about GTM automation or AI agents but no clear data need yet
- 3-4 = General AI/automation content, tangentially related
- 1-2 = Not relevant (hype posts, complaints with no builder intent, unrelated topics)

Write a 1-sentence summary of what they're building or the pain they have if score >= 6.

Respond with JSON only: {"score": <int>, "pain": "<string or null>"}

Post title: TITLE_PLACEHOLDER
Subreddit: r/SUBREDDIT_PLACEHOLDER
Matched keywords: KEYWORDS_PLACEHOLDER
Post body (truncated): BODY_PLACEHOLDER"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg):
    print(msg, flush=True)

def load_seen():
    if os.path.exists(SEEN_POSTS_FILE):
        try:
            with open(SEEN_POSTS_FILE) as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    os.makedirs(os.path.dirname(os.path.abspath(SEEN_POSTS_FILE)), exist_ok=True)
    with open(SEEN_POSTS_FILE, "w") as f:
        json.dump(list(seen), f)

def is_recent(post):
    """Return True if post was created within MAX_POST_AGE_HOURS."""
    created = post.get("created_utc") or post.get("created")
    if not created:
        return True  # no timestamp = don't filter out
    age_hours = (time.time() - float(created)) / 3600
    return age_hours <= MAX_POST_AGE_HOURS

def passes_prefilter(title, body):
    text = (title + " " + body).lower()
    if any(term in text for term in PREFILTER_STRONG):
        return True
    weak_hits = sum(1 for term in PREFILTER_WEAK if term in text)
    return weak_hits >= 2

def search_posts(query, subreddit):
    url = "https://api.agntdata.dev/v1/reddit/getSearchPosts"
    params = {"query": query, "subreddit": subreddit, "sort": "new"}
    headers = {"Authorization": "Bearer " + AGNTDATA_API_KEY}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        d = r.json()
        if not d.get("success"):
            return []
        return d["data"]["data"]["posts"]
    except Exception as e:
        log("  [warn] search_posts failed ({} / {}): {}".format(query, subreddit, e))
        return []

# ── Anthropic scoring ────────────────────────────────────────────────────────

def score_post(url, post, keywords):
    """Score a single post via Anthropic claude-haiku-4-5. Returns (score, pain)."""
    title    = post.get("title", "")
    subreddit = post.get("subreddit", "")
    body     = post.get("selftext", "")
    prompt = (
        SCORING_PROMPT_TEMPLATE
        .replace("TITLE_PLACEHOLDER", title)
        .replace("SUBREDDIT_PLACEHOLDER", subreddit)
        .replace("KEYWORDS_PLACEHOLDER", ", ".join(keywords))
        .replace("BODY_PLACEHOLDER", body[:400].replace("\n", " "))
    )
    for attempt in range(3):
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                },
                timeout=20,
            )
            if r.status_code in (429, 529):
                wait = int(r.headers.get("retry-after", 10 * (attempt + 1)))
                log("  [rate limit] waiting {}s...".format(wait))
                time.sleep(wait)
                continue
            resp = r.json()
            if "error" in resp:
                log("  [anthropic error] {}".format(resp["error"]))
                time.sleep(5)
                continue
            content = resp["content"][0]["text"].strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            result = json.loads(content)
            return result.get("score", 0), result.get("pain")
        except Exception as e:
            log("  [warn] score_post failed (attempt {}): {}".format(attempt + 1, e))
            time.sleep(5)
    return 0, None

def run_scoring(to_score):
    """Score all posts, return {url: (score, pain)} dict."""
    log("  Scoring {} posts with Claude Haiku...".format(len(to_score)))
    scores = {}
    for i, (url, data) in enumerate(to_score.items()):
        score, pain = score_post(url, data["post"], list(data["keywords"]))
        title = data["post"].get("title", "")[:60]
        log("  [{}/{}] Score {}: {} — {}".format(i + 1, len(to_score), score, data["post"].get("subreddit", ""), title))
        scores[url] = (score, pain)
        time.sleep(0.3)
    return scores

# ── Slack ─────────────────────────────────────────────────────────────────────

def post_to_slack(blocks):
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": "Bearer " + SLACK_BOT_TOKEN, "Content-Type": "application/json"},
        json={"channel": SLACK_CHANNEL, "blocks": blocks},
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        log("  [warn] Slack post failed: {}".format(data.get("error")))

# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    log("\n[{}] Starting Reddit listener run...".format(datetime.now().strftime("%Y-%m-%d %H:%M")))
    seen = load_seen()

    collected = defaultdict(lambda: {"post": None, "keywords": set()})

    total_calls = 0
    for subreddit in SUBREDDITS:
        for keyword in KEYWORDS:
            log("  Searching r/{} for '{}'...".format(subreddit, keyword))
            posts = search_posts(keyword, subreddit)
            total_calls += 1
            for p in posts:
                post = p.get("data", p)
                permalink = post.get("permalink", "")
                if not permalink:
                    continue
                url = "https://reddit.com" + permalink
                collected[url]["post"] = post
                collected[url]["keywords"].add(keyword)
            time.sleep(0.3)

    log("  Total API calls: {}, unique posts found: {}".format(total_calls, len(collected)))

    new_posts = {url: data for url, data in collected.items() if url not in seen}
    log("  New posts (not seen before): {}".format(len(new_posts)))

    recent_posts = {url: data for url, data in new_posts.items() if is_recent(data["post"])}
    log("  Recent posts (< {}h old): {} (skipping {})".format(
        MAX_POST_AGE_HOURS, len(recent_posts), len(new_posts) - len(recent_posts)
    ))

    prefiltered = {
        url: data for url, data in recent_posts.items()
        if passes_prefilter(data["post"].get("title", ""), data["post"].get("selftext", ""))
    }
    log("  Posts passing pre-filter: {} (skipping {})".format(
        len(prefiltered), len(new_posts) - len(prefiltered)
    ))

    sorted_prefiltered = sorted(
        prefiltered.items(),
        key=lambda x: x[1]["post"].get("score", 0),
        reverse=True
    )
    to_score = dict(sorted_prefiltered[:MAX_TO_SCORE])
    if len(prefiltered) > MAX_TO_SCORE:
        log("  Capped scoring at top {} by Reddit score (had {})".format(MAX_TO_SCORE, len(prefiltered)))

    seen.update(new_posts.keys())  # mark all (including old) as seen so they never re-appear
    save_seen(seen)

    if not recent_posts:
        log("  Nothing new.")
        return

    # Score via Anthropic Haiku
    scores = run_scoring(to_score)

    scored = []
    for url, data in to_score.items():
        post = data["post"]
        score, pain = scores.get(url, (0, None))
        title = post.get("title", "")
        subreddit = post.get("subreddit", "")
        if score >= 6:
            scored.append({
                "url": url,
                "title": title,
                "subreddit": subreddit,
                "score": score,
                "pain": pain,
                "keywords": list(data["keywords"]),
                "reddit_score": post.get("score", 0),
                "comments": post.get("num_comments", 0),
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    log("  High-relevance posts (score >= 6): {}".format(len(scored)))

    now_str = datetime.now().strftime("%b %d, %H:%M")
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Reddit Listener — {}".format(now_str)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "Scanned *{}* subreddits x *{}* keywords — *{}* new — *{}* pre-filtered — *{}* scored — *{}* hits".format(
            len(SUBREDDITS), len(KEYWORDS), len(new_posts), len(prefiltered), len(to_score), len(scored)
        )}},
        {"type": "divider"},
    ]

    if not scored:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "No high-relevance posts this run (all scored below 6)."}})
    else:
        for p in scored[:20]:
            score_emoji = "🔴" if p["score"] >= 9 else "🟠" if p["score"] >= 7 else "🟡"
            kw_str = ", ".join("`{}`".format(k) for k in sorted(p["keywords"])[:4])
            pain_str = "\n> _{}_".format(p["pain"]) if p["pain"] else ""
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": (
                "{} *{}/10* | r/{} | up{} · {} comments\n"
                "<{}|{}>{}\n"
                "Keywords: {}"
            ).format(
                score_emoji, p["score"], p["subreddit"],
                p["reddit_score"], p["comments"],
                p["url"], p["title"], pain_str, kw_str,
            )}})
            blocks.append({"type": "divider"})

    post_to_slack(blocks)
    log("  Digest posted to Slack. Done.")

if __name__ == "__main__":
    run()
