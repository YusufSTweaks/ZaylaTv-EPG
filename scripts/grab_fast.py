"""Export official FAST & public streaming EPGs (Pluto TV, Plex, Roku, Samsung TV Plus, PBS)
using canonical iptv-org XMLTV IDs from the pinned i.mjh.nz catalog."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FEED_BASE = 'https://i.mjh.nz'
XMLTV_TIME = '%Y%m%d%H%M%S %z'
MAX_GZ_BYTES = 16_000_000
MAX_XML_BYTES = 96_000_000

DEFAULT_CATALOGS = (
    'i.mjh.nz_pluto.channels.xml',
    'i.mjh.nz_plex.channels.xml',
    'i.mjh.nz_roku.channels.xml',
    'i.mjh.nz_samsung.channels.xml',
    'i.mjh.nz_pbs.channels.xml',
)

# High-value feed paths to fetch (one compact .xml.gz download per feed).
DEFAULT_FEEDS = (
    'PlutoTV/us',
    'PlutoTV/gb',
    'PlutoTV/ca',
    'PlutoTV/fr',
    'PlutoTV/de',
    'PlutoTV/es',
    'PlutoTV/it',
    'PlutoTV/br',
    'PlutoTV/mx',
    'Plex/us',
    'Plex/gb',
    'Plex/ca',
    'Plex/fr',
    'Plex/de',
    'Roku/all',
    'SamsungTVPlus/us',
    'SamsungTVPlus/gb',
    'SamsungTVPlus/ca',
    'SamsungTVPlus/fr',
    'SamsungTVPlus/de',
    'PBS/all',
)


def _clean_text(value):
    if value is None or isinstance(value, bool):
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip()


def _parse_xmltv_time(value):
    text = _clean_text(value)
    if not text:
        return None
    for fmt in ('%Y%m%d%H%M%S %z', '%Y%m%d%H%M%S%z'):
        try:
            return datetime.strptime(text, fmt).astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def load_canonical_fast_map(upstream_dir, catalogs=DEFAULT_CATALOGS, feeds=None):
    """Load feed_path -> {raw_channel_id: (xmltv_id, display_name, lang)} from pinned i.mjh.nz."""
    allowed_feeds = set(feeds) if feeds else None
    by_feed = {}
    if not upstream_dir:
        return by_feed
    sites_dir = Path(upstream_dir) / 'sites' / 'i.mjh.nz'
    if not sites_dir.exists():
        return by_feed
    for fname in catalogs:
        path = sites_dir / fname
        if not path.exists():
            continue
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        for ch in root.findall('channel'):
            site_id = _clean_text(ch.get('site_id'))
            xmltv_id = _clean_text(ch.get('xmltv_id'))
            name = _clean_text(ch.text)
            lang = _clean_text(ch.get('lang')) or 'en'
            if not site_id or not xmltv_id or not name or '#' not in site_id:
                continue
            feed_path, raw_id = site_id.split('#', 1)
            if allowed_feeds is not None and feed_path not in allowed_feeds:
                continue
            by_feed.setdefault(feed_path, {}).setdefault(raw_id, (xmltv_id, name, lang))
    return by_feed


def fetch_feed_xml(feed_path):
    url = f'{FEED_BASE}/{feed_path}.xml.gz'
    request = Request(
        url,
        headers={
            'Accept': 'application/gzip, application/xml, */*',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        },
    )
    with urlopen(request, timeout=25) as response:
        gz_bytes = response.read(MAX_GZ_BYTES + 1)
    if len(gz_bytes) > MAX_GZ_BYTES:
        raise ValueError(f'FAST feed {feed_path} exceeded compressed size cap')
    if gz_bytes[:2] == b'\x1f\x8b':
        xml_bytes = gzip.decompress(gz_bytes)
    else:
        xml_bytes = gz_bytes
    if len(xml_bytes) > MAX_XML_BYTES:
        raise ValueError(f'FAST feed {feed_path} exceeded decompressed XML cap')
    return xml_bytes


def extract_feed_programmes(xml_bytes, feed_map, now, window_hours=48):
    """Extract canonical mapped channels & programmes within [now-6h, now+window_hours]."""
    now_utc = now.astimezone(timezone.utc)
    win_start = now_utc - timedelta(hours=6)
    win_end = now_utc + timedelta(hours=window_hours)

    tree = ET.fromstring(xml_bytes)
    if tree.tag != 'tv':
        return {}, {}

    raw_names = {}
    for ch in tree.findall('channel'):
        rid = _clean_text(ch.get('id'))
        if rid in feed_map:
            dn = _clean_text(ch.findtext('display-name'))
            if dn:
                raw_names[rid] = dn

    channels = {}
    programmes = {}
    for prog in tree.findall('programme'):
        rid = _clean_text(prog.get('channel'))
        mapped = feed_map.get(rid)
        if not mapped:
            continue
        title = _clean_text(prog.findtext('title'))
        if not title:
            continue
        start = _parse_xmltv_time(prog.get('start'))
        stop = _parse_xmltv_time(prog.get('stop'))
        if start is None or stop is None or stop <= start:
            continue
        if start >= win_end or stop <= win_start:
            continue
        xmltv_id, canon_name, lang = mapped
        desc = _clean_text(prog.findtext('desc')) or None
        programmes[(xmltv_id, start, stop)] = (title, lang, desc)
        if xmltv_id not in channels:
            names = [canon_name]
            raw_dn = raw_names.get(rid)
            if raw_dn and raw_dn.lower() != canon_name.lower():
                names.append(raw_dn)
            channels[xmltv_id] = names
    return channels, programmes


def write_guide(output, now=None, upstream=None, feeds=None, catalogs=None, max_channels=None):
    now = now or datetime.now(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    feeds = tuple(feeds) if feeds else DEFAULT_FEEDS
    catalogs = tuple(catalogs) if catalogs else DEFAULT_CATALOGS
    by_feed = load_canonical_fast_map(upstream, catalogs=catalogs, feeds=feeds)
    active_feeds = [f for f in feeds if f in by_feed]
    if not active_feeds:
        raise ValueError('No canonical FAST channel mappings found in upstream i.mjh.nz catalog')

    channels = {}
    programmes = {}
    errors = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        future_map = {pool.submit(fetch_feed_xml, fp): fp for fp in active_feeds}
        fetched = {}
        for future in as_completed(future_map):
            fp = future_map[future]
            try:
                fetched[fp] = future.result()
            except (OSError, HTTPError, URLError, ValueError, gzip.BadGzipFile) as err:
                errors.append(f'{fp}: {err}')

    for fp in active_feeds:
        xml_bytes = fetched.get(fp)
        if not xml_bytes:
            continue
        try:
            f_channels, f_programmes = extract_feed_programmes(xml_bytes, by_feed[fp], now_utc)
        except ET.ParseError as err:
            errors.append(f'{fp}: {err}')
            continue
        for cid, names in f_channels.items():
            if cid in channels:
                continue
            if max_channels is not None and len(channels) >= max_channels:
                continue
            channels[cid] = names
        for (cid, start, stop), val in f_programmes.items():
            if cid in channels and (cid, start, stop) not in programmes:
                programmes[(cid, start, stop)] = val

    # Keep only channels with at least one programme.
    used_cids = {cid for cid, _, _ in programmes}
    channels = {cid: names for cid, names in channels.items() if cid in used_cids}
    if not any(stop > now_utc for _, _, stop in programmes):
        raise ValueError(f'FAST feeds returned no usable upcoming programmes ({"; ".join(errors[:4])})')

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
    stats = {
        'channels': len(channels),
        'programmes': len(programmes),
        'feeds_fetched': len(fetched),
        'feed_errors': len(errors),
    }
    return len(channels), len(programmes), stats
