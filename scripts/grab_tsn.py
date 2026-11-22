"""Export the public TSN schedule using the linear and streaming feed IDs it supplies."""

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo


SCHEDULE_URL = 'https://www.tsn.ca/pf/api/v3/content/fetch/sports-schedule-custom'
EASTERN = ZoneInfo('America/Toronto')
XMLTV_TIME = '%Y%m%d%H%M%S %z'


def fetch_schedule(day):
    query = {
        'type': 'scheduleQuery',
        'channelGroup': 'TSN+',
        'selectedChannels': 'TSN1,TSN2,TSN3,TSN4,TSN5,TSN+',
        'startTime': day.isoformat(timespec='milliseconds'),
        'endTime': (day + timedelta(days=1)).isoformat(timespec='milliseconds'),
    }
    parameters = urlencode({'query': json.dumps(query, separators=(',', ':')), '_website': 'tsn'})
    with urlopen(f'{SCHEDULE_URL}?{parameters}', timeout=15) as response:
        payload = response.read(4_000_001)
    if len(payload) > 4_000_000:
        raise ValueError('TSN schedule exceeds 4 MB')
    records = json.loads(payload)
    if not isinstance(records, list):
        raise ValueError('Expected a list of programmes from TSN')
    return records


def write_guide(output, now=None):
    now = now or datetime.now(EASTERN)
    day = now.astimezone(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    end = day + timedelta(days=2)
    records = fetch_schedule(day) + fetch_schedule(day + timedelta(days=1))
    channels = {}
    programmes = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        name = record.get('channelName')
        headlines = record.get('headlines')
        title = headlines.get('basic') if isinstance(headlines, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r'TSN(?:[1-5]|\+\d{2})', name):
            continue
        if not isinstance(title, str) or not title.strip():
            continue
        try:
            start = datetime.fromisoformat(record.get('startTime') or '')
            stop = datetime.fromisoformat(record.get('endTime') or '')
        except (TypeError, ValueError):
            continue
        if not start.tzinfo or not stop.tzinfo or not start < stop or start >= end or stop <= day:
            continue
        channel = f'TSNPlus.ca@{name[-2:]}' if name.startswith('TSN+') else f'{name}.ca@SD'
        channels[channel] = name
        programmes[(channel, start, stop)] = title.strip()

    if not any(stop > now for _, _, stop in programmes):
        raise ValueError('TSN returned no usable current or upcoming programmes')
    root = ET.Element('tv')
    for channel, name in sorted(channels.items()):
        definition = ET.SubElement(root, 'channel', id=channel)
        ET.SubElement(definition, 'display-name').text = name
    for (channel, start, stop), title in sorted(programmes.items()):
        programme = ET.SubElement(
            root, 'programme', channel=channel,
            start=start.astimezone(timezone.utc).strftime(XMLTV_TIME),
            stop=stop.astimezone(timezone.utc).strftime(XMLTV_TIME),
        )
        ET.SubElement(programme, 'title', lang='en').text = title
    ET.ElementTree(root).write(output, encoding='utf-8', xml_declaration=True)
    return len(channels), len(programmes)
