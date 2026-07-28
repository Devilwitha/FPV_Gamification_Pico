import urllib.request
import json

url = "https://api.github.com/repos/Devilwitha/FPV_Gamification_Pico/releases/latest"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        print(f"Tag: {data.get('tag_name')}")
        assets = data.get('assets', [])
        for asset in assets:
            print(f"Asset: {asset.get('name')} - URL: {asset.get('browser_download_url')}")
except Exception as e:
    print(f"Error: {e}")
