"""Export the public TV Guide schedule across major U.S. national and metro lineups."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE = 'https://backend.tvguide.com/tvschedules/tvguide'
API_KEY = 'DI9elXhZ3bU6ujsA2gXEKOANyncXGUGc'
XMLTV_TIME = '%Y%m%d%H%M%S %z'
MAX_PAYLOAD_BYTES = 16_000_000
WINDOW_MINUTES = 720  # 12 hours per API call
WINDOWS_PER_LINEUP = 4  # 48-hour schedule

# Ordered national cable/satellite lineups + major U.S. broadcast markets.
DEFAULT_LINEUPS = (
    '9100001138',  # US National Standard (matches iptv-org tvguide.com catalog)
    '9133007816',  # DirecTV - New York (national cable, sports, RSNs, movies, news)
    '9100008443',  # Dish - New York (satellite + multicultural/specialty networks)
    '9100002336',  # Spectrum - Manhattan (cable)
    '9100001487',  # Broadcast - New York
    '9100001489',  # Broadcast - Los Angeles
    '9100003756',  # Broadcast - Chicago
    '9100007054',  # Broadcast - Houston
    '9100001520',  # Broadcast - Dallas / Ft Worth
    '9100002397',  # Broadcast - Miami / Ft Lauderdale
    '9100001723',  # Broadcast - Boston
    '9100002366',  # Broadcast - San Francisco / Oakland
    '9100000653',  # Broadcast - Washington D.C.
)

# Canonical ESPN linear IDs so HD simulcasts from DirecTV/Dish never bypass us-espn-official.
CANONICAL_NAME_OVERRIDES = {
    'espn': ('ESPN.us@SD', 'ESPN'),
    'espn2': ('ESPN2.us@SD', 'ESPN2'),
    'espnu': ('ESPNU.us@SD', 'ESPNU'),
    'espnews': ('ESPNews.us@SD', 'ESPNews'),
    'espnnews': ('ESPNews.us@SD', 'ESPNews'),
    'secnetwork': ('SECNetwork.us@SD', 'SEC Network'),
    'accnetwork': ('ACCNetwork.us@SD', 'ACC Network'),
    'espndeportes': ('ESPNDeportes.us@SD', 'ESPN Deportes'),
}

JUNK_PATTERNS = (
    'music choice',
    'siriusxm',
    'pay per view',
    'pay-per-view',
    'ppv',
    'on demand',
    'in demand',
    'directv cinema',
    'dish cinema',
    'directv preview',
    'local access',
    'public access',
    'leased access',
    'program guide',
    'barker',
    'mosaic',
    'multiview',
    'playboy',
    'hustler',
    'brazzers',
    'penthouse',
    'vivid',
    'xtsy',
    'ten+',
    'adult',
    'juicy',
    'real ',
    'stingray',
)

CALLSIGN_RE = re.compile(r'^[KW][A-Z0-9]{2,5}(?:-(?:DT|CD|LD|TV|LP|CA))?\d*$', re.I)


def _clean_text(value):
    if value is None or isinstance(value, bool):
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip()


def _is_junk_channel(full_name, short_name):
    combined = f'{full_name} {short_name}'.lower()
    if not combined.strip():
        return True
    if any(token in combined for token in JUNK_PATTERNS):
        return True
    if re.search(r'\b(?:ppv|vod|ondmd|mc\d{2})\b', combined):
        return True
    return False


def _normalize_dedup_key(full_name, short_name):
    """Build a simulcast deduplication key that collapses HD/SD copies while keeping
    local call signs, East/West feeds, and distinct networks separate."""
    short_clean = _clean_text(short_name)
    full_clean = _clean_text(full_name)
    if CALLSIGN_RE.fullmatch(short_clean):
        return 'call:' + re.sub(r'[^a-z0-9]+', '', short_clean.lower())
    base = full_clean or short_clean
    # Strip trailing HD/SD/FHD/UHD/4K/TV markers for simulcast deduplication.
    base = re.sub(r'\s*\((?:hd|sd|fhd|uhd|4k)\)\s*$', '', base, flags=re.I)
    base = re.sub(r'(?:\s+|\b)(?:fhd|uhd|hd|sd|4k)\b', '', base, flags=re.I)
    base = re.sub(r'\s+', ' ', base).strip()
    key = re.sub(r'[^a-z0-9]+', '', base.lower())
    return key or re.sub(r'[^a-z0-9]+', '', short_clean.lower())


def load_canonical_tvguide_map(upstream_dir):
    """Load sourceId -> (xmltv_id, display_name) from iptv-org tvguide.com channels."""
    mapping = {}
    if not upstream_dir:
        return mapping
    sites_dir = Path(upstream_dir) / 'sites' / 'tvguide.com'
    if not sites_dir.exists():
        return mapping
    for path in sorted(sites_dir.glob('*.channels.xml')):
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        for ch in root.findall('channel'):
            sid = _clean_text(ch.get('site_id'))
            cid = _clean_text(ch.get('xmltv_id'))
            name = _clean_text(ch.text)
            if sid and cid and name and sid not in mapping:
                mapping[sid] = (cid, name)
    return mapping


def load_canonical_us_name_map(upstream_dir):
    """Load normalized US national channel name -> (xmltv_id, display_name) from iptv-org catalogs."""
    mapping = {}
    if not upstream_dir:
        return mapping
    sites_root = Path(upstream_dir) / 'sites'
    if not sites_root.exists():
        return mapping
    globs = (
        'tvguide.com/*.channels.xml',
        'tvpassport.com/*.channels.xml',
        'directv.com/*.channels.xml',
        'ontvtonight.com/ontvtonight.com_us.channels.xml',
    )
    for pattern in globs:
        for path in sorted(sites_root.glob(pattern)):
            try:
                root = ET.parse(path).getroot()
            except (OSError, ET.ParseError):
                continue
            for ch in root.findall('channel'):
                cid = _clean_text(ch.get('xmltv_id'))
                name = _clean_text(ch.text)
                if not cid or not name:
                    continue
                if not cid.split('@', 1)[0].lower().endswith('.us'):
                    continue
                # Never map an unqualified national name onto a West/Pacific-only ID.
                if ('@west' in cid.lower() or 'pacific' in cid.lower()) and not re.search(r'\b(west|pacific)\b', name, re.I):
                    continue
                dkey = _normalize_dedup_key(name, '')
                if not dkey or len(dkey) < 3 or dkey.startswith('call:'):
                    continue
                if dkey not in mapping:
                    mapping[dkey] = (cid, name)
    return mapping


def fetch_window(provider_id, start_ts, duration=WINDOW_MINUTES):
    params = urlencode({
        'start': int(start_ts),
        'duration': int(duration),
        'apiKey': API_KEY,
    })
    url = f'{API_BASE}/{provider_id}/web?{params}'
    request = Request(
        url,
        headers={
            'Accept': 'application/json',
            'Referer': 'https://www.tvguide.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        },
    )
    with urlopen(request, timeout=20) as response:
        payload = response.read(MAX_PAYLOAD_BYTES + 1)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f'TV Guide response exceeded {MAX_PAYLOAD_BYTES // 1_000_000} MB')
    parsed = json.loads(payload)
    items = parsed.get('data', {}).get('items') if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise ValueError(f'Expected TV Guide items list for provider {provider_id}')
    return items


def build_guide_data(lineup_batches, now, canonical_map=None, canonical_name_map=None, max_channels=None):
    """Merge multi-lineup TV Guide schedule items into deduplicated XMLTV structures."""
    canonical_map = canonical_map or {}
    canonical_name_map = canonical_name_map or {}
    window_start = now.astimezone(timezone.utc) - timedelta(hours=6)
    window_end = now.astimezone(timezone.utc) + timedelta(hours=54)

    # Track dedup_key -> channel_id so HD/SD copies collapse to one canonical channel.
    dedup_to_cid = {}
    for sid, (cid, cname) in canonical_map.items():
        dkey = _normalize_dedup_key(cname, '')
        if dkey and dkey not in dedup_to_cid:
            dedup_to_cid[dkey] = cid
    for name_key, (cid, _) in CANONICAL_NAME_OVERRIDES.items():
        dedup_to_cid[name_key] = cid

    source_to_cid = {}
    channels = {}  # cid -> [primary_display_name, optional_alias...]
    programmes = {}  # (cid, start, stop) -> title
    skipped_junk = 0
    deduplicated_simulcasts = 0

    for items in lineup_batches:
        for entry in items:
            if not isinstance(entry, dict):
                continue
            ch = entry.get('channel')
            sched = entry.get('programSchedules')
            if not isinstance(ch, dict) or not isinstance(sched, list) or not sched:
                continue
            sid = _clean_text(ch.get('sourceId'))
            if not sid:
                continue
            full_name = _clean_text(ch.get('fullName'))
            short_name = _clean_text(ch.get('name'))

            cid = source_to_cid.get(sid)
            if cid is None:
                if sid in canonical_map:
                    cid, canon_name = canonical_map[sid]
                    names = [canon_name]
                    if full_name and full_name.lower() != canon_name.lower():
                        names.append(full_name)
                    dkey = _normalize_dedup_key(canon_name, short_name)
                    if dkey:
                        dedup_to_cid[dkey] = cid
                else:
                    if _is_junk_channel(full_name, short_name):
                        skipped_junk += 1
                        continue
                    dkey = _normalize_dedup_key(full_name, short_name)
                    if dkey in CANONICAL_NAME_OVERRIDES:
                        cid, canon_name = CANONICAL_NAME_OVERRIDES[dkey]
                        if cid in channels:
                            deduplicated_simulcasts += 1
                            source_to_cid[sid] = cid
                            continue
                        names = [canon_name]
                    elif dkey and dkey in dedup_to_cid:
                        # Skip HD/SD simulcast when the primary/canonical channel already exists.
                        existing_cid = dedup_to_cid[dkey]
                        if existing_cid in channels:
                            deduplicated_simulcasts += 1
                            source_to_cid[sid] = ''
                            continue
                        cid = existing_cid
                        names = [full_name or short_name]
                    elif (
                        dkey
                        and not dkey.startswith('call:')
                        and not re.search(r'\b(west|pacific)\b', f'{full_name} {short_name}', re.I)
                        and dkey in canonical_name_map
                    ):
                        cid, canon_name = canonical_name_map[dkey]
                        if cid in channels:
                            deduplicated_simulcasts += 1
                            source_to_cid[sid] = ''
                            continue
                        names = [canon_name]
                        if full_name and full_name.lower() != canon_name.lower():
                            names.append(full_name)
                        dedup_to_cid[dkey] = cid
                    else:
                        if max_channels is not None and len(channels) >= max_channels:
                            continue
                        cid = f'TVGuide.{sid}.us'
                        if CALLSIGN_RE.fullmatch(short_name):
                            names = [short_name]
                            if full_name and full_name.lower() != short_name.lower():
                                names.append(full_name)
                        else:
                            primary = full_name or short_name
                            if not primary:
                                continue
                            names = [primary]
                        if dkey:
                            dedup_to_cid[dkey] = cid
                source_to_cid[sid] = cid
                if cid not in channels:
                    channels[cid] = names

            if not cid:
                continue

            for prog in sched:
                if not isinstance(prog, dict):
                    continue
                title = _clean_text(prog.get('title'))
                if not title:
                    continue
                st_raw = prog.get('startTime')
                en_raw = prog.get('endTime')
                if not isinstance(st_raw, (int, float)) or not isinstance(en_raw, (int, float)):
                    continue
                if isinstance(st_raw, bool) or isinstance(en_raw, bool) or en_raw <= st_raw:
                    continue
                start = datetime.fromtimestamp(st_raw, tz=timezone.utc)
                stop = datetime.fromtimestamp(en_raw, tz=timezone.utc)
                if start >= window_end or stop <= window_start:
                    continue
                programmes[(cid, start, stop)] = title

    # Keep only channels that have at least one programme.
    active_cids = {cid for cid, _, _ in programmes}
    channels = {cid: names for cid, names in channels.items() if cid in active_cids}
    stats = {
        'channels': len(channels),
        'programmes': len(programmes),
        'skipped_junk': skipped_junk,
        'deduplicated_simulcasts': deduplicated_simulcasts,
    }
    return channels, programmes, stats


def write_guide(output, now=None, upstream=None, lineups=None, max_channels=None):
    now = now or datetime.now(timezone.utc)
    base_ts = int(now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0).timestamp())
    lineups = tuple(lineups) if lineups else DEFAULT_LINEUPS
    canonical_map = load_canonical_tvguide_map(upstream)
    canonical_name_map = load_canonical_us_name_map(upstream)

    tasks = []
    for idx, pid in enumerate(lineups):
        for w in range(WINDOWS_PER_LINEUP):
            tasks.append((idx, w, pid, base_ts + w * WINDOW_MINUTES * 60))

    results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {
            pool.submit(fetch_window, pid, start_ts): (idx, w, pid)
            for idx, w, pid, start_ts in tasks
        }
        for future in as_completed(future_map):
            idx, w, pid = future_map[future]
            try:
                results[(idx, w)] = future.result()
            except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError) as err:
                errors.append(f'{pid}[{w}]: {err}')

    ordered_batches = [results[key] for key in sorted(results)]
    if not ordered_batches:
        raise ValueError(f'TV Guide fetched no lineup data: {"; ".join(errors[:5])}')

    channels, programmes, stats = build_guide_data(
        ordered_batches,
        now,
        canonical_map=canonical_map,
        canonical_name_map=canonical_name_map,
        max_channels=max_channels,
    )
    if not any(stop > now for _, _, stop in programmes):
        raise ValueError('TV Guide returned no usable current or upcoming programmes')

    root = ET.Element('tv')
    for cid, names in sorted(channels.items()):
        definition = ET.SubElement(root, 'channel', id=cid)
        for name in names:
            ET.SubElement(definition, 'display-name').text = name
    for (cid, start, stop), title in sorted(programmes.items()):
        programme = ET.SubElement(
            root, 'programme', channel=cid,
            start=start.astimezone(timezone.utc).strftime(XMLTV_TIME),
            stop=stop.astimezone(timezone.utc).strftime(XMLTV_TIME),
        )
        ET.SubElement(programme, 'title', lang='en').text = title
    ET.ElementTree(root).write(output, encoding='utf-8', xml_declaration=True)
    stats['lineup_errors'] = len(errors)
    return len(channels), len(programmes), stats
