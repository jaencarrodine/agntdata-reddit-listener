#!/usr/bin/env python3
"""
Reddit Social Listener — powered by agntdata
Monitors subreddits for people experiencing pain your product solves.
Scores posts with GPT-4o-mini and delivers a digest to Slack.

Setup: see README.md
"""

import os
import json
import time
import requests
from datetime import datetime
from collections import defaultdict

# ── Config ───────────────────────────────────────────────────────────────────
# Set these via environment variables or edit directly

AGNTDATA_API_KEY = os.environ.get("AGNTDATA_API_KEY", "your_agntdata_api_key_here")
SLACK_BOT_TOKEN  = os.environ.get("SLACK_BOT_TOKEN",  "xoxb-your-slack-bot-token")
SLACK_CHANNEL    = os.environ.get("SLACK_CHANNEL",    "C0000000000")  # channel ID
OPENAI_API_KEY   = os.environ.get("OPENAI_API_KEY",   "sk-your-openai-key")

SEEN_POSTS_FILE = os.environ.get("SEEN_POSTS_FILE", "./state/seen.json")

# Cap GPT scoring to control spend (~$0.05/run at 80 posts with gpt-4o-mini)
MAX_TO_SCORE = 80

# ── Targeting ────────────────────────────────────────────────────────────────
# Edit these to match your product's ICP

SUBREDDITS = [
    # Automation / agent builders
    "automation",
    "n8n",
    "AIAgentsInAction",
    "mcp",
    "LLMDevs",
    # GTM / outbound / sales
    "coldemail",
    "b2bmarketing",
    "sales",
    # SaaS founders
    "SaaS",
    "startups",
    "indiehackers",
    # Data / scraping
    "webscraping",
    "datasets",
    # OpenClaw community
    "better_claw",
    "hermesagent",
]

KEYWORDS = [
    # API fragmentation pain
    "multiple apis",
    "juggling apis",
    "api key management",
    "rapidapi",
    "rapidapi expensive",
    # LinkedIn data pain
    "linkedin api",
    "linkedin scraping",
    "linkedin scraper",
    "linkedin data",
    "linkedin blocked",
    "linkedin banned",
    # Clay / incumbent alternatives
    "clay alternative",
    "clay too expensive",
    "clay pricing",
    "apollo alternative",
    "zoominfo alternative",
    "zoominfo too expensive",
    "apollo too expensive",
    # GTM / outbound agents
    "lead enrichment",
    "data enrichment",
    "prospect enrichment",
    "outbound agent",
    "ai sdr",
    "gtm agent",
    "social data api",
    # Scraping pain
    "scraping blocked",
    "scraping rate limit",
    "scraping banned",
    "twitter api expensive",
    "twitter scraping",
    "x api expensive",
]

# Strong prefilter — specific phrases, one match = pass
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
    "twitter scraping", "api key management",
]

# Weaker fallback — need 2+ hits to pass
PREFILTER_WEAK = [
    "linkedin", "clay", "apollo", "zoominfo", "scraping", "scraper",
    "enrichment", "api expensive", "rate limit", "blocked", "banned",
    "outbound", "sdr", "data api",
]

# ── Scoring prompt ────────────────────────────────────────────────────────────
# Edit this to describe your product and ICP

SCORING_PROMPT_TEMPLATE = """\
You are evaluating Reddit posts for relevance to agntdata (agnt_), a unified social data API.

agntdata solves: builders and AI agents that need data from LinkedIn, X/Twitter, Reddit, TikTok, \
Instagram, Facebook, YouTube are forced to sign up for multiple separate APIs (often via RapidAPI), \
manage multiple keys, deal with fragmented rate limits, and pay a lot. agntdata gives one API key \
for all of them at a fraction of the cost. It's especially useful for: AI agents doing \
GTM/outbound/lead enrichment, builders automating with n8n/Make/LangChain, developers building \
social listening tools, and anyone replacing Clay-style workflows with agent-native code.

Rate this post's relevance on a scale of 1-10 where:
- 10 = Person is actively experiencing the pain agntdata solves and would benefit immediately
- 7-9 = Person is building something that needs social/data APIs or is frustrated with current tools
- 4-6 = Tangentially related (general automation, AI agent building, SaaS growth talk)
- 1-3 = Not relevant

Also write a 1-sentence pain summary if score >= 6.

Respond with JSON only: {"score": <int>, "pain": "<string or null>"}

Post title: TITLE_PLACEHOLDER
Subreddit: r/SUBREDDIT_PLACEHOLDER
Matched keywords: KEYWORDS_PLACEHOLDER
Post body (truncated): BODY_PLACEHOLDER"""

# ── Helpers ──────────────────────────────────────────────────────────────────

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

def score_post(title, subreddit, keywords, body):
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
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": "Bearer " + OPENAI_API_KEY,
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=20,
            )
            resp = r.json()
            if r.status_code == 429 or "error" in resp:
                wait = 15 * (attempt + 1)
                log("  [rate limit] waiting {}s...".format(wait))
                time.sleep(wait)
                continue
            result = json.loads(resp["choices"][0]["message"]["content"])
            return result.get("score", 0), result.get("pain")
        except Exception as e:
            log("  [warn] score_post failed (attempt {}): {}".format(attempt + 1, e))
            time.sleep(5)
    return 0, None

def post_to_slack(blocks):
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={
            "Authorization": "Bearer " + SLACK_BOT_TOKEN,
            "Content-Type": "application/json",
        },
        json={"channel": SLACK_CHANNEL, "blocks": blocks},
        timeout=15,
    )
    data = resp.json()
    if not data.get("ok"):
        log("  [warn] Slack post failed: {}".format(data.get("error")))

# ── Main ─────────────────────────────────────────────────────────────────────

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

    prefiltered = {
        url: data for url, data in new_posts.items()
        if passes_prefilter(
            data["post"].get("title", ""),
            data["post"].get("selftext", ""),
        )
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

    seen.update(new_posts.keys())
    save_seen(seen)

    if not new_posts:
        log("  Nothing new.")
        return

    scored = []
    for i, (url, data) in enumerate(to_score.items()):
        post = data["post"]
        keywords = list(data["keywords"])
        title = post.get("title", "")
        subreddit = post.get("subreddit", "")
        body = post.get("selftext", "")
        score, pain = score_post(title, subreddit, keywords, body)
        log("  [{}/{}] Score {}: [{}] {}".format(i + 1, len(to_score), score, subreddit, title[:55]))
        if score >= 6:
            scored.append({
                "url": url,
                "title": title,
                "subreddit": subreddit,
                "score": score,
                "pain": pain,
                "keywords": keywords,
                "reddit_score": post.get("score", 0),
                "comments": post.get("num_comments", 0),
            })
        time.sleep(0.5)

    scored.sort(key=lambda x: x["score"], reverse=True)
    log("  High-relevance posts (score >= 6): {}".format(len(scored)))

    now_str = datetime.now().strftime("%b %d, %H:%M")
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "Reddit Listener — {}".format(now_str)},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Scanned *{}* subreddits x *{}* keywords — *{}* new — *{}* pre-filtered — *{}* scored — *{}* hits".format(
                    len(SUBREDDITS), len(KEYWORDS), len(new_posts), len(prefiltered), len(to_score), len(scored)
                ),
            },
        },
        {"type": "divider"},
    ]

    if not scored:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "No high-relevance posts this run (all scored below 6)."},
        })
    else:
        for p in scored[:20]:
            score_emoji = "🔴" if p["score"] >= 9 else "🟠" if p["score"] >= 7 else "🟡"
            kw_str = ", ".join("`{}`".format(k) for k in sorted(p["keywords"])[:4])
            pain_str = "\n> _{}_".format(p["pain"]) if p["pain"] else ""
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "{} *{}/10* | r/{} | up{} · {} comments\n"
                        "<{}|{}>{}\n"
                        "Keywords: {}"
                    ).format(
                        score_emoji, p["score"], p["subreddit"],
                        p["reddit_score"], p["comments"],
                        p["url"], p["title"],
                        pain_str,
                        kw_str,
                    ),
                },
            })
            blocks.append({"type": "divider"})

    post_to_slack(blocks)
    log("  Digest posted to Slack. Done.")

if __name__ == "__main__":
    run()
