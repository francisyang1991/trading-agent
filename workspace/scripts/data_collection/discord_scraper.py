import requests
import json
import time
import sys
import os
from datetime import datetime, timedelta, timezone

# Configuration
CHANNELS = [
    "1277321989874385029", # Goku
    "1411717415565393970", # Goku
    "1227315745352847461", # Goku
    "1211549165629476924"  # Wilson
]

AUTH_TOKEN = os.environ.get("DISCORD_USER_TOKEN", "")
DATA_FILE = "/Users/francisyang/Downloads/trading_agent/workspace/data/real_discord_messages_goku_wilson_60d.txt"

def fetch_messages(channel_id, params):
    """Base fetch function"""
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages"
    headers = {"authorization": AUTH_TOKEN}
    
    try:
        r = requests.get(url, headers=headers, params=params)
        if r.status_code == 429:
            retry_after = float(r.json().get('retry_after', 1))
            print(f"Rate limited. Waiting {retry_after}s...")
            time.sleep(retry_after)
            return fetch_messages(channel_id, params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"Error fetching {channel_id}: {e}")
        return []

def retrieve_historical(channel_id, days=60):
    """Fetch history backwards from now"""
    collected = []
    last_id = None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    
    print(f"Fetching history for {channel_id}...")
    
    while True:
        params = {"limit": 50}
        if last_id:
            params["before"] = last_id
            
        messages = fetch_messages(channel_id, params)
        if not messages:
            break
            
        batch_valid = 0
        for msg in messages:
            ts = msg.get('timestamp')
            if not ts: continue
            
            try:
                dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except:
                continue
                
            if dt < cutoff:
                return collected
            
            collected.append(msg)
            batch_valid += 1
            last_id = msg['id']
            
        print(f"  Fetched {len(messages)} (Total: {len(collected)})")
        if batch_valid == 0: # All messages in batch were too old
            break
            
        time.sleep(0.5)
        
    return collected

def retrieve_new(channel_id, last_known_id):
    """Fetch new messages forwards from last_known_id"""
    collected = []
    current_after = last_known_id
    
    print(f"Fetching new messages for {channel_id} after {last_known_id}...")
    
    while True:
        params = {"limit": 50, "after": current_after}
        messages = fetch_messages(channel_id, params)
        
        if not messages:
            break
            
        # Messages from 'after' come oldest -> newest
        # We want to reverse them later to match 'before' order? 
        # Actually standard is usually Newest First or Oldest First.
        # My analysis scripts assume list of messages. Order shouldn't matter if they sort by date.
        
        collected.extend(messages)
        print(f"  Fetched {len(messages)} new messages")
        
        current_after = messages[0]['id'] # In 'after' pagination, [0] is the oldest in batch? 
        # Discord 'after' returns messages immediately following the ID.
        # If I request after ID 100, I get 101, 102, 103...
        # The list returned is usually chronological (oldest first).
        # So the last message in the list is the newest.
        current_after = messages[-1]['id']
        
        time.sleep(0.5)
        
    return collected

def main():
    # Load existing data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
            print(f"Loaded existing data ({len(data)} channels)")
        except:
            print("Error loading data, starting fresh")
            data = {}
    else:
        data = {}
        
    total_new = 0
    
    for channel_id in CHANNELS:
        existing_msgs = data.get(channel_id, [])
        
        if existing_msgs:
            # Find max ID
            # IDs are roughly chronological, max ID = newest
            max_id = max(existing_msgs, key=lambda x: int(x['id']))['id']
            new_msgs = retrieve_new(channel_id, max_id)
            
            # Merge (avoid duplicates just in case)
            existing_ids = set(m['id'] for m in existing_msgs)
            for msg in new_msgs:
                if msg['id'] not in existing_ids:
                    existing_msgs.append(msg)
                    total_new += 1
            
            data[channel_id] = existing_msgs
            
        else:
            # Full fetch
            msgs = retrieve_historical(channel_id)
            data[channel_id] = msgs
            total_new += len(msgs)
            
    # Save
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=2)
        
    print(f"\nSaved {total_new} new messages to {DATA_FILE}")

if __name__ == "__main__":
    main()
