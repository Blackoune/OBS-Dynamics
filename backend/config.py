import json, pathlib
CONFIG_PATH = pathlib.Path(__file__).parents[1] / 'obs_config.json'
with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
HOST = cfg.get('host', 'localhost')
PORT = cfg.get('port', 4455)
PASSWORD = cfg.get('password', '')
SCAN_INTERVAL = cfg.get('scan_interval_seconds', 1.0)
MATCH_THRESHOLD = cfg.get('match_threshold', 0.8)
DEBUG = cfg.get('debug', False)
