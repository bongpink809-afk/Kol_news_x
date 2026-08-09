"""
X (Twitter) KOL news bot.

Reads accounts.json (niche -> list of X usernames), polls the latest tweets
for each account via the Sorsa API, and forwards any tweet newer than the
last one seen to a Telegram forum topic (one topic per niche, mapped in
topics.json). Read-only: never posts, replies, or follows anything.
"""

import json
import logging
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

SORSA_API_KEY = os.environ.get("SORSA_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SORSA_API_URL = "https://api.sorsa.io/v3/user-tweets"
TELEGRAM_SEND_URL_TMPL = "https://api.telegram.org/bot{token}/sendMessage"

ACCOUNTS_FILE = "accounts.json"
TOPICS_FILE = "topics.json"
LAST_SEEN_FILE = "last_seen.json"

# Sorsa allows 20 req/s on all plans, so a light delay is enough to stay safe.
SORSA_REQUEST_DELAY_SECONDS = 0.3
# Telegram recommends ~1 msg/s to the same chat to avoid flood limits.
TELEGRAM_REQUEST_DELAY_SECONDS = 1.0
MAX_TWEET_TEXT_LENGTH = 600

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


def fetch_last_tweets(username):
    """Call the Sorsa 'user-tweets' endpoint for one username.

    Returns the list of tweet dicts (newest first), or [] on failure.
    """
    headers = {"ApiKey": SORSA_API_KEY}
    body = {"username": username}
    response = requests.post(SORSA_API_URL, headers=headers, json=body, timeout=30)

    if not response.ok:
        try:
            error_message = response.json().get("message", response.text)
        except ValueError:
            error_message = response.text
        raise RuntimeError(f"Sorsa API error ({response.status_code}) for @{username}: {error_message}")

    payload = response.json()

    logger.debug("Raw Sorsa response for @%s: %s", username, json.dumps(payload)[:2000])

    tweets = payload.get("tweets")
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
    if not response.ok:
        raise RuntimeError(f"Telegram sendMessage failed ({response.status_code}): {response.text}")


def tweet_id_int(tweet):
    return int(tweet["id"])


def process_account(niche, username, thread_id, last_seen):
    """Fetch, diff, send, and return (new_tweet_count, updated_last_seen_id_or_None)."""
    tweets = fetch_last_tweets(username)
    if not tweets:
        return 0, None

    newest_id = max(tweet_id_int(t) for t in tweets)
    previous_id = last_seen.get(username)

    if previous_id is None:
        # First run for this account: baseline only, don't spam old tweets.
        logger.info("First run for @%s — baselining at tweet id %s (no notifications sent).", username, newest_id)
        return 0, newest_id

    new_tweets = [t for t in tweets if tweet_id_int(t) > int(previous_id)]
    if not new_tweets:
        return 0, newest_id

    # Send oldest-first so the topic reads chronologically.
    new_tweets.sort(key=tweet_id_int)

    sent_count = 0
    if thread_id is None:
        logger.warning(
            "Niche '%s' has no message_thread_id configured in topics.json — "
            "sending %d new tweet(s) from @%s to the group's General topic instead.",
            niche,
            len(new_tweets),
            username,
        )

    for tweet in new_tweets:
        # Sorsa doesn't return a permalink field, so build the standard tweet URL.
        tweet_url = f"https://x.com/{username}/status/{tweet['id']}"
        send_telegram_message(niche, username, tweet.get("full_text"), tweet_url, thread_id)
        sent_count += 1
        time.sleep(TELEGRAM_REQUEST_DELAY_SECONDS)

    return sent_count, newest_id


def main():
    missing = [
        name
        for name, val in (
            ("SORSA_API_KEY", SORSA_API_KEY),
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
    last_seen = load_json(LAST_SEEN_FILE, default={})

    niches_checked = 0
    accounts_checked = 0
    total_new_tweets = 0
    errors = []

    for niche, usernames in accounts.items():
        niches_checked += 1
        thread_id = topics.get(niche)

        for username in usernames:
            accounts_checked += 1
            try:
                sent_count, newest_id = process_account(niche, username, thread_id, last_seen)
                if newest_id is not None:
                    last_seen[username] = newest_id
                total_new_tweets += sent_count
            except Exception as exc:  # noqa: BLE001 - one bad account must not stop the run
                errors.append(f"@{username} ({niche}): {exc}")
                logger.error("Error processing @%s in niche '%s': %s", username, niche, exc)
            finally:
                time.sleep(SORSA_REQUEST_DELAY_SECONDS)

    save_json(LAST_SEEN_FILE, last_seen)

    logger.info(
        "Run complete. Niches checked: %d | Accounts checked: %d | New tweets sent: %d | Errors: %d",
        niches_checked,
        accounts_checked,
        total_new_tweets,
        len(errors),
    )
    if errors:
        logger.info("Error details:\n%s", "\n".join(errors))


if __name__ == "__main__":
    main()
