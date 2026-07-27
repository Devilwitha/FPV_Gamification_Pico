import json
import network
import machine
import time
import urequests
import os
from ota_helpers import apply_firmware_bundle

CLIENT_WIFI_CONF = "wlan.conf"
GITHUB_API_URL = "https://api.github.com/repos/Devilwitha/FPV_Gamification_Pico/releases/latest"

def load_client_wifi_config():
    config = {"ssid": "", "password": ""}
    try:
        with open(CLIENT_WIFI_CONF, "r") as f:
            data = json.loads(f.read())
            config["ssid"] = data.get("ssid", "")
            config["password"] = data.get("password", "")
    except Exception:
        pass
    return config

def save_client_wifi_config(ssid, password):
    try:
        with open(CLIENT_WIFI_CONF, "w") as f:
            f.write(json.dumps({"ssid": ssid, "password": password}))
        return True
    except Exception:
        return False

def check_and_apply_github_update(led, feed_wdt, bundle_magic, log=print):
    config = load_client_wifi_config()
    ssid = config.get("ssid")
    password = config.get("password")
    if not ssid:
        return {"ok": False, "error": "No Client Wi-Fi configured."}

    wlan_sta = network.WLAN(network.STA_IF)
    wlan_ap = network.WLAN(network.AP_IF)

    # Disable AP temporarily
    was_ap_active = wlan_ap.active()
    if was_ap_active:
        wlan_ap.active(False)

    wlan_sta.active(True)

    blink_timer = machine.Timer(-1)
    led_state = False
    def blink_cb(t):
        nonlocal led_state
        led_state = not led_state
        led.value(1 if led_state else 0)

    blink_timer.init(period=2000, mode=machine.Timer.PERIODIC, callback=blink_cb)

    try:
        connected = False
        for attempt in range(3):
            log(f"Connecting to {ssid}... (Attempt {attempt+1})")
            wlan_sta.connect(ssid, password)

            timeout = 15
            while not wlan_sta.isconnected() and timeout > 0:
                feed_wdt()
                time.sleep_ms(1000)
                timeout -= 1

            if wlan_sta.isconnected():
                connected = True
                break

            wlan_sta.disconnect()

        if not connected:
            blink_timer.deinit()
            led.value(1)
            wlan_sta.active(False)
            if was_ap_active:
                wlan_ap.active(True)
            return {"ok": False, "error": "Could not connect to Wi-Fi nach 3 Versuchen."}

        log("Connected to Wi-Fi. Checking GitHub...")
        headers = {'User-Agent': 'MicroPython-Pico'}
        res = urequests.get(GITHUB_API_URL, headers=headers)
        if res.status_code != 200:
            raise Exception(f"GitHub API Error: {res.status_code}")

        release_data = res.json()
        res.close()

        latest_version = release_data.get("tag_name", "").lstrip("v")

        # Current version
        current_version = "0.0.0"
        try:
            with open("firmware_version.txt", "r") as f:
                current_version = f.read().strip()
        except Exception:
            pass

        if latest_version <= current_version:
            log("No update available.")
            wlan_sta.active(False)
            if was_ap_active:
                wlan_ap.active(True)
            return {"ok": True, "message": "No update available."}

        log(f"New version found: {latest_version}. Downloading...")

        # Find firmware.nbo asset
        download_url = None
        for asset in release_data.get("assets", []):
            if asset.get("name") == "firmware.nbo":
                download_url = asset.get("browser_download_url")
                break

        if not download_url:
            raise Exception("firmware.nbo not found in release.")

        res = urequests.get(download_url, headers=headers, stream=True)
        if res.status_code not in (200, 301, 302):
            raise Exception(f"Download Error: {res.status_code}")

        tmp_file = "update_download.tmp"
        with open(tmp_file, "wb") as f:
            while True:
                chunk = res.raw.read(512)
                if not chunk:
                    break
                f.write(chunk)
                feed_wdt()
        res.close()

        log("Download complete. Applying update...")

        from ota_helpers import apply_firmware_bundle
        extracted_files, needs_restart = apply_firmware_bundle(tmp_file, None, bundle_magic, log=log, feed_wdt=feed_wdt)

        os.remove(tmp_file)

        # Also clean up any staging file
        try:
            os.remove('update.pbp')
        except Exception:
            pass

        blink_timer.deinit()
        led.value(1)
        log("Update applied successfully. Rebooting...")
        return {"ok": True, "message": "Update installed. Rebooting...", "restart": True}

    except Exception as e:
        blink_timer.deinit()
        led.value(1)
        log(f"Update failed: {e}")
        wlan_sta.active(False)
        if was_ap_active:
            wlan_ap.active(True)
        return {"ok": False, "error": str(e)}
