"""Export official DAZN linear channel schedules from DAZN's public Rail-Router API."""

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RAIL_URL = 'https://rail-router.discovery.indazn.com/eu/v10/Rail'
XMLTV_TIME = '%Y%m%d%H%M%S %z'
MAX_PAYLOAD_BYTES = 8_000_000

COUNTRY_LANGUAGE = {
    'de': 'de',
    'es': 'es',
    'it': 'it',
    'fr': 'fr',
    'pt': 'pt',
    'gb': 'en',
    'us': 'en',
    'ca': 'en',
}

DEFAULT_COUNTRIES = ('de', 'es', 'it', 'fr', 'pt', 'gb', 'us')

# Curated per-country DAZN linear channel mappings keyed by (country, asset_id)
# so only DAZN linear channels (and DAZN-branded carrier feeds needed by lineups)
# are emitted without duplicating third-party channels (Eurosport, Fox Footy, etc.).
CURATED_ASSETS = {
    # Germany
    ('de', 'bj5o60qt6uoe1clfdsev239pr'): ('DAZN1.de@SD', ('DAZN 1',), 'de'),
    ('de', '1v9s3d9dmt4ph1q4simfgo7ih4'): ('DAZN2.de@SD', ('DAZN 2',), 'de'),
    ('de', '1xx7lone8fvv21a2rpc5cji3jx'): ('DAZNRise.de@SD', ('DAZN RISE',), 'de'),
    ('de', '1vi4p39obt24i1dtsmthi921tl'): ('DAZNFAST.de@HD', ('DAZN FAST+', 'DAZN Fast'), 'de'),
    ('de', 'db90fd73f6cv14nn9ux8jf6n9'): ('DAZNMLB.de@SD', ('DAZN MLB',), 'de'),
    ('de', 'r0kudpubxdhq1pg22sywrz2w3'): ('DAZNNFLNetwork.de@SD', ('DAZN NFL Network',), 'de'),
    # Spain (reuse @Spain IDs for F1/LaLiga so es-movistar remains seamless fallback)
    ('es', 'g4xuuh9i2y2b13yamobxm8men'): ('DAZN1.es@SD', ('DAZN 1',), 'es'),
    ('es', '7kz6de1zs0wq13yx0rdsszp4t'): ('DAZN2.es@SD', ('DAZN 2',), 'es'),
    ('es', 'ng4ei1s4tvvk182ndxjeaj08p'): ('DAZNF1.uk@Spain', ('DAZN F1',), 'es'),
    ('es', 'q994m1myq9ps1dyhicv16xn9m'): ('DAZNLaLiga.uk@Spain', ('DAZN LALIGA',), 'es'),
    # Italy (reuse ZonaDAZN.it@SD so eu-sky-it remains seamless fallback)
    ('it', '4hqcjx5zv91f1bdx71io4bnrl'): ('ZonaDAZN.it@SD', ('DAZN 1',), 'it'),
    # France (Ligue 1+ linear channel on DAZN FR)
    ('fr', 'ndozfjboh3z617ww4zdvm4f3h'): ('Ligue1Plus.fr@SD', ('Ligue 1+',), 'fr'),
    # Portugal (reuse @Portugal IDs so pt-meo remains seamless fallback)
    ('pt', '192nxdhuxe0xb1nxzvjrm68ho8'): ('DAZN1.uk@Portugal', ('DAZN 1',), 'pt'),
    ('pt', '1o4twnocva6uwz9121szkcqly'): ('DAZN2.uk@Portugal', ('DAZN 2',), 'pt'),
    ('pt', '29l8c8txra9q1gmicmku13nhw'): ('DAZN3.uk@Portugal', ('DAZN 3',), 'pt'),
    ('pt', 'gpg12e28xrnbz5mmgjrjnu99'): ('DAZN4.uk@Portugal', ('DAZN 4',), 'pt'),
    ('pt', '1xi1dh83i1tl018kl25a3phsr2'): ('DAZN5.uk@Portugal', ('DAZN 5',), 'pt'),
    # United Kingdom
    ('gb', '1uk4wqa9cpm91cy40uybfda5t'): ('DAZNRedBullTV.uk@SD', ('DAZN Red Bull TV',), 'en'),
    # United States
    ('us', '1owzb99m9mnzy15hjxolpukwul'): ('DAZN1.us@SD', ('DAZN 1 USA',), 'en'),
}

# Fallback normalized title mapping if DAZN rotates an AssetId for a curated channel.
CURATED_TITLES = {
    ('de', 'dazn1'): ('DAZN1.de@SD', ('DAZN 1',), 'de'),
    ('de', 'dazn2'): ('DAZN2.de@SD', ('DAZN 2',), 'de'),
    ('de', 'daznrise'): ('DAZNRise.de@SD', ('DAZN RISE',), 'de'),
    ('de', 'daznfast'): ('DAZNFAST.de@HD', ('DAZN FAST+', 'DAZN Fast'), 'de'),
    ('de', 'mlbnetwork'): ('DAZNMLB.de@SD', ('DAZN MLB',), 'de'),
    ('de', 'nflnetwork'): ('DAZNNFLNetwork.de@SD', ('DAZN NFL Network',), 'de'),
    ('es', 'dazn1'): ('DAZN1.es@SD', ('DAZN 1',), 'es'),
    ('es', 'dazn2'): ('DAZN2.es@SD', ('DAZN 2',), 'es'),
    ('es', 'daznf1'): ('DAZNF1.uk@Spain', ('DAZN F1',), 'es'),
    ('es', 'daznlaliga'): ('DAZNLaLiga.uk@Spain', ('DAZN LALIGA',), 'es'),
    ('it', 'dazn1'): ('ZonaDAZN.it@SD', ('DAZN 1',), 'it'),
    ('fr', 'ligue1'): ('Ligue1Plus.fr@SD', ('Ligue 1+',), 'fr'),
    ('pt', 'dazn1'): ('DAZN1.uk@Portugal', ('DAZN 1',), 'pt'),
    ('pt', 'dazn2'): ('DAZN2.uk@Portugal', ('DAZN 2',), 'pt'),
    ('pt', 'dazn3'): ('DAZN3.uk@Portugal', ('DAZN 3',), 'pt'),
    ('pt', 'dazn4'): ('DAZN4.uk@Portugal', ('DAZN 4',), 'pt'),
    ('pt', 'dazn5'): ('DAZN5.uk@Portugal', ('DAZN 5',), 'pt'),
    ('gb', 'redbulltv'): ('DAZNRedBullTV.uk@SD', ('DAZN Red Bull TV',), 'en'),
    ('us', 'dazn1usa'): ('DAZN1.us@SD', ('DAZN 1 USA',), 'en'),
}


def _clean_text(value):
    if value is None or isinstance(value, bool):
        return ''
    return re.sub(r'\s+', ' ', str(value)).strip()


def _title_slug(value):
    text = _clean_text(value).split(':', 1)[0]
    return re.sub(r'[^a-z0-9]+', '', text.lower())


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
                raise ValueError('DAZN schedule response exceeded limit')
            return json.loads(payload)
        except HTTPError as error:
            if attempt >= retries or (error.code < 500 and error.code != 429):
                raise
        except (OSError, URLError):
            if attempt >= retries:
                raise
        time.sleep(0.5 * (attempt + 1))


def fetch_country_tiles(country, language=None):
    cc = _clean_text(country).lower()
    if cc == 'uk':
        cc = 'gb'
    lang = _clean_text(language).lower() or COUNTRY_LANGUAGE.get(cc, 'en')
    params = urlencode({
        'platform': 'web',
        'id': 'Livetvschedule',
        'country': cc,
        'brand': 'dazn',
        'languageCode': lang,
    })
    data = _get_json(f'{RAIL_URL}?{params}')
    tiles = data.get('Tiles', []) if isinstance(data, dict) else []
    return tiles if isinstance(tiles, list) else []


def _resolve_tile_mapping(country, tile):
    cc = 'gb' if country == 'uk' else country
    asset_id = _clean_text(tile.get('AssetId')).lower()
    if asset_id and (cc, asset_id) in CURATED_ASSETS:
        return CURATED_ASSETS[(cc, asset_id)]
    slug = _title_slug(tile.get('Title'))
    if slug and (cc, slug) in CURATED_TITLES:
        return CURATED_TITLES[(cc, slug)]
    return None


def _iter_schedule_items(tile):
    schedule = tile.get('LinearSchedule')
    if not isinstance(schedule, dict):
        return
    for key in ('Now', 'Next'):
        item = schedule.get(key)
        if isinstance(item, dict):
            yield item
    later = schedule.get('Later')
    if isinstance(later, list):
        for item in later:
            if isinstance(item, dict):
                yield item


def build_guide_data(country_tiles_pairs, now):
    """Convert (country, tiles) pairs into curated XMLTV channels and programmes."""
    now_utc = now.astimezone(timezone.utc)
    window_start = now_utc - timedelta(hours=6)
    window_end = now_utc + timedelta(days=3, hours=6)

    channels = {}
    programmes = {}

    for raw_country, tiles in country_tiles_pairs:
        country = _clean_text(raw_country).lower()
        if not country or not isinstance(tiles, list):
            continue
        for tile in tiles:
            if not isinstance(tile, dict):
                continue
            mapping = _resolve_tile_mapping(country, tile)
            if mapping is None:
                continue
            cid, display_names, lang = mapping
            added = False
            for item in _iter_schedule_items(tile):
                title = _clean_text(item.get('Title'))
                if not title:
                    continue
                start = _parse_iso(item.get('Start'))
                stop = _parse_iso(item.get('End'))
                if start is None or stop is None or stop <= start:
                    continue
                start_u = start.astimezone(timezone.utc)
                stop_u = stop.astimezone(timezone.utc)
                if start_u >= window_end or stop_u <= window_start:
                    continue
                episode = _clean_text(item.get('EpisodeTitle'))
                if episode.lower() == title.lower():
                    episode = ''
                desc = _clean_text(item.get('Description'))
                programmes[(cid, start_u, stop_u)] = (title, episode or None, desc or None, lang)
                added = True
            if added and cid not in channels:
                channels[cid] = list(display_names)

    return channels, programmes


def write_guide(output, now=None, countries=None):
    now = now or datetime.now(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    selected = tuple(
        _clean_text(c).lower() for c in (countries or DEFAULT_COUNTRIES) if _clean_text(c)
    )
    if not selected:
        raise ValueError('DAZN adapter requires at least one country code')

    pairs = []
    fetch_errors = 0
    for country in selected:
        try:
            tiles = fetch_country_tiles(country)
        except (OSError, HTTPError, URLError, ValueError, json.JSONDecodeError):
            fetch_errors += 1
            continue
        pairs.append((country, tiles))

    channels, programmes = build_guide_data(pairs, now)
    if not any(stop > now_utc for _, _, stop in programmes):
        raise ValueError(
            f'DAZN returned no usable current or upcoming programmes for: {", ".join(selected)}'
        )

    root = ET.Element('tv')
    for cid, names in sorted(channels.items()):
        definition = ET.SubElement(root, 'channel', id=cid)
        for name in names:
            ET.SubElement(definition, 'display-name').text = name
    for (cid, start, stop), (title, sub_title, desc, lang) in sorted(programmes.items()):
        programme = ET.SubElement(
            root,
            'programme',
            channel=cid,
            start=start.astimezone(timezone.utc).strftime(XMLTV_TIME),
            stop=stop.astimezone(timezone.utc).strftime(XMLTV_TIME),
        )
        ET.SubElement(programme, 'title', lang=lang).text = title
        if sub_title:
            ET.SubElement(programme, 'sub-title', lang=lang).text = sub_title
        if desc:
            ET.SubElement(programme, 'desc', lang=lang).text = desc

    ET.ElementTree(root).write(output, encoding='utf-8', xml_declaration=True)
    return len(channels), len(programmes), {'countries': list(selected), 'fetch_errors': fetch_errors}
