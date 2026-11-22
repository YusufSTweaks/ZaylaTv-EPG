"""Export official beIN SPORTS schedules across MENA and international regions via Opta API."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


CHANNELS_URL = 'https://www.beinsports.com/api/opta/tv-channel'
EVENTS_URL = 'https://www.beinsports.com/api/opta/tv-event'
XMLTV_TIME = '%Y%m%d%H%M%S %z'
MAX_PAYLOAD_BYTES = 8_000_000

REGION_META = {
    'ar-mena': ('qa', 'ar', 'mena'),
    'en-mena': ('qa', 'en', 'mena'),
    'fr-fr': ('fr', 'fr', 'fr'),
    'en-us': ('us', 'en', 'us'),
    'es-us': ('us', 'es', 'us'),
    'en-au': ('au', 'en', 'au'),
    'en-nz': ('nz', 'en', 'nz'),
    'en-my': ('my', 'en', 'my'),
    'en-hk': ('hk', 'en', 'hk'),
    'en-ph': ('ph', 'en', 'ph'),
}

DEFAULT_REGIONS = ('ar-mena',)


def _clean_text(value):
    if value is None or isinstance(value, bool):
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip()


def _parse_iso(value):
    text = _clean_text(value)
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _slug(value, max_len=24):
    cleaned = re.sub(r'[^a-z0-9]+', '', _clean_text(value).lower())
    return cleaned[:max_len] or 'ch'


def load_canonical_beinsports_map(upstream_dir):
    """Load site_id UUID -> (xmltv_id, display_name, lang) from iptv-org beinsports.com."""
    mapping = {}
    if not upstream_dir:
        return mapping
    sites_dir = Path(upstream_dir) / 'sites' / 'beinsports.com'
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
            lang = _clean_text(ch.get('lang')) or 'en'
            if sid and cid and name and sid.lower() not in mapping:
                mapping[sid.lower()] = (cid, name, lang)
    return mapping


def _get_json(url):
    request = Request(
        url,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        },
    )
    with urlopen(request, timeout=15) as response:
        payload = response.read(MAX_PAYLOAD_BYTES + 1)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError('beIN SPORTS response exceeded limit')
    return json.loads(payload)


def fetch_region_channels(region):
    data = _get_json(f'{CHANNELS_URL}?{urlencode({"region": region})}')
    rows = data.get('rows', []) if isinstance(data, dict) else []
    return rows if isinstance(rows, list) else []


def fetch_channel_events(channel_uuid, start_utc, end_utc):
    params = urlencode({
        'startBefore': end_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'endAfter': start_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'channelIds': channel_uuid,
    })
    data = _get_json(f'{EVENTS_URL}?{params}')
    rows = data.get('rows', []) if isinstance(data, dict) else []
    return rows if isinstance(rows, list) else []


def _extract_event_title(event):
    for key in ('title', 'name', 'programName', 'shortTitle'):
        val = _clean_text(event.get(key))
        if val:
            return val
    # Fallback to nested match/fixture fields if present
    home = _clean_text((event.get('homeTeam') or {}).get('name') if isinstance(event.get('homeTeam'), dict) else '')
    away = _clean_text((event.get('awayTeam') or {}).get('name') if isinstance(event.get('awayTeam'), dict) else '')
    if home and away:
        return f'{home} vs {away}'
    return ''


def _extract_event_desc(event):
    for key in ('description', 'synopsis', 'subtitle'):
        val = _clean_text(event.get(key))
        if val:
            return val
    parts = []
    for key in ('sport', 'competition', 'category'):
        item = event.get(key)
        val = _clean_text(item.get('name') if isinstance(item, dict) else item)
        if val and val not in parts:
            parts.append(val)
    return ' - '.join(parts) or None


def build_guide_data(channel_event_pairs, now, canonical_map=None):
    """Convert (region, channel_dict, events_list) tuples into XMLTV channel/programme dicts."""
    canonical_map = canonical_map or {}
    now_utc = now.astimezone(timezone.utc)
    window_start = now_utc - timedelta(hours=6)
    window_end = now_utc + timedelta(days=2, hours=6)

    channels = {}
    programmes = {}
    seen_uuids = set()

    for region, ch, events in channel_event_pairs:
        if not isinstance(ch, dict) or not isinstance(events, list):
            continue
        uuid = _clean_text(ch.get('id'))
        if not uuid or uuid.lower() in seen_uuids:
            continue
        seen_uuids.add(uuid.lower())

        api_name = _clean_text(ch.get('name'))
        country, default_lang, reg_tag = REGION_META.get(region, ('qa', 'en', 'intl'))

        if uuid.lower() in canonical_map:
            cid, canon_name, lang = canonical_map[uuid.lower()]
            names = [canon_name]
            if api_name and api_name.lower() != canon_name.lower():
                names.append(api_name)
        else:
            if not api_name:
                continue
            ext_id = _clean_text(ch.get('externalId')) or _slug(api_name, 16)
            cid = f'beINSports.{reg_tag}.{_slug(ext_id, 18)}.{country}'
            names = [api_name]
            lang = default_lang

        added = False
        for ev in events:
            if not isinstance(ev, dict):
                continue
            title = _extract_event_title(ev)
            if not title:
                continue
            start = _parse_iso(ev.get('startDate') or ev.get('startTime') or ev.get('start'))
            stop = _parse_iso(ev.get('endDate') or ev.get('endTime') or ev.get('end'))
            if start is None or stop is None or stop <= start:
                continue
            start_u = start.astimezone(timezone.utc)
            stop_u = stop.astimezone(timezone.utc)
            if start_u >= window_end or stop_u <= window_start:
                continue
            desc = _extract_event_desc(ev)
            programmes[(cid, start_u, stop_u)] = (title, lang, desc)
            added = True

        if added and cid not in channels:
            channels[cid] = names

    return channels, programmes


def write_guide(output, now=None, upstream=None, regions=None):
    now = now or datetime.now(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=2)
    regions = tuple(regions) if regions else DEFAULT_REGIONS
    canonical_map = load_canonical_beinsports_map(upstream)

    discovered = []
    seen = set()
    for reg in regions:
        try:
            rows = fetch_region_channels(reg)
        except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError):
            continue
        for ch in rows:
            uid = _clean_text(ch.get('id')).lower() if isinstance(ch, dict) else ''
            if uid and uid not in seen:
                seen.add(uid)
                discovered.append((reg, ch))

    if not discovered:
        raise ValueError(f'beIN SPORTS returned no channels for regions: {", ".join(regions)}')

    pairs = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {
            pool.submit(fetch_channel_events, ch['id'], day_start, day_end): (idx, reg, ch)
            for idx, (reg, ch) in enumerate(discovered)
        }
        ordered = {}
        for future in as_completed(future_map):
            idx, reg, ch = future_map[future]
            try:
                ordered[idx] = (reg, ch, future.result())
            except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError):
                ordered[idx] = (reg, ch, [])
        pairs = [ordered[i] for i in sorted(ordered)]

    channels, programmes = build_guide_data(pairs, now, canonical_map=canonical_map)
    if not any(stop > now_utc for _, _, stop in programmes):
        raise ValueError('beIN SPORTS returned no usable current or upcoming programmes')

    root = ET.Element('tv')
    for cid, names in sorted(channels.items()):
        definition = ET.SubElement(root, 'channel', id=cid)
        for name in names:
            ET.SubElement(definition, 'display-name').text = name
    for (cid, start, stop), (title, lang, desc) in sorted(programmes.items()):
        programme = ET.SubElement(
            root, 'programme', channel=cid,
            start=start.astimezone(timezone.utc).strftime(XMLTV_TIME),
            stop=stop.astimezone(timezone.utc).strftime(XMLTV_TIME),
        )
        ET.SubElement(programme, 'title', lang=lang).text = title
        if desc:
            ET.SubElement(programme, 'desc', lang=lang).text = desc
    ET.ElementTree(root).write(output, encoding='utf-8', xml_declaration=True)
    return len(channels), len(programmes)
