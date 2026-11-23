"""Validate and merge actual XMLTV programme data into combined and per-region feeds."""
import copy, datetime as dt, gzip, html, json, re, sys, xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from unicodedata import normalize

UTC = dt.timezone.utc


def ts(v):
    return dt.datetime.strptime(v.strip(), '%Y%m%d%H%M%S %z').astimezone(UTC)


def region_slug(region):
    cleaned = re.sub(r'[^a-z0-9]+', '-', str(region).strip().lower()).strip('-')
    if not cleaned:
        raise ValueError(f'Invalid region identifier: {region!r}')
    return cleaned


def _build_xmltv_payload(channels_map, rows_list):
    root = ET.Element('tv', {'generator-info-name': 'Zayla TV maintained-site EPG merger'})
    for cid in sorted(channels_map):
        root.append(copy.deepcopy(channels_map[cid]))
    ordered = sorted(rows_list, key=lambda p: (p.get('channel'), ts(p.get('start'))))
    root.extend(ordered)
    ET.indent(root)
    payload = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    ET.fromstring(payload)
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    return payload, compressed


def add_channel_aliases(channels, aliases):
    """Append unambiguous names without replacing the source label or guide ID."""
    def key(value):
        return normalize('NFKC', value).strip().casefold()

    owners = defaultdict(set)
    existing_names = {}
    for channel_id, channel in channels.items():
        names = {key(n.text) for n in channel.findall('display-name') if n.text}
        existing_names[channel_id] = names
        for name in names | {key(channel_id)}:
            owners[name].add(channel_id)
    for channel_id, values in aliases.items():
        if channel_id in channels:
            for value in values:
                owners[key(value)].add(channel_id)

    updated = dict(channels)
    added = 0
    for channel_id, values in aliases.items():
        if channel_id not in channels:
            continue
        names = existing_names[channel_id]
        for value in values:
            folded = key(value)
            if not folded or folded in names or owners[folded] != {channel_id}:
                continue
            if updated[channel_id] is channels[channel_id]:
                updated[channel_id] = copy.deepcopy(channels[channel_id])
            channel = updated[channel_id]
            alias = ET.Element('display-name')
            alias.text = value
            channel.insert(len(channel.findall('display-name')), alias)
            names.add(folded)
            added += 1
    return updated, added


_QUALITY = re.compile(r'\b(?:u?hd|fhd|sd|4k|8k|hevc|h265|1080p|720p)\b', re.I)
_PROVIDER_ID = re.compile(r'^[a-z][a-z0-9]{1,39}\.[a-z]{2}$')


def provider_slug(name):
    folded = normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    folded = folded.replace('&', 'and').replace('+', 'plus')
    return re.sub(r'[^a-z0-9]', '', folded)


def id_country(channel_id):
    match = re.search(r'\.([A-Za-z]{2})$', channel_id.split('@', 1)[0])
    return match.group(1).lower() if match else None


def derive_provider_id_aliases(channels):
    """IPTV providers usually send tvg-ids as slug(name).cc (wdr.de, kabel1.de).

    Derive that form from each display name and keep only aliases that point to
    exactly one channel across the whole guide and do not shadow an existing
    channel ID base. The app resolves these with a single dictionary lookup.
    """
    bases = {cid.split('@', 1)[0].lower() for cid in channels}
    owners = defaultdict(set)
    for cid, channel in channels.items():
        country = id_country(cid)
        if not country:
            continue
        for node in channel.findall('display-name'):
            text = (node.text or '').strip()
            if not text or _PROVIDER_ID.match(text.lower()):
                continue
            for variant in {text, _QUALITY.sub(' ', text)}:
                alias = f'{provider_slug(variant)}.{country}'
                if _PROVIDER_ID.match(alias) and alias not in bases:
                    owners[alias].add(cid)
    derived = defaultdict(list)
    for alias, ids in sorted(owners.items()):
        if len(ids) == 1:
            derived[next(iter(ids))].append(alias)
    return dict(derived)


def _build_index_html(report):
    rows_html = []
    for region, info in sorted(report['regions'].items()):
        kb = info['compressed_bytes'] / 1024
        size_str = f'{kb / 1024:.2f} MB' if kb >= 1024 else f'{kb:.0f} KB'
        rows_html.append(
            f'<tr><td><strong>{html.escape(region)}</strong></td>'
            f'<td><a href="{html.escape(info["file"])}">{html.escape(info["file"])}</a></td>'
            f'<td><a href="{html.escape(info["xml_file"])}">XML</a></td>'
            f'<td>{info["channels"]:,}</td>'
            f'<td>{info["programmes"]:,}</td>'
            f'<td>{size_str}</td></tr>'
        )
    table = (
        '<table border="1" cellpadding="6" cellspacing="0">'
        '<thead><tr><th>Region</th><th>Compressed XMLTV (.xml.gz)</th><th>XML</th>'
        '<th>Channels</th><th>Programmes</th><th>Size</th></tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table>'
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<title>Zayla TV EPG</title>'
        '<h1>Zayla TV EPG</h1>'
        f'<p>Combined guide ({report["channels"]:,} channels, {report["programmes"]:,} programmes): '
        '<a href="guide.xml.gz">guide.xml.gz</a> | '
        '<a href="guide.xml">guide.xml</a> | '
        '<a href="status.json">status.json</a></p>'
        '<h2>Per-Region EPG Files</h2>'
        f'{table}'
        '</html>'
    )


def merge(input_dir, manifest, required, out_dir, now=None, aliases_by_region=None):
    now = now or dt.datetime.now(UTC)
    aliases_by_region = aliases_by_region or {}
    channels = {}
    rows = []
    keys = set()
    owner = {}
    covered = set()
    reports = []

    # Track per-region deduplicated channels/programmes so every regional file is self-contained.
    region_channels = {}
    region_rows = {}
    region_keys = {}
    region_owner = {}

    for spec in manifest:
        region = spec['region']
        info = {'source': spec['id'], 'region': region, 'accepted': 0, 'rejected': 0}
        reports.append(info)
        try:
            data = (Path(input_dir) / spec['output']).read_bytes()
            if len(data) > 64 * 1024 * 1024:
                raise ValueError('Source exceeds 64 MiB')
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise ValueError('DTD/entities unsupported')
            tree = ET.fromstring(data)
            if tree.tag != 'tv':
                raise ValueError('Expected XMLTV root')
            definitions = {
                ch.get('id'): ch
                for ch in tree.findall('channel')
                if ch.get('id') and ch.findtext('display-name')
            }
            valid = []
            upcoming = set()
            for p in tree.findall('programme'):
                try:
                    cid = p.get('channel')
                    start = ts(p.get('start', ''))
                    stop = ts(p.get('stop', ''))
                    if cid not in definitions or not p.findtext('title') or stop <= start:
                        raise ValueError('Invalid programme')
                    valid.append((p, cid, start, stop))
                    if stop > now:
                        upcoming.add(cid)
                except (ValueError, TypeError):
                    info['rejected'] += 1

            r_ch = region_channels.setdefault(region, {})
            r_rows = region_rows.setdefault(region, [])
            r_keys = region_keys.setdefault(region, set())
            r_own = region_owner.setdefault(region, {})

            for p, cid, start, stop in valid:
                if cid not in upcoming:
                    continue
                key = (cid, start, stop)

                # Per-region self-contained schedule (first source in this region owns cid).
                if cid not in r_own or r_own[cid] == spec['id']:
                    r_own[cid] = spec['id']
                    r_ch[cid] = definitions[cid]
                    if key not in r_keys:
                        r_keys.add(key)
                        r_rows.append(copy.deepcopy(p))

                # Global combined schedule (first source overall owns cid).
                if cid in owner and owner[cid] != spec['id']:
                    continue
                owner[cid] = spec['id']
                channels[cid] = definitions[cid]
                if key in keys:
                    continue
                keys.add(key)
                rows.append(copy.deepcopy(p))
                info['accepted'] += 1

            if info['accepted'] or r_rows:
                if info['accepted']:
                    covered.add(region)
        except (OSError, ET.ParseError, ValueError) as e:
            info['error'] = str(e)

    missing = set(required) - covered
    if missing or not rows:
        report = {
            'generated_at_utc': now.isoformat(),
            'channels': len(channels),
            'programmes': len(rows),
            'covered_regions': sorted(covered),
            'missing_regions': sorted(missing),
            'sources': reports,
        }
        raise ValueError(
            'Publication stopped: missing usable future coverage '
            + ','.join(sorted(missing))
            + '. '
            + json.dumps(report)
        )

    # Validate & serialize combined XMLTV plus every per-region XMLTV in memory before writing.
    # Curated aliases win; derived provider-style IDs never reuse a curated value.
    curated_values = {
        normalize('NFKC', value).strip().casefold()
        for aliases in aliases_by_region.values() for names in aliases.values() for value in names
    }
    derived = {
        cid: [a for a in values if a not in curated_values]
        for cid, values in derive_provider_id_aliases(channels).items()
    }
    combined_aliases = defaultdict(list)
    for region, aliases in aliases_by_region.items():
        for channel_id, names in aliases.items():
            if channel_id in region_channels.get(region, {}):
                combined_aliases[channel_id].extend(names)
    for channel_id, names in derived.items():
        combined_aliases[channel_id].extend(names)
    aliased_channels, combined_alias_count = add_channel_aliases(channels, combined_aliases)
    payload, compressed = _build_xmltv_payload(aliased_channels, rows)
    region_payloads = {}
    region_report = {}
    for region in sorted(region_rows):
        if not region_rows[region]:
            continue
        slug = region_slug(region)
        r_aliases = defaultdict(list)
        for channel_id, names in aliases_by_region.get(region, {}).items():
            r_aliases[channel_id].extend(names)
        for channel_id in region_channels[region]:
            r_aliases[channel_id].extend(derived.get(channel_id, []))
        r_channels, alias_count = add_channel_aliases(region_channels[region], r_aliases)
        r_payload, r_compressed = _build_xmltv_payload(r_channels, region_rows[region])
        region_payloads[slug] = (r_payload, r_compressed)
        region_report[region] = {
            'file': f'regions/{slug}.xml.gz',
            'xml_file': f'regions/{slug}.xml',
            'channels': len(region_channels[region]),
            'channel_aliases_added': alias_count,
            'programmes': len(region_rows[region]),
            'compressed_bytes': len(r_compressed),
        }

    report = {
        'generated_at_utc': now.isoformat(),
        'channels': len(channels),
        'programmes': len(rows),
        'channel_aliases_added': combined_alias_count,
        'covered_regions': sorted(covered),
        'missing_regions': sorted(missing),
        'regions': region_report,
        'sources': reports,
    }

    # Nothing is written until all validation and regional requirements pass.
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    regions_dir = out / 'regions'
    regions_dir.mkdir(parents=True, exist_ok=True)
    expected_region_files = set()
    for slug, (r_payload, r_compressed) in region_payloads.items():
        xml_name = f'{slug}.xml'
        gz_name = f'{slug}.xml.gz'
        expected_region_files.update((xml_name, gz_name))
        (regions_dir / xml_name).write_bytes(r_payload)
        (regions_dir / gz_name).write_bytes(r_compressed)
    for stale in regions_dir.iterdir():
        if stale.is_file() and stale.name not in expected_region_files:
            stale.unlink()

    (out / 'guide.xml').write_bytes(payload)
    (out / 'guide.xml.gz').write_bytes(compressed)
    (out / 'status.json').write_text(json.dumps(report, indent=2) + '\n')
    (out / '.nojekyll').write_text('')
    (out / 'index.html').write_text(_build_index_html(report))
    return report


if __name__ == '__main__':
    config_path = Path(sys.argv[3])
    config = json.loads(config_path.read_text())
    alias_path = config.get('channel_id_aliases')
    aliases = json.loads((config_path.parent / alias_path).read_text()) if alias_path else {}
    print(
        json.dumps(
            merge(
                sys.argv[1],
                json.loads(Path(sys.argv[2]).read_text()),
                config['required_regions'],
                sys.argv[4],
                aliases_by_region=aliases,
            ),
            indent=2,
        )
    )
