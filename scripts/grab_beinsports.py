"""Export official beIN SPORTS schedules across MENA and international regions via Opta API."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import time
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

# Verified canonical XMLTV mappings for Opta UUIDs where upstream iptv-org XML is
# blank, uses cryptic provider codes (BEINC/BEINSSC), or assigns a .qa ID to US/SG feeds.
BUILTIN_CANONICAL_OVERRIDES = {
    '84f7e0c1-d47a-4444-bba8-c50bb8d2c5a8': ('beINSports.qa@MENA', 'beIN SPORTS', 'ar'),
    '7836fea9-6b39-4a1a-8352-dc5fcb97a16c': ('beINSports1.qa@MENA', 'beIN SPORTS 1', 'ar'),
    '5c08d9d3-c713-4f1f-947e-87c761428b9b': ('beINSports9.qa@MENA', 'beIN SPORTS 9', 'ar'),
    '5ed9e1c4-bef4-4ac2-8f90-28b3dca26375': ('beINSportsCanada.ca@SD', 'beIN SPORTS Canada', 'en'),
    '5824c394-7211-4004-ac46-35bd58b9d1ee': ('beINSportsenEspanolCanada.ca@SD', 'beIN SPORTS en Español Canada', 'es'),
    '520d107a-71d4-4cb7-b141-1c6b089a7ffc': ('beINSports8Canada.ca@SD', 'beIN SPORTS 8 Canada', 'en'),
    'c244c48d-3b54-406a-94c9-d63b16318267': ('beINSportsUSA.us@SD', 'beIN SPORTS', 'en'),
    '1acadcf1-dfac-480b-872c-53d51fe1b45d': ('beINSPORTSXTRA.us@SD', 'beIN SPORTS XTRA', 'en'),
    '5223e3d7-fb4e-4155-885a-9165996b22fa': ('beINSPORTSXTRAenEspanol.us@SD', 'beIN SPORTS XTRA ñ', 'es'),
    '4083661e-f588-4700-943c-8fdf65f1b5eb': ('beINSportsenEspanol.us@HD', 'beIN SPORTS en Español', 'es'),
    '90e69fc7-aa8c-40f2-b35f-ebd174495f76': ('beINSports3.us@SD', 'beIN SPORTS 3', 'en'),
    'd0546ed7-9db2-4924-9e32-c4f077e7bfc7': ('beINSports4.us@SD', 'beIN SPORTS 4', 'en'),
    'a5a48db1-c00b-4db9-9fc6-5e5f25c18830': ('beINSports5.us@SD', 'beIN SPORTS 5', 'en'),
    'e56b0905-f99f-4db1-931e-e6002b530867': ('beINSports6.us@SD', 'beIN SPORTS 6', 'en'),
    '831591c8-da65-4528-b837-0e5a147887fb': ('beINSports7.us@SD', 'beIN SPORTS 7', 'en'),
    '9a424246-ec89-43c3-9239-aa3a40540f94': ('beINSports8.us@SD', 'beIN SPORTS 8', 'en'),
    '164c0eda-ebce-4aa6-9dda-d603e0948b9f': ('beINSports1.qa@Australia', 'beIN SPORTS 1', 'en'),
    '63a30d9c-609d-4a30-8730-2ca391df287a': ('beINSports2.qa@Australia', 'beIN SPORTS 2', 'en'),
    '2e8a6222-8d90-4291-81e3-af2f72557746': ('beINSports3.qa@Australia', 'beIN SPORTS 3', 'en'),
    '0fa3def0-234b-4e25-934f-392df2c3162c': ('beINSports1.qa@Malaysia', 'beIN SPORTS 1', 'en'),
}


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


def _channel_country(channel_id):
    base = channel_id.split('@', 1)[0]
    match = re.search(r'\.([a-z]{2})$', base, re.I)
    return match.group(1).lower() if match else ''


def load_canonical_beinsports_map(upstream_dir):
    """Load site_id UUID -> (xmltv_id, display_name, lang) from iptv-org beinsports.com."""
    mapping = {}
    if upstream_dir:
        sites_dir = Path(upstream_dir) / 'sites' / 'beinsports.com'
        if sites_dir.exists():
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
    mapping.update(BUILTIN_CANONICAL_OVERRIDES)
    return mapping


def _get_json(url, retries=2):
    request = Request(
        url,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        },
    )
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=15) as response:
                payload = response.read(MAX_PAYLOAD_BYTES + 1)
            if len(payload) > MAX_PAYLOAD_BYTES:
                raise ValueError('beIN SPORTS response exceeded limit')
            return json.loads(payload)
        except HTTPError as error:
            if attempt >= retries or (error.code < 500 and error.code != 429):
                raise
        except (OSError, URLError):
            if attempt >= retries:
                raise
        time.sleep(0.5 * (attempt + 1))


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


def build_guide_data(channel_event_pairs, now, canonical_map=None, countries=None):
    """Convert (region, channel_dict, events_list) tuples into XMLTV channel/programme dicts."""
    canonical_map = canonical_map or {}
    country_filter = {c.strip().lower() for c in countries if c and str(c).strip()} if countries else None
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
            if (
                api_name
                and api_name.lower() != canon_name.lower()
                and not re.fullmatch(r'BEIN[A-Z0-9]{0,4}C', api_name)
            ):
                names.append(api_name)
        else:
            if not api_name:
                continue
            ext_id = _clean_text(ch.get('externalId')) or _slug(api_name, 16)
            cid = f'beINSports.{reg_tag}.{_slug(ext_id, 18)}.{country}'
            names = [api_name]
            lang = default_lang

        if country_filter and _channel_country(cid) not in country_filter:
            continue

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


def write_guide(output, now=None, upstream=None, regions=None, countries=None):
    now = now or datetime.now(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=2)
    regions = tuple(regions) if regions else DEFAULT_REGIONS
    canonical_map = load_canonical_beinsports_map(upstream)
    country_filter = {c.strip().lower() for c in countries if c and str(c).strip()} if countries else None

    discovered = []
    seen = set()
    for reg in regions:
        try:
            rows = fetch_region_channels(reg)
        except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError):
            continue
        reg_country = REGION_META.get(reg, ('qa', 'en', 'intl'))[0]
        for ch in rows:
            uid = _clean_text(ch.get('id')).lower() if isinstance(ch, dict) else ''
            if not uid or uid in seen:
                continue
            if country_filter:
                mapped = canonical_map.get(uid)
                ch_country = _channel_country(mapped[0]) if mapped else reg_country
                if ch_country not in country_filter:
                    continue
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

    channels, programmes = build_guide_data(
        pairs, now, canonical_map=canonical_map, countries=countries
    )
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
