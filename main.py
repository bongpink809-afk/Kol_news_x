"""
X (Twitter) KOL news bot.

Reads accounts.json (niche -> list of X usernames), polls tweets for each
account via the TwitterAPI.io API, and forwards any tweet newer than the last
one seen to a Telegram forum topic (one topic per niche, mapped in
topics.json). Read-only: never posts, replies, or follows anything.

Polling is 2-tier (see tiers.json): each niche belongs to a tier with its own
interval_minutes. On every run, an account is only checked (API called) once
its tier's interval has elapsed since last_checked_at; when it is checked,
every tweet accumulated since the last check is sent (not just the newest).
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

TWITTERAPI_KEY = os.environ.get("TWITTERAPI_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TWITTERAPI_URL = "https://api.twitterapi.io/twitter/user/last_tweets"
TELEGRAM_SEND_URL_TMPL = "https://api.telegram.org/bot{token}/sendMessage"

ACCOUNTS_FILE = "accounts.json"
TOPICS_FILE = "topics.json"
TIERS_FILE = "tiers.json"
LAST_SEEN_FILE = "last_seen.json"

# TwitterAPI.io's QPS limit is tiered by account credit balance (see
# https://twitterapi.io/qps-limits): >=1,000 credits -> 3 QPS, >=5,000 -> 6,
# >=10,000 -> 10, >=50,000 -> 20. New/low-balance accounts are capped at just
# 1 request per 5 seconds. We default to that safest pace (this project only
# makes ~130 calls/day total, so the extra delay costs nothing meaningful) —
# lower it once you've confirmed a higher QPS tier from your account balance.
TWITTERAPI_REQUEST_DELAY_SECONDS = 5.0
# Telegram recommends ~1 msg/s to the same chat to avoid flood limits.
TELEGRAM_REQUEST_DELAY_SECONDS = 1.0
MAX_TWEET_TEXT_LENGTH = 600

# TwitterAPI.io's last_tweets endpoint returns up to this many tweets per
# page; only the first page is fetched here (no pagination). If an account
# accumulates more new tweets than this between two checks, some of the
# oldest new ones could be missed — we only log a warning for that case.
TWITTERAPI_TWEETS_PAGE_LIMIT = 20

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("x_kol_bot")


def load_json(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        raise FileNotFoundError(f"Required file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_niche_tier_lookup(tiers):
    """Return {niche: (tier_name, interval_minutes)} from tiers.json."""
    lookup = {}
    for tier_name, tier_cfg in tiers.items():
        interval_minutes = tier_cfg.get("interval_minutes", 0)
        for niche in tier_cfg.get("niches", []):
            lookup[niche] = (tier_name, interval_minutes)
    return lookup


def get_last_seen_entry(last_seen, username):
    """Read a last_seen.json entry, supporting both the old and new format.

    Old (pre-2-tier): {"username": "<tweet_id>"}
    New: {"username": {"last_tweet_id": "...", "last_checked_at": "..."}}

    Returns (last_tweet_id, last_checked_at_iso_or_None).
    """
    entry = last_seen.get(username)
    if entry is None:
        return None, None
    if isinstance(entry, dict):
        return entry.get("last_tweet_id"), entry.get("last_checked_at")
    return entry, None


def is_due(last_checked_at, interval_minutes):
    """Whether interval_minutes have elapsed since last_checked_at (UTC ISO 8601)."""
    if not last_checked_at:
        return True
    try:
        last_dt = datetime.fromisoformat(str(last_checked_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_dt >= timedelta(minutes=interval_minutes)


def fetch_last_tweets(username):
    """Call the TwitterAPI.io 'get user last tweets' endpoint for one username.

    Returns the list of tweet dicts (newest first), or [] on failure.
    """
    headers = {"X-API-Key": TWITTERAPI_KEY}
    params = {"userName": username}
    response = requests.get(TWITTERAPI_URL, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    logger.debug("Raw TwitterAPI.io response for @%s: %s", username, json.dumps(payload)[:2000])

    status = payload.get("status")
    if status is not None and status != "success":
        raise RuntimeError(
            f"TwitterAPI.io returned status={status!r} for @{username}: "
            f"{payload.get('message') or payload.get('msg')}"
        )

    tweets = (payload.get("data") or {}).get("tweets")
    if tweets is None:
        logger.warning(
            "Unexpected response shape for @%s (no 'tweets' key). Full payload: %s",
            username,
            json.dumps(payload)[:2000],
        )
        return []

    return tweets


def send_telegram_message(niche, username, tweet_text, tweet_url, message_thread_id):
    text = tweet_text or ""
    if len(text) > MAX_TWEET_TEXT_LENGTH:
        text = text[:MAX_TWEET_TEXT_LENGTH].rstrip() + "..."

    message = f"[{niche}] @{username}\n\n{text}\n\n🔗 {tweet_url}"

    url = TELEGRAM_SEND_URL_TMPL.format(token=TELEGRAM_BOT_TOKEN)
    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": False,
    }
    if message_thread_id:
        data["message_thread_id"] = message_thread_id

    response = requests.post(url, data=data, timeout=30)
    if response.status_code == 429:
        retry_after = response.json().get("parameters", {}).get("retry_after", 1)
        logger.warning(
            "Telegram rate-limited sendMessage (429) for @%s, retrying in %d second(s)...",
            username,
            retry_after,
        )
        time.sleep(retry_after + 1)
        response = requests.post(url, data=data, timeout=30)

    if not response.ok:
        raise RuntimeError(f"Telegram sendMessage failed ({response.status_code}): {response.text}")


def tweet_id_int(tweet):
    return int(tweet["id"])


def process_account(niche, username, thread_id, previous_id):
    """Fetch, diff, and send every accumulated new tweet for one account.

    Returns (sent_count, newest_id_or_None, fetched_count).
    """
    tweets = fetch_last_tweets(username)
    fetched_count = len(tweets)
    if not tweets:
        return 0, None, 0

    newest_id = max(tweet_id_int(t) for t in tweets)

    if previous_id is None:
        # First run for this account: baseline only, don't spam old tweets.
        logger.info("First run for @%s — baselining at tweet id %s (no notifications sent).", username, newest_id)
        return 0, newest_id, fetched_count

    new_tweets = [t for t in tweets if tweet_id_int(t) > int(previous_id)]
    if not new_tweets:
        return 0, newest_id, fetched_count

    # Send oldest-first so the topic reads chronologically.
    new_tweets.sort(key=tweet_id_int)

    if thread_id is None:
        logger.warning(
            "Niche '%s' has no message_thread_id configured in topics.json — "
            "sending %d new tweet(s) from @%s to the group's General topic instead.",
            niche,
            len(new_tweets),
            username,
        )

    for tweet in new_tweets:
        send_telegram_message(niche, username, tweet.get("text"), tweet.get("url"), thread_id)
        time.sleep(TELEGRAM_REQUEST_DELAY_SECONDS)

    if len(new_tweets) == fetched_count and fetched_count >= TWITTERAPI_TWEETS_PAGE_LIMIT:
        logger.warning(
            "@%s (niche '%s'): all %d fetched tweet(s) were new — this account may have posted more than "
            "the API's %d-tweet page limit since the last check, so some older new tweets could be missing. "
            "Consider a shorter tier interval for this niche in tiers.json.",
            username,
            niche,
            fetched_count,
            TWITTERAPI_TWEETS_PAGE_LIMIT,
        )

    return len(new_tweets), newest_id, fetched_count


def main():
    missing = [
        name
        for name, val in (
            ("TWITTERAPI_KEY", TWITTERAPI_KEY),
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        )
        if not val
    ]
    if missing:
        logger.error("Missing required environment variable(s): %s. Set them in .env or repo secrets.", ", ".join(missing))
        sys.exit(1)

    accounts = load_json(ACCOUNTS_FILE)
    topics = load_json(TOPICS_FILE)
    tiers = load_json(TIERS_FILE)
    last_seen = load_json(LAST_SEEN_FILE, default={})

    niche_tier_lookup = build_niche_tier_lookup(tiers)
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    niches_checked = 0
    accounts_total = 0
    accounts_checked = 0
    accounts_skipped = 0
    total_new_tweets = 0
    errors = []

    for niche, usernames in accounts.items():
        niches_checked += 1
        thread_id = topics.get(niche)
        tier_name, interval_minutes = niche_tier_lookup.get(niche, (None, 0))
        if tier_name is None:
            logger.warning(
                "Niche '%s' is missing from tiers.json — treating it as due on every run until you add it.",
                niche,
            )

        for username in usernames:
            accounts_total += 1
            previous_id, last_checked_at = get_last_seen_entry(last_seen, username)

            if not is_due(last_checked_at, interval_minutes):
                accounts_skipped += 1
                continue

            accounts_checked += 1
            try:
                sent_count, newest_id, fetched_count = process_account(niche, username, thread_id, previous_id)
                resolved_id = newest_id if newest_id is not None else previous_id
                last_seen[username] = {
                    "last_tweet_id": str(resolved_id) if resolved_id is not None else None,
                    "last_checked_at": now_iso,
                }
                total_new_tweets += sent_count
                logger.info(
                    "Checked @%s [%s / tier=%s]: fetched %d tweet(s), %d new sent.",
                    username,
                    niche,
                    tier_name or "unconfigured",
                    fetched_count,
                    sent_count,
                )
            except Exception as exc:  # noqa: BLE001 - one bad account must not stop the run
                errors.append(f"@{username} ({niche}): {exc}")
                logger.error("Error processing @%s in niche '%s': %s", username, niche, exc)
            finally:
                time.sleep(TWITTERAPI_REQUEST_DELAY_SECONDS)

    save_json(LAST_SEEN_FILE, last_seen)

    logger.info(
        "Run complete. Niches: %d | Accounts total: %d | Checked: %d | Skipped (not due): %d | "
        "New tweets sent: %d | Errors: %d",
        niches_checked,
        accounts_total,
        accounts_checked,
        accounts_skipped,
        total_new_tweets,
        len(errors),
    )
    if errors:
        logger.info("Error details:\n%s", "\n".join(errors))


if __name__ == "__main__":
    main()
