import requests
from flask import Flask, render_template, url_for, send_file, request, jsonify
from datetime import datetime, timedelta
import os
import random
import socket
import qrcode
import io
import json
import sys
import re
import threading
from urllib.parse import quote
from bs4 import BeautifulSoup

# Import variables and functions from the new cache builder script
from cache_builder import CACHE_DIRECTORY, SPECIES_FILE, load_species_from_file

# --- Constants and Configuration ---
CONFIG_PATH = "config.json"
DEFAULT_CONFIG = {
    "birdnet_pi_base_url": "",
    "config_version": 0
}
CONFIG_LOCK = threading.Lock()
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'application/json'
}
PROXIES = {"http": None, "https": None}
def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as cfg_file:
                file_data = json.load(cfg_file)
                if isinstance(file_data, dict):
                    config.update(file_data)
        except (IOError, json.JSONDecodeError) as exc:
            print(f"[WARN] Failed to read {CONFIG_PATH}: {exc}")
    return config

def save_config(config_data):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as cfg_file:
            json.dump(config_data, cfg_file, indent=2)
    except IOError as exc:
        print(f"[WARN] Unable to persist config update: {exc}")

def normalize_base_url(value):
    if not value:
        return ""
    value = value.strip()
    if not value:
        return ""
    if not value.startswith(('http://', 'https://')):
        value = f"http://{value}"
    return value.rstrip('/')

CONFIG = load_config()
BIRDNET_PI_BASE_URL = normalize_base_url(CONFIG.get('birdnet_pi_base_url', DEFAULT_CONFIG['birdnet_pi_base_url']))

def is_birdnet_configured():
    return bool(BIRDNET_PI_BASE_URL)

def set_birdnet_base_url(new_value):
    global BIRDNET_PI_BASE_URL, CONFIG
    normalized = normalize_base_url(new_value)
    with CONFIG_LOCK:
        CONFIG['birdnet_pi_base_url'] = normalized
        CONFIG['config_version'] = int(CONFIG.get('config_version', 0)) + 1
        save_config(CONFIG)
        BIRDNET_PI_BASE_URL = normalized
    with BIRD_DATA_CACHE_LOCK:
        BIRD_DATA_CACHE.update({
            "data": [],
            "api_is_down": False,
            "fetched_at": datetime.min,
            "refresh_in_progress": False
        })
    DETECTION_CACHE["id"] = None
    DETECTION_CACHE["raw_data"] = []
    DAILY_DETECTION_CACHE.clear()
    return normalized

def build_birdnet_pi_list_url():
    if not BIRDNET_PI_BASE_URL:
        return None
    return f"{BIRDNET_PI_BASE_URL}/todays_detections.php?ajax_detections=true&display_limit=undefined&hard_limit=1000"

def build_birdnet_pi_stats_url():
    if not BIRDNET_PI_BASE_URL:
        return None
    return f"{BIRDNET_PI_BASE_URL}/todays_detections.php"

SERVER_PORT = 5000
PINNED_SPECIES_FILE = "pinned_species.json"
PINNED_DURATION_HOURS = 24
BIRD_DATA_CACHE_TTL_SECONDS = 4

# --- Flask App Initialization ---
app = Flask(__name__, template_folder='static')

# --- Caching & Status Globals ---
DETECTION_CACHE = { "id": None, "raw_data": [] }
DAILY_DETECTION_CACHE = {}
BIRD_DATA_CACHE = {
    "data": [],
    "api_is_down": False,
    "fetched_at": datetime.min,
    "refresh_in_progress": False
}
BIRD_DATA_CACHE_LOCK = threading.Lock()

# --- Pinned Species Management ---
def load_pinned_species():
    """Load pinned species from JSON file."""
    if not os.path.exists(PINNED_SPECIES_FILE):
        return {}
    try:
        with open(PINNED_SPECIES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error loading pinned species file: {e}")
        return {}

def save_pinned_species(pinned_data):
    """Save pinned species to JSON file."""
    try:
        with open(PINNED_SPECIES_FILE, 'w', encoding='utf-8') as f:
            json.dump(pinned_data, f, indent=2)
    except IOError as e:
        print(f"Error saving pinned species file: {e}")

def add_pinned_species(species_name):
    """Add a species to the pinned list with 24-hour expiration."""
    pinned = load_pinned_species()
    # Only add if not already present (dismissed or not)
    if species_name not in pinned:
        pinned[species_name] = {
            'pinned_until': (datetime.now() + timedelta(hours=PINNED_DURATION_HOURS)).isoformat(),
            'dismissed': False
        }
        save_pinned_species(pinned)

def dismiss_pinned_species(species_name):
    """Mark a pinned species as dismissed."""
    pinned = load_pinned_species()
    if species_name in pinned:
        pinned[species_name]['dismissed'] = True
        save_pinned_species(pinned)
        return True
    return False

def get_active_pinned_species():
    """Get list of currently active (not expired, not dismissed) pinned species."""
    pinned = load_pinned_species()
    active = {}
    now = datetime.now()

    for species_name, data in list(pinned.items()):
        pinned_until = datetime.fromisoformat(data['pinned_until'])
        if not data.get('dismissed', False) and now < pinned_until:
            active[species_name] = data
        elif now >= pinned_until:
            # Clean up expired entries
            del pinned[species_name]

    if len(pinned) != len(active):
        save_pinned_species(pinned)

    return active

# --- IP and QR Code Helpers ---
def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def get_qr_target_url():
    return BIRDNET_PI_BASE_URL if is_birdnet_configured() else None

def build_display_access_url():
    scheme = request.scheme if request else 'http'
    ip = get_local_ip()
    port = ''
    if request and request.host and ':' in request.host:
        port = request.host.rsplit(':', 1)[1]
    elif request:
        port = request.environ.get('SERVER_PORT', '')
    if not port:
        port = str(SERVER_PORT)
    if port in ('80', '443'):
        return f"{scheme}://{ip}"
    return f"{scheme}://{ip}:{port}"

@app.route('/qr_code.png')
def qr_code():
    url = get_qr_target_url()
    if not url:
        placeholder = "Configure base URL first"
        img = qrcode.make(placeholder)
    else:
        img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/qr_setup.png')
def qr_setup_code():
    display_url = build_display_access_url()
    img = qrcode.make(display_url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

# --- Time Helper Functions ---
def parse_absolute_time_to_seconds_ago(time_str):
    if not time_str: return 0
    try:
        time_format = "%Y-%m-%d %H:%M:%S"
        detection_time = datetime.strptime(time_str, time_format)
        time_difference = datetime.now() - detection_time
        return max(0, time_difference.total_seconds())
    except (ValueError, TypeError):
        return 0

def format_seconds_ago(total_seconds):
    if total_seconds < 60: return f"{int(total_seconds)}s ago"
    minutes = total_seconds / 60
    if minutes < 60: return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24: return f"{int(hours)}h ago"
    return f"{int(hours / 24)}d ago"

def parse_detection_datetime(time_raw):
    """Convert the raw detection timestamp into a datetime for sorting/deduping."""
    if not time_raw:
        return datetime.min
    try:
        return datetime.strptime(time_raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.min

# --- Data Parsing and API Helpers ---
def check_image_url_fast(url):
    """Quick check if an image URL is accessible with very short timeout."""
    try:
        response = requests.head(url, timeout=0.5)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False

def parse_birdnet_pi_row(row, default_date):
    """Parse a single <tr> row from the BirdNET-Pi detections table."""
    cells = row.find_all('td')
    if len(cells) < 3:
        return None

    time_text = cells[0].get_text(strip=True).replace('\n', ' ').strip()
    mid_td = row.find('td', id='recent_detection_middle_td')

    species_button = mid_td.find('button', attrs={'name': 'species'}) if mid_td else None
    species_name = species_button.get_text(strip=True) if species_button else 'Unknown Species'

    image_tag = mid_td.find('img', {'id': 'birdimage'}) if mid_td else None
    image_url = image_tag['src'] if image_tag and image_tag.has_attr('src') else ''

    confidence_value = 0
    for cell in cells:
        text = cell.get_text(' ', strip=True)
        if 'Confidence:' in text:
            match = re.search(r'(\d+)', text)
            if match:
                confidence_value = int(match.group(1))
            break

    date_str = default_date
    audio_tag = row.find('audio')
    if audio_tag and audio_tag.has_attr('src'):
        for part in audio_tag['src'].split('/'):
            if re.match(r'\d{4}-\d{2}-\d{2}', part):
                date_str = part
                break

    time_raw = f"{date_str} {time_text}".strip()

    return {
        "name": species_name,
        "time_raw": time_raw,
        "confidence_value": confidence_value,
        "image_url": image_url,
        "copyright": "",
        "is_new_species": False
    }

def get_today_detection_count(species_name, today_str, stats_url):
    """Fetch today's detection count for a species from BirdNET-Pi stats endpoint."""
    if not species_name or not stats_url:
        return 0

    cache_key = (species_name.lower(), today_str)
    if cache_key in DAILY_DETECTION_CACHE:
        return DAILY_DETECTION_CACHE[cache_key]

    params = {
        'comname': species_name,
        'date': today_str
    }
    try:
        url = f"{stats_url}?comname={quote(species_name)}&date={today_str}"
        response = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=5)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            for entry in payload:
                if entry.get('date') == today_str:
                    count = int(entry.get('count', 0))
                    DAILY_DETECTION_CACHE[cache_key] = count
                    return count
        DAILY_DETECTION_CACHE[cache_key] = 0
        return 0
    except (requests.exceptions.RequestException, ValueError, json.JSONDecodeError):
        DAILY_DETECTION_CACHE[cache_key] = 0
        return 0

# --- Core Data Fetching Logic ---
def get_cached_image(species_name):
    species_folder_name = "".join(c for c in species_name if c.isalnum() or c in ' _').rstrip().replace(' ', '_')
    species_dir = os.path.join(CACHE_DIRECTORY, species_folder_name)
    if os.path.isdir(species_dir):
        images = sorted([f for f in os.listdir(species_dir) if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
        if not images: return None
        chosen_image = random.choice(images)
        attr_path = os.path.join(species_dir, f"{os.path.splitext(chosen_image)[0]}.txt")
        copyright_info = ""
        if os.path.exists(attr_path):
            with open(attr_path, 'r', encoding='utf-8') as f: copyright_info = f.read().strip()
        image_url = url_for('static', filename=os.path.join(os.path.basename(CACHE_DIRECTORY), species_folder_name, chosen_image).replace('\\', '/'))
        return {"image_url": image_url, "copyright": copyright_info}
    return None

def get_offline_fallback_data():
    print("[INFO] Loading data from local cache.")
    species_list = load_species_from_file(SPECIES_FILE)
    if not species_list: return []
    fallback_data = []
    num_to_sample = min(len(species_list), 4)
    sampled_species = random.sample(species_list, num_to_sample)
    for common_name, scientific_name in sampled_species:
        cached_asset = get_cached_image(common_name)
        if cached_asset:
            fallback_data.append({
                "name": common_name, "time_display": "Offline", "confidence": "0%",
                "confidence_value": 0, "image_url": cached_asset['image_url'],
                "copyright": cached_asset['copyright'], "time_raw": "", "is_offline": True,
                "detections_today": 0
            })
    return fallback_data

def _fetch_bird_data_from_source():
    today_str = datetime.now().strftime("%Y-%m-%d")
    list_url = build_birdnet_pi_list_url()
    stats_url = build_birdnet_pi_stats_url()
    if not list_url:
        print("[INFO] BirdNET-Pi base URL not configured. Waiting for setup.")
        return [], True
    try:
        response = requests.get(list_url, headers=HEADERS, proxies=PROXIES, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.select('tr.relative')
        if not rows:
            return get_offline_fallback_data(), True

        all_parsed = []
        for row in rows:
            parsed = parse_birdnet_pi_row(row, today_str)
            if parsed:
                all_parsed.append(parsed)

        if not all_parsed:
            return get_offline_fallback_data(), True

        for bird in all_parsed:
            if bird.get('is_new_species', False):
                add_pinned_species(bird['name'])

        active_pinned = get_active_pinned_species()

        deduped_by_species = {}
        for bird in all_parsed:
            name = bird.get('name') or 'Unknown Species'
            detected_at = parse_detection_datetime(bird.get('time_raw'))
            bird['_detected_at'] = detected_at
            bird['is_pinned'] = name in active_pinned
            existing = deduped_by_species.get(name)
            if existing is None or detected_at > existing.get('_detected_at', datetime.min):
                deduped_by_species[name] = bird

        unique_birds = sorted(
            deduped_by_species.values(),
            key=lambda d: d.get('_detected_at', datetime.min),
            reverse=True
        )

        if not unique_birds:
            return get_offline_fallback_data(), True

        for bird in unique_birds:
            bird['detections_today'] = get_today_detection_count(bird['name'], today_str, stats_url)

        for bird in unique_birds:
            if bird.get('image_url'):
                if not check_image_url_fast(bird['image_url']):
                    cached_asset = get_cached_image(bird['name'])
                    if cached_asset:
                        bird['image_url'] = cached_asset['image_url']
                        bird['copyright'] = cached_asset['copyright']
            else:
                cached_asset = get_cached_image(bird['name'])
                if cached_asset:
                    bird['image_url'] = cached_asset['image_url']
                    bird['copyright'] = cached_asset['copyright']

        for bird in unique_birds:
            bird.pop('_detected_at', None)

        new_id = "-".join([f"{d['name']}_{d['time_raw']}" for d in unique_birds])

        if new_id == DETECTION_CACHE["id"]:
            data_to_process = DETECTION_CACHE["raw_data"]
        else:
            DETECTION_CACHE["raw_data"] = unique_birds
            DETECTION_CACHE["id"] = new_id
            data_to_process = unique_birds

        display_data = []
        for bird in data_to_process:
            bird_display_copy = bird.copy()
            bird_display_copy['time_display'] = format_seconds_ago(parse_absolute_time_to_seconds_ago(bird['time_raw']))
            bird_display_copy['confidence'] = f"{bird['confidence_value']}%"
            bird_display_copy['detections_today'] = bird.get('detections_today', 0)
            display_data.append(bird_display_copy)

        return display_data, False
    except requests.exceptions.RequestException:
        print("[INFO] BirdNET-Pi endpoint unavailable, using offline mode")
        return get_offline_fallback_data(), True


def get_bird_data(force_refresh=False):
    """Return cached bird data, refreshing from BirdNET-Pi when stale."""
    if not is_birdnet_configured():
        return [], True
    now = datetime.now()
    with BIRD_DATA_CACHE_LOCK:
        cache_age = (now - BIRD_DATA_CACHE["fetched_at"]).total_seconds()
        cache_valid = (
            BIRD_DATA_CACHE["data"]
            and cache_age < BIRD_DATA_CACHE_TTL_SECONDS
            and not force_refresh
        )
        if cache_valid:
            return BIRD_DATA_CACHE["data"], BIRD_DATA_CACHE["api_is_down"]

        if BIRD_DATA_CACHE["refresh_in_progress"]:
            # Another request is already refreshing; serve the last cached payload.
            return BIRD_DATA_CACHE["data"], BIRD_DATA_CACHE["api_is_down"]

        previous_data = BIRD_DATA_CACHE["data"]
        previous_status = BIRD_DATA_CACHE["api_is_down"]
        BIRD_DATA_CACHE["refresh_in_progress"] = True

    bird_data = previous_data
    api_is_down = previous_status
    try:
        fetched_data, fetched_status = _fetch_bird_data_from_source()
        bird_data, api_is_down = fetched_data, fetched_status
    except Exception as exc:
        print(f"[ERROR] Failed to fetch bird data: {exc}")
        if not bird_data:
            bird_data, api_is_down = get_offline_fallback_data(), True
    finally:
        with BIRD_DATA_CACHE_LOCK:
            BIRD_DATA_CACHE.update({
                "data": bird_data,
                "api_is_down": api_is_down,
                "fetched_at": datetime.now(),
                "refresh_in_progress": False
            })

    return bird_data, api_is_down

# --- Flask Routes ---
@app.route('/')
def index():
    needs_setup = not is_birdnet_configured()
    if needs_setup:
        bird_data, api_is_down = [], True
    else:
        bird_data, api_is_down = get_bird_data()
    if not os.path.exists('static'): os.makedirs('static')
    template_path = 'index.html'
    if not os.path.exists(os.path.join('static', template_path)):
         with open(os.path.join('static', template_path), 'w') as f:
              f.write('<h1>Template file not found. Please create an index.html file.</h1>')
    refresh_interval = 30 if api_is_down else 5
    server_url = get_qr_target_url()
    display_url = build_display_access_url()
    config_version = int(CONFIG.get('config_version', 0))
    return render_template(
        template_path, birds=bird_data, refresh_interval=refresh_interval,
        api_is_down=api_is_down, server_url=server_url, requires_setup=needs_setup,
        display_url=display_url, config_version=config_version
    )

@app.route('/data')
def data():
    force_refresh = request.args.get('force') == '1'
    needs_setup = not is_birdnet_configured()
    if needs_setup:
        return jsonify({
            'birds': [],
            'api_is_down': True,
            'requires_setup': True,
            'config_version': int(CONFIG.get('config_version', 0))
        })
    bird_data, api_is_down = get_bird_data(force_refresh=force_refresh)
    return jsonify({
        'birds': bird_data,
        'api_is_down': api_is_down,
        'requires_setup': False,
        'config_version': int(CONFIG.get('config_version', 0))
    })

@app.route('/api/config/base_url', methods=['POST'])
def update_base_url():
    payload = request.get_json(silent=True) or {}
    base_url = (payload.get('base_url') or '').strip()
    if not base_url:
        return jsonify({'status': 'error', 'message': 'Base URL is required.'}), 400
    try:
        normalized = set_birdnet_base_url(base_url)
        return jsonify({'status': 'success', 'base_url': normalized, 'requires_setup': False})
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500

@app.route('/debug/bird_data')
def debug_bird_data():
    bird_data, api_is_down = get_bird_data()
    payload = {
        'generated_at': datetime.now().isoformat(),
        'api_is_down': api_is_down,
        'count': len(bird_data),
        'birds': bird_data
    }
    pretty = json.dumps(payload, indent=2)
    html = [
        "<html><head><title>Bird Data Debug</title>",
        "<style>body{font-family:monospace;background:#111;color:#eee;padding:1rem;}pre{white-space:pre-wrap;word-break:break-word;}</style>",
        "</head><body>",
        "<h1>Current Bird Data</h1>",
        f"<p>API status: {'offline' if api_is_down else 'online'} | Items: {len(bird_data)}</p>",
        f"<pre>{pretty}</pre>",
        "</body></html>"
    ]
    return "".join(html)

@app.route('/shutdown', methods=['POST'])
def shutdown():
    shutdown_func = request.environ.get('werkzeug.server.shutdown')
    if shutdown_func:
        print("Shutdown request received. Shutting down server...")
        shutdown_func()
        return 'Server is shutting down...'
    else:
        print('Error: Not running with the Werkzeug Server. Cannot shut down.')
        return 'Server not running with Werkzeug.', 500

@app.route('/brightness', methods=['POST'])
def set_brightness():
    try:
        brightness = request.json.get('brightness')
        if brightness is not None and 0 <= int(brightness) <= 255:
            command = f"echo {brightness} | sudo tee /sys/class/backlight/10-0045/brightness"
            print(f"Executing brightness command: {command}")
            os.system(command)
            return jsonify({'status': 'success', 'brightness': brightness})
        return jsonify({'status': 'error', 'message': 'Invalid brightness value'}), 400
    except Exception as e:
        print(f"Error setting brightness: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/reboot', methods=['POST'])
def reboot_system():
    print("Executing reboot command...")
    os.system('sudo reboot')
    return jsonify({'status': 'rebooting'})

@app.route('/poweroff', methods=['POST'])
def poweroff_system():
    print("Executing power off command...")
    os.system('sudo poweroff')
    return jsonify({'status': 'shutting down'})

@app.route('/api/pinned_species')
def get_pinned_species():
    """Return list of currently pinned species with time remaining."""
    active_pinned = get_active_pinned_species()
    now = datetime.now()
    result = []

    for species_name, data in active_pinned.items():
        pinned_until = datetime.fromisoformat(data['pinned_until'])
        time_remaining = pinned_until - now
        hours_remaining = int(time_remaining.total_seconds() / 3600)

        result.append({
            'name': species_name,
            'hours_remaining': hours_remaining,
            'pinned_until': data['pinned_until']
        })

    return jsonify(result)

@app.route('/api/dismiss_pinned/<species_name>', methods=['POST'])
def dismiss_pinned(species_name):
    """Dismiss a pinned species."""
    success = dismiss_pinned_species(species_name)
    if success:
        return jsonify({'status': 'success', 'message': f'{species_name} dismissed'})
    else:
        return jsonify({'status': 'error', 'message': f'{species_name} not found in pinned list'}), 404

@app.route('/api/dismiss_all_pinned', methods=['POST'])
def dismiss_all_pinned():
    """Dismiss all pinned species."""
    try:
        pinned = load_pinned_species()
        for species_name in pinned:
            pinned[species_name]['dismissed'] = True
        save_pinned_species(pinned)
        return jsonify({'status': 'success', 'message': 'All pinned species dismissed'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# --- Main Execution ---
if __name__ == '__main__':
    if '--build-cache' in sys.argv:
        print("To build the cache, please run 'python cache_builder.py' directly.")
        sys.exit()
    
    print(f"Starting Flask server on http://0.0.0.0:{SERVER_PORT}")
    app.run(host='0.0.0.0', port=SERVER_PORT)