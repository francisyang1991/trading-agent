"""
Download 1 Year Discord Signal History — Knowledge Hub Builder
================================================================
Downloads messages from all Goku and Wilson signal channels going back 365 days.
Saves to workspace/data/knowledge_hub/discord_signals_1year.json

This data is used by:
  - pattern_library.py: seed initial win rates for technical patterns
  - signal_tracker.py: historical trader grading
  - signal_pipeline.py: conviction scoring calibration

Usage:
  export DISCORD_USER_TOKEN="..."
  python download_1year_history.py [--days 365]
"""

import requests
import json
import time
import os
import sys
import argparse
from datetime import datetime, timedelta, timezone

# All signal channels
CHANNELS = {
    "1277321989874385029": "Goku-Main",
    "1411717415565393970": "Goku-Alerts",
    "1227315745352847461": "Goku-Ideas",
    "1393715240474247249": "Goku-Raw",
    "1211549165629476924": "Wilson-Main",
}

AUTH_TOKEN = os.environ.get("DISCORD_USER_TOKEN", "")

# Output paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_HUB_DIR = os.path.join(SCRIPT_DIR, "../../data/knowledge_hub")
OUTPUT_FILE = os.path.join(KNOWLEDGE_HUB_DIR, "discord_signals_1year.json")

# Also update the standard 60d file used by the bot
DATA_FILE_60D = os.path.join(SCRIPT_DIR, "../../data/real_discord_messages_goku_wilson_60d.txt")


def fetch_messages(channel_id, params, retries=3):
    """Fetch messages from Discord API with retry and rate-limit handling."""
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
    headers = {"authorization": AUTH_TOKEN}

    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code == 429:
                retry_after = float(r.json().get("retry_after", 2))
                print(f"  Rate limited. Waiting {retry_after:.1f}s...")
                time.sleep(retry_after + 0.5)
                continue
            if r.status_code == 401:
                print("ERROR: Discord user token is invalid/expired.")
                sys.exit(1)
            if r.status_code == 403:
                print(f"  WARNING: No access to channel {channel_id}")
                return []
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            print(f"  Timeout (attempt {attempt + 1}/{retries})")
            time.sleep(2)
        except Exception as e:
            print(f"  Error: {e} (attempt {attempt + 1}/{retries})")
            time.sleep(2)
    return []


def retrieve_historical(channel_id, channel_name, days=365):
    """Fetch full history backwards from now, up to `days` days."""
    collected = []
    last_id = None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    batch_num = 0

    print(f"\n{'='*60}")
    print(f"Channel: {channel_name} ({channel_id})")
    print(f"Fetching {days} days of history (back to {cutoff.strftime('%Y-%m-%d')})...")
    print(f"{'='*60}")

    while True:
        params = {"limit": 100}  # Max per request
        if last_id:
            params["before"] = last_id

        messages = fetch_messages(channel_id, params)
        if not messages:
            break

        batch_num += 1
        oldest_in_batch = None
        for msg in messages:
            ts = msg.get("timestamp")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue

            if dt < cutoff:
                print(f"  Reached cutoff date. Total: {len(collected)} messages")
                return collected

            collected.append(msg)
            last_id = msg["id"]
            oldest_in_batch = dt

        if oldest_in_batch:
            days_back = (datetime.now(timezone.utc) - oldest_in_batch).days
            print(
                f"  Batch {batch_num}: {len(messages)} msgs | "
                f"Total: {len(collected)} | "
                f"Oldest: {oldest_in_batch.strftime('%Y-%m-%d')} ({days_back}d ago)"
            )
        else:
            break

        time.sleep(0.6)  # Respect rate limits

    print(f"  Done. Total: {len(collected)} messages")
    return collected


def extract_signal_summary(msg):
    """Extract a compact signal summary from a raw Discord message."""
    content = msg.get("content", "")
    author = msg.get("author", {}).get("username", "unknown")
    ts = msg.get("timestamp", "")
    attachments = msg.get("attachments", [])
    embeds = msg.get("embeds", [])

    image_urls = []
    for att in attachments:
        url = att.get("url", "")
        if any(ext in url.lower() for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
            image_urls.append(url)
    for emb in embeds:
        if emb.get("image", {}).get("url"):
            image_urls.append(emb["image"]["url"])
        if emb.get("thumbnail", {}).get("url"):
            image_urls.append(emb["thumbnail"]["url"])

    return {
        "id": msg.get("id"),
        "timestamp": ts,
        "author": author,
        "content": content,
        "image_urls": image_urls,
        "has_images": len(image_urls) > 0,
        "embed_count": len(embeds),
    }


def main():
    parser = argparse.ArgumentParser(description="Download Discord signal history")
    parser.add_argument("--days", type=int, default=365, help="Days of history (default: 365)")
    parser.add_argument("--token", type=str, default="", help="Discord user token (or set DISCORD_USER_TOKEN env)")
    args = parser.parse_args()

    global AUTH_TOKEN
    if args.token:
        AUTH_TOKEN = args.token

    if not AUTH_TOKEN:
        print("ERROR: No Discord user token. Set DISCORD_USER_TOKEN or use --token")
        sys.exit(1)

    # Verify token
    print("Verifying Discord token...")
    r = requests.get(
        "https://discord.com/api/v9/users/@me",
        headers={"authorization": AUTH_TOKEN},
        timeout=10,
    )
    if r.status_code != 200:
        print(f"ERROR: Token invalid (status {r.status_code})")
        sys.exit(1)
    user = r.json()
    print(f"Authenticated as: {user.get('username')}#{user.get('discriminator')}")

    # Create knowledge hub directory
    os.makedirs(KNOWLEDGE_HUB_DIR, exist_ok=True)

    # Download from all channels
    all_data = {}
    total_messages = 0
    total_signals = 0

    for channel_id, channel_name in CHANNELS.items():
        raw_msgs = retrieve_historical(channel_id, channel_name, days=args.days)
        all_data[channel_id] = raw_msgs
        total_messages += len(raw_msgs)

        # Count messages with potential signals (contain $ ticker references)
        signal_count = sum(1 for m in raw_msgs if "$" in m.get("content", ""))
        total_signals += signal_count
        print(f"  Signal messages (with $ticker): {signal_count}")

    # Save raw data (full JSON, used by bot systems)
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total messages: {total_messages}")
    print(f"Signal messages: {total_signals}")
    print(f"Channels: {len(all_data)}")

    # Save full raw data
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_data, f, indent=2)
    size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
    print(f"\nSaved raw data: {OUTPUT_FILE} ({size_mb:.1f} MB)")

    # Also create a compact signal-only extract for pattern analysis
    compact_file = os.path.join(KNOWLEDGE_HUB_DIR, "signals_compact_1year.json")
    compact_data = {}
    for channel_id, msgs in all_data.items():
        channel_name = CHANNELS.get(channel_id, channel_id)
        summaries = [extract_signal_summary(m) for m in msgs]
        # Keep only messages with content (skip empty/system messages)
        summaries = [s for s in summaries if s["content"].strip()]
        compact_data[channel_id] = {
            "channel_name": channel_name,
            "message_count": len(summaries),
            "messages": summaries,
        }

    with open(compact_file, "w") as f:
        json.dump(compact_data, f, indent=2)
    size_mb2 = os.path.getsize(compact_file) / (1024 * 1024)
    print(f"Saved compact signals: {compact_file} ({size_mb2:.1f} MB)")

    # Also update the 60d file used by the bot (subset of the full data)
    cutoff_60d = datetime.now(timezone.utc) - timedelta(days=60)
    data_60d = {}
    for channel_id, msgs in all_data.items():
        recent = []
        for m in msgs:
            ts = m.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt >= cutoff_60d:
                    recent.append(m)
            except Exception:
                continue
        data_60d[channel_id] = recent

    with open(DATA_FILE_60D, "w") as f:
        json.dump(data_60d, f, indent=2)
    recent_count = sum(len(v) for v in data_60d.values())
    print(f"Updated 60d bot cache: {DATA_FILE_60D} ({recent_count} messages)")

    print(f"\n✅ Knowledge Hub ready at: {os.path.abspath(KNOWLEDGE_HUB_DIR)}")
    print("Files:")
    print(f"  discord_signals_1year.json  — Full raw messages ({args.days}d)")
    print(f"  signals_compact_1year.json  — Compact signal extract")


if __name__ == "__main__":
    main()
