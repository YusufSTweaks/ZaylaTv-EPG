"""Export the public ESPN schedule using linear XMLTV IDs and optional ESPN+ slots."""

import heapq
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SCHEDULE_URL = 'https://watch.graph.api.espn.com/api'
API_KEY = '0dbf88e8-cc6d-41da-aa83-18b5c630bc5c'
EASTERN = ZoneInfo('America/New_York')
XMLTV_TIME = '%Y%m%d%H%M%S %z'
MAX_PAYLOAD_BYTES = 16_000_000

ESTIMATE_NEXT_NOTE = 'End time estimated from next scheduled programme.'
ESTIMATE_DURATION_NOTE = 'End time estimated from scheduled duration.'

AIRINGS_QUERY = '''query Airings($countryCode: String!, $deviceType: DeviceType!, $tz: String!, $day: String, $limit: Int) {
  airings(countryCode: $countryCode, deviceType: $deviceType, tz: $tz, day: $day, limit: $limit) {
    id
    name
    shortName
    type
    startDateTime
    endDateTime
    duration
    feedName
    network {
      id
      type
      abbreviation
      name
      shortName
    }
    packages {
      name
    }
    sport {
      name
    }
    league {
      name
    }
    category {
      name
    }
    subcategory {
      name
    }
  }
}'''

# Map ESPN Watch linear network IDs to canonical XMLTV IDs (matching tvguide.com).
LINEAR_CHANNELS = {
    'espn1': ('ESPN.us@SD', 'ESPN', 'en'),
    'espn2': ('ESPN2.us@SD', 'ESPN2', 'en'),
    'espnu': ('ESPNU.us@SD', 'ESPNU', 'en'),
    'espnews': ('ESPNews.us@SD', 'ESPNews', 'en'),
    'sec': ('SECNetwork.us@SD', 'SEC Network', 'en'),
    'acc': ('ACCNetwork.us@SD', 'ACC Network', 'en'),
    'espndeportes': ('ESPNDeportes.us@SD', 'ESPN Deportes', 'es'),
}

ESPN_PLUS_NETWORKS = {'espn_dtc', 'bam_dtc'}


def _parse_iso(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _is_espn_plus(record, network):
    net_id = (network.get('id') or '').strip().lower()
    if net_id in ESPN_PLUS_NETWORKS:
        return True
    if (network.get('type') or '').strip().lower() != 'online':
        return False
    packages = record.get('packages')
    if isinstance(packages, list):
        for pkg in packages:
            if isinstance(pkg, dict) and pkg.get('name') == 'ESPN_PLUS':
                return True
    return False


def _extract_title(record, include_feed=False):
    raw = record.get('name') or record.get('shortName')
    if not isinstance(raw, str) or not raw.strip():
        return None
    title = raw.strip()
    if include_feed:
        feed = record.get('feedName')
        if isinstance(feed, str) and feed.strip():
            feed_text = feed.strip()
            if feed_text.lower() not in title.lower():
                title = f'{title} ({feed_text})'
    return title


def _build_description(record, title, estimate_note=None):
    parts = []
    seen = {title.lower()}
    for field in ('sport', 'league', 'subcategory'):
        item = record.get(field)
        name = item.get('name') if isinstance(item, dict) else None
        if isinstance(name, str):
            text = name.strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                parts.append(text)
    summary = ' - '.join(parts)
    if estimate_note:
        return f'{summary}. {estimate_note}' if summary else estimate_note
    return summary or None


def fetch_schedule(day):
    url = f'{SCHEDULE_URL}?{urlencode({"apiKey": API_KEY})}'
    body = json.dumps({
        'operationName': 'Airings',
        'query': AIRINGS_QUERY,
        'variables': {
            'countryCode': 'US',
            'deviceType': 'DESKTOP',
            'tz': 'America/New_York',
            'day': day.strftime('%Y-%m-%d'),
            'limit': 2000,
        },
    }, separators=(',', ':')).encode('utf-8')
    request = Request(
        url,
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0',
        },
        method='POST',
    )
    with urlopen(request, timeout=15) as response:
        payload = response.read(MAX_PAYLOAD_BYTES + 1)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f'ESPN schedule exceeds {MAX_PAYLOAD_BYTES // 1_000_000} MB')
    parsed = json.loads(payload)
    airings = parsed.get('data', {}).get('airings') if isinstance(parsed, dict) else None
    if not isinstance(airings, list):
        raise ValueError('Expected a list of airings from ESPN')
    return airings


def build_guide_data(records, now, include_espn_plus=False):
    day = now.astimezone(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = day + timedelta(days=2)

    linear_by_channel = {}
    espn_plus_events = {}
    channels = {}
    programmes = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        network = record.get('network')
        if not isinstance(network, dict):
            continue
        net_id = (network.get('id') or '').strip().lower()
        start = _parse_iso(record.get('startDateTime'))
        if start is None:
            continue
        explicit_stop = _parse_iso(record.get('endDateTime'))
        if explicit_stop is not None and explicit_stop <= start:
            explicit_stop = None

        if net_id in LINEAR_CHANNELS:
            title = _extract_title(record, include_feed=False)
            if not title:
                continue
            channel_id, display_name, lang = LINEAR_CHANNELS[net_id]
            dedup_key = (start, title)
            existing = linear_by_channel.setdefault(channel_id, {}).get(dedup_key)
            if existing is None or (existing['stop'] is None and explicit_stop is not None):
                linear_by_channel[channel_id][dedup_key] = {
                    'start': start,
                    'stop': explicit_stop,
                    'title': title,
                    'lang': lang,
                    'display_name': display_name,
                    'record': record,
                }
        elif include_espn_plus and _is_espn_plus(record, network):
            title = _extract_title(record, include_feed=True)
            if not title:
                continue
            stop = explicit_stop
            estimate_note = None
            if stop is None:
                duration = record.get('duration')
                if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0:
                    stop = start + timedelta(seconds=duration)
                    estimate_note = ESTIMATE_DURATION_NOTE
                else:
                    continue
            if start >= window_end or stop <= day or stop <= now:
                continue
            event_key = (start, stop, title)
            if event_key not in espn_plus_events:
                espn_plus_events[event_key] = {
                    'start': start,
                    'stop': stop,
                    'title': title,
                    'desc': _build_description(record, title, estimate_note=estimate_note),
                }

    for channel_id, items_map in linear_by_channel.items():
        ordered = sorted(items_map.values(), key=lambda item: (item['start'], item['title']))
        for index, item in enumerate(ordered):
            start = item['start']
            stop = item['stop']
            estimate_note = None
            if stop is None:
                next_start = None
                for candidate in ordered[index + 1:]:
                    if candidate['start'] > start:
                        next_start = candidate['start']
                        break
                if next_start is None:
                    # Omit the final programme on a channel when no end time can be determined.
                    continue
                stop = next_start
                estimate_note = ESTIMATE_NEXT_NOTE
            if start >= window_end or stop <= day:
                continue
            channels[channel_id] = item['display_name']
            programmes[(channel_id, start, stop)] = (
                item['title'],
                item['lang'],
                _build_description(item['record'], item['title'], estimate_note=estimate_note),
            )

    if include_espn_plus and espn_plus_events:
        # Pack simultaneous ESPN+ events into the minimum number of non-overlapping slots.
        slot_heap = []
        next_slot = 0
        for event in sorted(espn_plus_events.values(), key=lambda e: (e['start'], e['stop'], e['title'])):
            if slot_heap and slot_heap[0][0] <= event['start']:
                _, slot_index = heapq.heappop(slot_heap)
            else:
                next_slot += 1
                slot_index = next_slot
            heapq.heappush(slot_heap, (event['stop'], slot_index))
            channel_id = f'ESPNPlus.us@{slot_index:02d}'
            channels[channel_id] = f'ESPN+ {slot_index:02d}'
            programmes[(channel_id, event['start'], event['stop'])] = (
                event['title'],
                'en',
                event['desc'],
            )

    return channels, programmes


def write_guide(output, now=None, include_espn_plus=False):
    now = now or datetime.now(EASTERN)
    day = now.astimezone(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    records = fetch_schedule(day) + fetch_schedule(day + timedelta(days=1))
    channels, programmes = build_guide_data(records, now, include_espn_plus=include_espn_plus)

    if not any(stop > now for _, _, stop in programmes):
        raise ValueError('ESPN returned no usable current or upcoming programmes')

    root = ET.Element('tv')
    for channel_id, name in sorted(channels.items()):
        definition = ET.SubElement(root, 'channel', id=channel_id)
        ET.SubElement(definition, 'display-name').text = name
    for (channel_id, start, stop), (title, lang, desc) in sorted(programmes.items()):
        programme = ET.SubElement(
            root, 'programme', channel=channel_id,
            start=start.astimezone(timezone.utc).strftime(XMLTV_TIME),
            stop=stop.astimezone(timezone.utc).strftime(XMLTV_TIME),
        )
        ET.SubElement(programme, 'title', lang=lang).text = title
        if desc:
            ET.SubElement(programme, 'desc', lang=lang).text = desc
    ET.ElementTree(root).write(output, encoding='utf-8', xml_declaration=True)
    return len(channels), len(programmes)
