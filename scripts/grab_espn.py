"""Export the public ESPN schedule using linear XMLTV IDs and explicit ESPN+ mappings."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    airingId
    simulcastAiringId
    eventId
    name
    shortName
    type
    startDateTime
    endDateTime
    duration
    feedName
    language
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
ALLOWED_MATCH_KEYS = frozenset({
    'airing_id',
    'event_id',
    'feed',
    'language',
    'title_contains',
    'league',
    'sport',
})


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


def _clean_text(value):
    if value is None or isinstance(value, bool):
        return ''
    text = str(value).strip()
    return re.sub(r'\s+', ' ', text)


def _norm_key(value):
    return _clean_text(value).lower()


def _slug(value, max_len=28):
    cleaned = re.sub(r'[^a-z0-9]+', '-', _norm_key(value)).strip('-')
    if not cleaned:
        return ''
    if len(cleaned) <= max_len:
        return cleaned
    digest = hashlib.sha256(_norm_key(value).encode('utf-8')).hexdigest()[:8]
    return f'{cleaned[:max_len - 9].rstrip("-")}-{digest}'


def _is_espn_plus(record, network):
    net_id = _norm_key(network.get('id'))
    if net_id in ESPN_PLUS_NETWORKS:
        return True
    if _norm_key(network.get('type')) != 'online':
        return False
    packages = record.get('packages')
    if isinstance(packages, list):
        for pkg in packages:
            if isinstance(pkg, dict) and pkg.get('name') == 'ESPN_PLUS':
                return True
    return False


def _extract_title(record, include_feed=False):
    raw = record.get('name') or record.get('shortName')
    title = _clean_text(raw)
    if not title:
        return None
    if include_feed:
        feed = _clean_text(record.get('feedName'))
        if feed and feed.lower() not in title.lower():
            title = f'{title} ({feed})'
    return title


def _extract_language(record, default='en'):
    raw = _norm_key(record.get('language'))
    if raw and re.fullmatch(r'[a-z]{2,3}(?:-[a-z0-9]{2,4})?', raw):
        return raw.split('-')[0]
    return default


def _extract_airing_ids(record):
    ids = []
    seen = set()
    for field in ('airingId', 'id', 'simulcastAiringId'):
        val = _clean_text(record.get(field))
        if val and val.lower() not in seen:
            seen.add(val.lower())
            ids.append(val)
    return ids


def _extract_nested_name(record, field):
    item = record.get(field)
    return _clean_text(item.get('name')) if isinstance(item, dict) else ''


def _build_description(record, title, notes=()):
    parts = []
    seen = {title.lower()}
    for field in ('sport', 'league', 'subcategory'):
        text = _extract_nested_name(record, field)
        if text and text.lower() not in seen:
            seen.add(text.lower())
            parts.append(text)
    summary = ' - '.join(parts)
    valid_notes = [n for n in notes if n]
    if valid_notes:
        suffix = ' '.join(valid_notes)
        return f'{summary}. {suffix}' if summary else suffix
    return summary or None


def load_espn_plus_map(mapping_source):
    """Load and validate explicit ESPN+ playlist mappings from a path or dict/list."""
    if not mapping_source:
        return []
    if isinstance(mapping_source, (str, Path)):
        path = Path(mapping_source)
        if not path.exists():
            raise ValueError(f'ESPN+ mapping file not found: {path}')
        raw = json.loads(path.read_text(encoding='utf-8'))
    elif isinstance(mapping_source, (dict, list)):
        raw = mapping_source
    else:
        raise ValueError('espn_plus_map must be a file path, dict, or list')

    entries = raw.get('mappings', []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError('ESPN+ mapping file must contain a "mappings" list')

    rules = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f'ESPN+ mapping entry #{index} must be an object')
        channel_id = _clean_text(entry.get('channel_id'))
        display_name = _clean_text(entry.get('display_name'))
        match = entry.get('match')
        if not channel_id or not display_name:
            raise ValueError(f'ESPN+ mapping entry #{index} requires channel_id and display_name')
        if not isinstance(match, dict):
            raise ValueError(f'ESPN+ mapping entry #{index} requires a match object')
        cleaned_match = {}
        for key, value in match.items():
            if key not in ALLOWED_MATCH_KEYS:
                raise ValueError(f'Unsupported ESPN+ mapping match key: {key}')
            text = _norm_key(value)
            if text:
                cleaned_match[key] = text
        if not cleaned_match:
            raise ValueError(f'ESPN+ mapping entry #{index} must specify at least one match condition')
        # Rank explicit airing_id > event_id > general attribute rules deterministically.
        specificity = (
            1 if 'airing_id' in cleaned_match else 0,
            1 if 'event_id' in cleaned_match else 0,
            len(cleaned_match),
            -index,
        )
        rules.append({
            'channel_id': channel_id,
            'display_name': display_name,
            'match': cleaned_match,
            'specificity': specificity,
        })
    rules.sort(key=lambda rule: rule['specificity'], reverse=True)
    return rules


def _match_espn_plus_rule(event, rules):
    for rule in rules:
        m = rule['match']
        if 'airing_id' in m and m['airing_id'] not in event['airing_id_norms']:
            continue
        if 'event_id' in m and m['event_id'] != event['event_id_norm']:
            continue
        if 'feed' in m and m['feed'] != event['feed_norm']:
            continue
        if 'language' in m and m['language'] != event['lang']:
            continue
        if 'title_contains' in m and m['title_contains'] not in event['title'].lower():
            continue
        if 'league' in m and m['league'] != event['league_norm']:
            continue
        if 'sport' in m and m['sport'] != event['sport_norm']:
            continue
        return rule
    return None


def _stable_unmapped_channel_id(event):
    """Build a deterministic, refresh-stable unmapped channel ID from ESPN identity fields."""
    if event['event_id_norm']:
        parts = [f"e{_slug(event['event_id_norm'], 24)}", event['lang']]
        if event['feed_norm']:
            feed_slug = _slug(event['feed_norm'], 24)
            if feed_slug:
                parts.append(feed_slug)
        return f"ESPNPlus.unmapped.{'-'.join(parts)}.us"
    if event['primary_airing_id']:
        aid_slug = _slug(event['primary_airing_id'], 36)
        parts = [f'a{aid_slug}', event['lang']]
        if event['feed_norm']:
            feed_slug = _slug(event['feed_norm'], 20)
            if feed_slug:
                parts.append(feed_slug)
        return f"ESPNPlus.unmapped.{'-'.join(parts)}.us"
    basis = '|'.join((
        event['start'].astimezone(timezone.utc).isoformat(),
        event['title'].lower(),
        event['feed_norm'],
        event['lang'],
    ))
    digest = hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]
    return f'ESPNPlus.unmapped.h{digest}.us'


def _espn_plus_record_score(event):
    return (
        1 if event['explicit_stop'] else 0,
        1 if event['event_id_norm'] else 0,
        1 if event['feed_norm'] else 0,
        1 if event['league_norm'] else 0,
        1 if event['sport_norm'] else 0,
        len(event['airing_ids']),
    )


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


def build_guide_data(records, now, include_espn_plus=False, espn_plus_map=None):
    day = now.astimezone(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = day + timedelta(days=2)
    mapping_rules = load_espn_plus_map(espn_plus_map) if include_espn_plus else []

    linear_by_channel = {}
    linear_seen_airings = {}
    espn_plus_entries = []
    espn_plus_by_airing_id = {}
    espn_plus_by_broadcast_key = {}

    stats = {
        'linear_channels': 0,
        'linear_programmes': 0,
        'espn_plus_collected': 0,
        'espn_plus_deduplicated': 0,
        'espn_plus_unique': 0,
        'espn_plus_mapped': 0,
        'espn_plus_unmapped': 0,
    }

    channels = {}
    programmes = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        network = record.get('network')
        if not isinstance(network, dict):
            continue
        net_id = _norm_key(network.get('id'))
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
            channel_id, display_name, default_lang = LINEAR_CHANNELS[net_id]
            lang = _extract_language(record, default=default_lang)
            airing_ids = _extract_airing_ids(record)
            dedup_key = None
            for aid in airing_ids:
                existing_key = linear_seen_airings.get((channel_id, aid.lower()))
                if existing_key is not None:
                    dedup_key = existing_key
                    break
            if dedup_key is None:
                dedup_key = (start, title.lower())
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
            for aid in airing_ids:
                linear_seen_airings[(channel_id, aid.lower())] = dedup_key

        elif include_espn_plus and _is_espn_plus(record, network):
            title = _extract_title(record, include_feed=True)
            if not title:
                continue
            # Never infer missing ESPN+ end times from another simultaneous event's start.
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

            stats['espn_plus_collected'] += 1
            airing_ids = _extract_airing_ids(record)
            airing_id_norms = {aid.lower() for aid in airing_ids}
            event_id_raw = _clean_text(record.get('eventId'))
            event_id_norm = event_id_raw.lower()
            feed_raw = _clean_text(record.get('feedName'))
            feed_norm = feed_raw.lower()
            lang = _extract_language(record, default='en')

            # Deduplicate repeated airings by airing ID or (event_id, feed, language)
            # while preserving distinct broadcasts (feedName) and languages.
            if event_id_norm:
                broadcast_key = ('event', event_id_norm, feed_norm, lang)
            else:
                broadcast_key = (
                    'non_event',
                    start.astimezone(timezone.utc),
                    title.lower(),
                    feed_norm,
                    lang,
                )

            event = {
                'start': start,
                'stop': stop,
                'explicit_stop': explicit_stop is not None,
                'estimate_note': estimate_note,
                'title': title,
                'lang': lang,
                'airing_ids': airing_ids,
                'airing_id_norms': airing_id_norms,
                'primary_airing_id': airing_ids[0] if airing_ids else '',
                'event_id': event_id_raw,
                'event_id_norm': event_id_norm,
                'feed': feed_raw,
                'feed_norm': feed_norm,
                'sport_norm': _norm_key(_extract_nested_name(record, 'sport')),
                'league_norm': _norm_key(_extract_nested_name(record, 'league')),
                'broadcast_key': broadcast_key,
                'record': record,
            }

            existing_idx = None
            for aid_norm in airing_id_norms:
                if aid_norm in espn_plus_by_airing_id:
                    existing_idx = espn_plus_by_airing_id[aid_norm]
                    break
            if existing_idx is None and broadcast_key in espn_plus_by_broadcast_key:
                existing_idx = espn_plus_by_broadcast_key[broadcast_key]

            if existing_idx is not None:
                stats['espn_plus_deduplicated'] += 1
                prev = espn_plus_entries[existing_idx]
                if _espn_plus_record_score(event) > _espn_plus_record_score(prev):
                    event['airing_id_norms'] |= prev['airing_id_norms']
                    espn_plus_entries[existing_idx] = event
                else:
                    prev['airing_id_norms'] |= airing_id_norms
                for aid_norm in airing_id_norms:
                    espn_plus_by_airing_id[aid_norm] = existing_idx
                espn_plus_by_broadcast_key[broadcast_key] = existing_idx
            else:
                idx = len(espn_plus_entries)
                espn_plus_entries.append(event)
                for aid_norm in airing_id_norms:
                    espn_plus_by_airing_id[aid_norm] = idx
                espn_plus_by_broadcast_key[broadcast_key] = idx

    # Build linear channels: preserve explicit ends, infer missing linear ends from next start,
    # omit the final linear programme when no end time can be determined.
    linear_channel_ids = set()
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
                    continue
                stop = next_start
                estimate_note = ESTIMATE_NEXT_NOTE
            if start >= window_end or stop <= day:
                continue
            channels[channel_id] = item['display_name']
            linear_channel_ids.add(channel_id)
            programmes[(channel_id, start, stop)] = (
                item['title'],
                item['lang'],
                _build_description(item['record'], item['title'], notes=(estimate_note,)),
            )
    stats['linear_channels'] = len(linear_channel_ids)
    stats['linear_programmes'] = len(programmes)

    # Emit deduplicated ESPN+ events: mapped channels only from explicit playlist mappings;
    # unmapped events use deterministic, refresh-stable, clearly labelled unmapped channel IDs.
    if include_espn_plus and espn_plus_entries:
        stats['espn_plus_unique'] = len(espn_plus_entries)
        mapped_windows = {}
        ordered_plus = sorted(
            espn_plus_entries,
            key=lambda e: (e['start'], e['stop'], e['event_id_norm'], e['feed_norm'], e['lang'], e['title']),
        )
        for event in ordered_plus:
            matched_rule = _match_espn_plus_rule(event, mapping_rules)
            conflict_note = None
            if matched_rule is not None:
                target_cid = matched_rule['channel_id']
                existing_spans = mapped_windows.setdefault(target_cid, [])
                overlaps = any(
                    event['start'] < prev_stop and prev_start < event['stop']
                    for prev_start, prev_stop in existing_spans
                )
                if not overlaps:
                    existing_spans.append((event['start'], event['stop']))
                    channels[target_cid] = matched_rule['display_name']
                    programmes[(target_cid, event['start'], event['stop'])] = (
                        event['title'],
                        event['lang'],
                        _build_description(
                            event['record'],
                            event['title'],
                            notes=(event['estimate_note'],),
                        ),
                    )
                    stats['espn_plus_mapped'] += 1
                    continue
                conflict_note = (
                    f'Simultaneous conflict on mapped channel {target_cid}; '
                    'kept unmapped to prevent overlap.'
                )

            unmapped_cid = _stable_unmapped_channel_id(event)
            # Guard against an unexpected hash/ID collision with another simultaneous event.
            while (unmapped_cid, event['start'], event['stop']) in programmes:
                unmapped_cid = f'{unmapped_cid[:-3]}-alt.us'
            label_suffix = f' [{event["lang"]}]' if event['lang'] != 'en' else ''
            channels[unmapped_cid] = f'[Unmapped ESPN+] {event["title"]}{label_suffix}'

            id_details = []
            if event['event_id']:
                id_details.append(f'event_id={event["event_id"]}')
            if event['primary_airing_id']:
                id_details.append(f'airing_id={event["primary_airing_id"]}')
            if event['feed']:
                id_details.append(f'feed={event["feed"]}')
            id_details.append(f'lang={event["lang"]}')
            unmapped_note = (
                f'Unmapped ESPN+ event ({", ".join(id_details)}); '
                'not linked to a playlist stream.'
            )
            programmes[(unmapped_cid, event['start'], event['stop'])] = (
                event['title'],
                event['lang'],
                _build_description(
                    event['record'],
                    event['title'],
                    notes=(event['estimate_note'], conflict_note, unmapped_note),
                ),
            )
            stats['espn_plus_unmapped'] += 1

    return channels, programmes, stats


def write_guide(output, now=None, include_espn_plus=False, espn_plus_map=None):
    now = now or datetime.now(EASTERN)
    day = now.astimezone(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    records = fetch_schedule(day) + fetch_schedule(day + timedelta(days=1))
    channels, programmes, stats = build_guide_data(
        records,
        now,
        include_espn_plus=include_espn_plus,
        espn_plus_map=espn_plus_map,
    )

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
    return len(channels), len(programmes), stats
