import json
import os
import requests
import shutil

INPUT_FILE = "/Users/francisyang/Downloads/trading_agent/workspace/data/real_discord_messages_goku_wilson_60d.txt"
OUTPUT_DIR = "/Users/francisyang/Downloads/trading_agent/workspace/data/images"
MAP_FILE = "/Users/francisyang/Downloads/trading_agent/workspace/data/images/image_map.json"

def download_images():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
        
    with open(INPUT_FILE, 'r') as f:
        data = json.load(f)
        
    image_map = {}
    total_downloaded = 0
    
    print(f"Scanning {len(data)} channels for images...")
    
    for channel_id, messages in data.items():
        print(f"Checking channel {channel_id} ({len(messages)} messages)...")
        
        for msg in messages:
            if 'attachments' in msg and msg['attachments']:
                for att in msg['attachments']:
                    url = att.get('url')
                    filename = att.get('filename')
                    content_type = att.get('content_type', '')
                    
                    if not url or not content_type.startswith('image/'):
                        continue
                        
                    # Create a unique local filename
                    ext = os.path.splitext(filename)[1]
                    if not ext:
                        ext = '.jpg' # Default
                        
                    local_filename = f"{channel_id}_{msg['id']}_{att['id']}{ext}"
                    local_path = os.path.join(OUTPUT_DIR, local_filename)
                    
                    # Download
                    try:
                        r = requests.get(url, stream=True)
                        if r.status_code == 200:
                            with open(local_path, 'wb') as f:
                                r.raw.decode_content = True
                                shutil.copyfileobj(r.raw, f)
                            
                            if msg['id'] not in image_map:
                                image_map[msg['id']] = []
                            
                            image_map[msg['id']].append({
                                "original_url": url,
                                "local_path": local_path,
                                "filename": filename
                            })
                            total_downloaded += 1
                            # print(f"Downloaded {local_filename}")
                        else:
                            print(f"Failed to download {url}: {r.status_code}")
                    except Exception as e:
                        print(f"Error downloading {url}: {e}")
                        
    with open(MAP_FILE, 'w') as f:
        json.dump(image_map, f, indent=2)
        
    print(f"Downloaded {total_downloaded} images.")
    print(f"Map saved to {MAP_FILE}")

if __name__ == "__main__":
    download_images()
