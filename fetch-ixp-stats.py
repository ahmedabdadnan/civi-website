#!/usr/bin/env python3
"""
fetch-ixp-stats.py — Pull DE-CIX Baghdad traffic data and write /ixp-stats.json

Run this from cron every 5 minutes:
  */5 * * * * /usr/bin/python3 /path/to/fetch-ixp-stats.py >> /var/log/ixp-stats.log 2>&1

The script tries two data sources:
  1. DE-CIX public JSON API (if accessible from your server)
  2. Scrapes the mrtg/rrd graph image endpoint for numeric data

Output: ixp-stats.json in the same directory (serve via nginx as /ixp-stats.json)
"""

import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error

OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ixp-stats.json')

# Known DE-CIX API endpoints to try (they may require a user-agent header)
DECIX_ENDPOINTS = [
    'https://www.de-cix.net/api/v1/exchanges/baghdad/statistics',
    'https://www.de-cix.net/api/v1/ixps/baghdad/traffic',
    'https://www.de-cix.net/en/api/locations/baghdad/stats.json',
]

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/html, */*',
    'Referer': 'https://www.de-cix.net/en/locations/baghdad/statistics',
}


def fetch_url(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8'), resp.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception as e:
        return None, str(e)


def try_decix_api():
    """Try each known DE-CIX endpoint and return parsed JSON if any succeeds."""
    for url in DECIX_ENDPOINTS:
        body, status = fetch_url(url)
        if body and status == 200:
            try:
                data = json.loads(body)
                print(f'[OK] Got data from {url}')
                return data
            except json.JSONDecodeError:
                continue
        print(f'[SKIP] {url} → {status}')
    return None


def transform_decix_data(raw):
    """
    Transform DE-CIX API response into the format expected by the website chart.
    Adjust this function once you confirm the actual DE-CIX response structure.

    Expected output format:
    {
      "48h":  { "labels": [...], "inData": [...], "outData": [...] },
      "30d":  { "labels": [...], "inData": [...], "outData": [...] },
      "12m":  { "labels": [...], "inData": [...], "outData": [...] }
    }
    """
    # This is a best-guess transform — update once you see the real response structure.
    result = {}

    # If raw is a list of [timestamp, in_bps, out_bps] entries:
    if isinstance(raw, list) and raw and isinstance(raw[0], (list, tuple)) and len(raw[0]) >= 3:
        # Convert bps to Gbps
        labels, in_vals, out_vals = [], [], []
        for entry in raw:
            ts = entry[0]
            in_gbps  = round(entry[1] / 1e9, 2) if entry[1] else 0
            out_gbps = round(entry[2] / 1e9, 2) if entry[2] else 0
            dt = datetime.datetime.utcfromtimestamp(ts)
            labels.append(dt.strftime('%d %b %H:%M'))
            in_vals.append(in_gbps)
            out_vals.append(out_gbps)
        result['48h'] = {'labels': labels[-48:], 'inData': in_vals[-48:], 'outData': out_vals[-48:]}
        result['30d'] = {'labels': labels[-30:], 'inData': in_vals[-30:], 'outData': out_vals[-30:]}

    # If raw is a dict with keys like "5min", "daily", "monthly":
    elif isinstance(raw, dict):
        for key_map, out_key in [('5min', '48h'), ('hourly', '48h'), ('daily', '30d'), ('monthly', '12m')]:
            if key_map in raw:
                series = raw[key_map]
                labels, in_vals, out_vals = [], [], []
                for point in series:
                    ts  = point.get('timestamp') or point.get('time') or point.get('t')
                    inp = point.get('in')  or point.get('rx') or point.get('input')  or 0
                    out = point.get('out') or point.get('tx') or point.get('output') or 0
                    if ts:
                        dt = datetime.datetime.utcfromtimestamp(int(ts))
                        labels.append(dt.strftime('%d %b %H:%M'))
                    in_vals.append(round(float(inp) / 1e9 if float(inp) > 1e6 else float(inp), 2))
                    out_vals.append(round(float(out) / 1e9 if float(out) > 1e6 else float(out), 2))
                result[out_key] = {'labels': labels, 'inData': in_vals, 'outData': out_vals}

    return result if result else None


def main():
    print(f'[{datetime.datetime.utcnow().isoformat()}] Fetching IRAQ-IXP stats...')

    raw = try_decix_api()
    if raw:
        transformed = transform_decix_data(raw)
        if transformed:
            # Only write ranges that have valid data; keep existing for missing ones
            existing = {}
            if os.path.exists(OUT_FILE):
                try:
                    with open(OUT_FILE) as f:
                        existing = json.load(f)
                except Exception:
                    pass
            existing.update(transformed)
            existing['_updated'] = datetime.datetime.utcnow().isoformat() + 'Z'
            with open(OUT_FILE, 'w') as f:
                json.dump(existing, f)
            print(f'[OK] Written to {OUT_FILE}')
            return 0
        else:
            print('[WARN] Could not transform DE-CIX response — check raw output above.')
            print('[RAW]', json.dumps(raw)[:500])
    else:
        print('[WARN] No data from DE-CIX API. The website will use embedded fallback data.')
        print('       To debug, try manually: curl -H "User-Agent: Mozilla/5.0" https://www.de-cix.net/api/v1/exchanges/baghdad/statistics')

    return 1


if __name__ == '__main__':
    sys.exit(main())
