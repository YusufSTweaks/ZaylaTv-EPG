"""Export the public DStv South Africa schedule (all ~149 channels incl. SABC 1/2/3/News)."""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


API_URL = 'https://www.dstv.com/umbraco/api/TvGuide/GetProgrammes'
SAST = ZoneInfo('Africa/Johannesburg')
XMLTV_TIME = '%Y%m%d%H%M%S %z'
MAX_PAYLOAD_BYTES = 16_000_000


def _clean_text(value):
    if value is None or isinstance(value, bool):
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip()


def _parse_local(value):
    text = _clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SAST)
    return parsed


def load_canonical_dstv_za_map(upstream_dir):
    """Load DStv ZA channel number -> (xmltv_id, display_name) from iptv-org catalog."""
    mapping = {}
    if not upstream_dir:
        return mapping
    path = Path(upstream_dir) / 'sites' / 'dstv.com' / 'dstv.com_za.channels.xml'
    if not path.exists():
        return mapping
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return mapping
    for ch in root.findall('channel'):
        site_id = _clean_text(ch.get('site_id'))
        cid = _clean_text(ch.get('xmltv_id'))
        name = _clean_text(ch.text)
        if not site_id or not cid or not name:
            continue
        number = site_id.split('#', 1)[1] if '#' in site_id else site_id
        if number and number not in mapping:
            mapping[number] = (cid, name)
    return mapping


def fetch_day(day):
    params = urlencode({'d': day.strftime('%Y-%m-%d'), 'country': 'zaf'})
    request = Request(
        f'{API_URL}?{params}',
        headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        },
    )
    with urlopen(request, timeout=20) as response:
        payload = response.read(MAX_PAYLOAD_BYTES + 1)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError('DStv ZA schedule exceeds 16 MB')
    parsed = json.loads(payload)
    channels = parsed.get('Channels') if isinstance(parsed, dict) else None
    if not isinstance(channels, list):
        raise ValueError('Expected Channels list from DStv ZA API')
    return channels


def build_guide_data(day_payloads, now, canonical_map=None):
    canonical_map = canonical_map or {}
    day_start = now.astimezone(SAST).replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = day_start + timedelta(days=2, hours=6)

    channels = {}
    programmes = {}
    for payload in day_payloads:
        for ch in payload:
            if not isinstance(ch, dict):
                continue
            number = _clean_text(ch.get('Number'))
            api_name = _clean_text(ch.get('Name'))
            progs = ch.get('Programmes')
            if not number or not api_name or not isinstance(progs, list):
                continue
            if number in canonical_map:
                cid, canon_name = canonical_map[number]
                names = [canon_name]
                if api_name.lower() != canon_name.lower():
                    names.append(api_name)
            else:
                cid = f'DStvZA.{number}.za'
                names = [api_name]

            added = False
            for prog in progs:
                if not isinstance(prog, dict):
                    continue
                title = _clean_text(prog.get('Title'))
                if not title:
                    continue
                start = _parse_local(prog.get('StartTime'))
                stop = _parse_local(prog.get('EndTime'))
                if start is None or stop is None or stop <= start:
                    continue
                if start >= window_end or stop <= day_start:
                    continue
                programmes[(cid, start.astimezone(timezone.utc), stop.astimezone(timezone.utc))] = title
                added = True
            if added and cid not in channels:
                channels[cid] = names

    return channels, programmes


def write_guide(output, now=None, upstream=None):
    now = now or datetime.now(SAST)
    day = now.astimezone(SAST).replace(hour=0, minute=0, second=0, microsecond=0)
    canonical_map = load_canonical_dstv_za_map(upstream)
    payloads = [fetch_day(day), fetch_day(day + timedelta(days=1))]
    channels, programmes = build_guide_data(payloads, now, canonical_map=canonical_map)

    if not any(stop > now.astimezone(timezone.utc) for _, _, stop in programmes):
        raise ValueError('DStv ZA returned no usable current or upcoming programmes')

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
    return len(channels), len(programmes)
