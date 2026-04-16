# agntdata Reddit Listener

A social listening bot that monitors Reddit 2x daily for people experiencing the problems your product solves. Built on [agntdata](https://agntdata.dev) — a unified API for Reddit, LinkedIn, X, TikTok, Instagram, Facebook, and YouTube.

Scores every post with GPT-4o-mini and delivers a clean Slack digest with 🔴🟠🟡 heat indicators.

---

## What It Does

1. **Crawls** 15 subreddits × 31 keywords via the agntdata Reddit API
2. **Deduplicates** posts across keyword matches
3. **Pre-filters** with a two-tier keyword matcher to cut noise before GPT
4. **Scores** top 80 posts (by Reddit score) on 1–10 relevance via GPT-4o-mini
5. **Posts** a scored digest to your Slack channel twice a day

**Cost: ~$0.07/run × 2 runs/day ≈ $4/month total**

---

## Setup

### Prerequisites

- Python 3.9+
- A server or VPS to run the cron (or run locally)
- Three API keys: agntdata, OpenAI, Slack

### 1. Get an agntdata API key

Sign up at **https://app.agntdata.dev/dashboard**

agntdata gives you a single API key for Reddit, LinkedIn, X/Twitter, TikTok, Instagram, Facebook, and YouTube — no separate RapidAPI accounts needed.

```bash
# Test your key
curl https://api.agntdata.dev/v1/platforms \
  -H "Authorization: Bearer agnt_live_your_key"
```

### 2. Set up a Slack bot

1. Go to https://api.slack.com/apps → Create New App
2. Add the `chat:write` OAuth scope under **Bot Token Scopes**
3. Install to workspace → copy the `xoxb-...` Bot Token
4. Invite the bot to your channel: `/invite @yourbot`
5. Get the channel ID: right-click channel → View channel details → copy ID (`C0000000000`)

### 3. Get an OpenAI API key

https://platform.openai.com/api-keys — `gpt-4o-mini` costs ~$0.00015/1k input tokens.

### 4. Install & configure

```bash
git clone https://github.com/jaencarrodine/agntdata-reddit-listener
cd agntdata-reddit-listener

# Install dependencies
pip3 install requests

# Configure
cp .env.example .env
# Edit .env with your keys

# Create log dir
mkdir -p logs state

# Make setup script executable
chmod +x setup.sh
./setup.sh
```

### 5. Run it

```bash
# Load env vars and run
export $(cat .env | xargs) && python3 listener.py
```

You should see it crawling subreddits and posting to Slack within ~10 minutes.

### 6. Schedule it

```bash
# Edit crontab
crontab -e

# Add this line (adjust UTC hours for your timezone):
0 15,21 * * * cd /path/to/agntdata-reddit-listener && export $(cat .env | xargs) && python3 listener.py >> logs/listener.log 2>&1
```

---

## Customizing for Your Product

### Change the subreddits

Edit `SUBREDDITS` in `listener.py`. Pick communities where your ICP hangs out.

### Change the keywords

Edit `KEYWORDS`, `PREFILTER_STRONG`, and `PREFILTER_WEAK` in `listener.py`.

- **KEYWORDS** — what gets searched via the API
- **PREFILTER_STRONG** — specific phrases where one match = include for scoring
- **PREFILTER_WEAK** — broader words where two matches = include for scoring

### Update the scoring prompt

Edit `SCORING_PROMPT_TEMPLATE` to describe your product and what a high-value lead looks like. The better this description, the more accurate the scoring.

---

## How the Pre-filter Works

Two-tier approach to avoid burning GPT tokens on noise:

```
Strong terms (specific): "linkedin scraping", "clay alternative", "rapidapi"
  → one match = pass

Weak terms (broad): "linkedin", "clay", "scraping", "enrichment"
  → two matches required to pass
```

Posts that don't pass pre-filter are still marked as "seen" so they don't reappear next run.

---

## Slack Output

Each digest looks like:

```
Reddit Listener — Apr 16, 15:00

Scanned 15 subreddits x 31 keywords — 312 new posts — 48 pre-filtered — 48 scored — 7 hits

🔴 9/10 | r/n8n | up12 · 3 comments
"Need Reddit API for my lead enrichment workflow"
> Builder hitting rate limits on RapidAPI and looking for a cheaper unified solution
Keywords: `rapidapi`, `lead enrichment`, `data enrichment`

🟠 7/10 | r/automation | up8 · 5 comments
"Is there a Clay alternative that works with AI agents natively?"
> Founder frustrated that Clay doesn't expose a proper API for agent-driven workflows
Keywords: `clay alternative`, `outbound agent`
```

---

## File Structure

```
agntdata-reddit-listener/
├── listener.py        # Main script
├── setup.sh           # One-time setup
├── cron.txt           # Crontab entry
├── .env.example       # Config template
├── .env               # Your config (gitignored)
├── state/
│   └── seen.json      # Dedup state (auto-created)
└── logs/
    └── listener.log   # Cron output (auto-created)
```

---

## agntdata Reddit API

The script uses `GET /v1/reddit/getSearchPosts`:

```python
import requests

r = requests.get(
    "https://api.agntdata.dev/v1/reddit/getSearchPosts",
    params={"query": "linkedin scraping", "subreddit": "automation", "sort": "new"},
    headers={"Authorization": "Bearer agnt_live_your_key"},
)
posts = r.json()["data"]["data"]["posts"]
```

agntdata also has endpoints for LinkedIn profiles, X/Twitter search, TikTok videos, Instagram profiles, and more — all under one key. Docs: https://agntdata.dev/docs

---

## License

MIT
