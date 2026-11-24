"""Run bounded source grabs and record their outcomes."""

import json
import os
import signal
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from grab_beinsports import write_guide as write_beinsports_guide
from grab_dazn import write_guide as write_dazn_guide
from grab_dstv_za import write_guide as write_dstv_za_guide
from grab_espn import write_guide as write_espn_guide
from grab_fast import write_guide as write_fast_guide
from grab_pro_teams import write_guide as write_pro_teams_guide
from grab_tsn import write_guide
from grab_tvguide import write_guide as write_tvguide_guide


from zoneinfo import ZoneInfo


EASTERN_TZ = ZoneInfo('America/New_York')
BROADCAST_DAY_ROLLOVER_REGIONS = {'US', 'CA'}
WESTERN_REGION_OFFSETS = {
    'MX': -6,
    'BR': -6,
    'LATAM': -6,
}


def grab_start_date(source, now=None):
    """Return the calendar date (YYYY-MM-DD) for an iptv-org grabber.

    TV Passport's North American broadcast day runs 06:00 AM -> 06:00 AM Eastern
    (America/New_York), so US/CA grabs roll over at 06:00 AM Eastern rather than
    midnight or UTC-8 (which left CNN Canada and 1,000+ US/CA stations blank
    between midnight and 06:00 AM Eastern). Non-Western regions stay anchored to
    midnight America/New_York to match Zayla TV's default Eastern display clock.
    """
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    region = source.get('region')
    if region in BROADCAST_DAY_ROLLOVER_REGIONS:
        return (now_utc.astimezone(EASTERN_TZ) - timedelta(hours=6)).date().isoformat()
    offset_hours = WESTERN_REGION_OFFSETS.get(region)
    if offset_hours is not None:
        return (now_utc + timedelta(hours=offset_hours)).date().isoformat()
    return now_utc.astimezone(EASTERN_TZ).date().isoformat()


def grab_days(source, curr_date, now=None):
    """Return grab horizon in days (3 when anchored to yesterday overnight, else 2)."""
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    today_et = now_utc.astimezone(EASTERN_TZ).date().isoformat()
    if source.get('region') in BROADCAST_DAY_ROLLOVER_REGIONS and curr_date < today_et:
        return 3
    return 2


def run_grab(source, upstream, output):
    if source.get('adapter') == 'tsn':
        try:
            channels, programmes = write_guide(output / source['output'])
            print(f'TSN official schedule: {channels} channel feeds and {programmes} programmes', flush=True)
            return {'exit_code': 0, 'selected_channels': channels}
        except (OSError, ValueError) as error:
            message = f'Official TSN schedule failed: {error}'
            print(message, flush=True)
            return {'exit_code': None, 'error': message}

    if source.get('adapter') == 'espn':
        try:
            include_espn_plus = bool(source.get('include_espn_plus', False))
            channels, programmes, stats = write_espn_guide(
                output / source['output'],
                include_espn_plus=include_espn_plus,
                espn_plus_map=source.get('espn_plus_map'),
            )
            print(
                f'ESPN official schedule: {channels} channel feeds and {programmes} programmes '
                f'(ESPN+: collected={stats["espn_plus_collected"]}, '
                f'deduplicated={stats["espn_plus_deduplicated"]}, '
                f'mapped={stats["espn_plus_mapped"]}, '
                f'unmapped={stats["espn_plus_unmapped"]})',
                flush=True,
            )
            return {'exit_code': 0, 'selected_channels': channels, 'stats': stats}
        except (OSError, ValueError) as error:
            message = f'Official ESPN schedule failed: {error}'
            print(message, flush=True)
            return {'exit_code': None, 'error': message}

    if source.get('adapter') == 'pro_teams':
        try:
            channels, programmes, stats = write_pro_teams_guide(
                output / source['output'],
                leagues=source.get('leagues'),
                days=source.get('days'),
            )
            league_summary = ', '.join(
                f'{league.upper()}={counts["channels"]}ch/{counts["programmes"]}p'
                for league, counts in stats['leagues'].items()
            )
            print(
                f'Pro teams official schedule: {channels} team channels and {programmes} programmes '
                f'({league_summary}, fetch_errors={stats["fetch_errors"]})',
                flush=True,
            )
            return {'exit_code': 0, 'selected_channels': channels, 'stats': stats}
        except (OSError, ValueError) as error:
            message = f'Official pro teams schedule failed: {error}'
            print(message, flush=True)
            return {'exit_code': None, 'error': message}

    if source.get('adapter') == 'tvguide':
        try:
            channels, programmes, stats = write_tvguide_guide(
                output / source['output'],
                upstream=upstream,
                lineups=source.get('lineups'),
                max_channels=source.get('max_channels'),
            )
            print(
                f'TV Guide official multi-lineup schedule: {channels} channels and {programmes} programmes '
                f'(deduplicated={stats["deduplicated_simulcasts"]}, skipped_junk={stats["skipped_junk"]}, '
                f'lineup_errors={stats["lineup_errors"]})',
                flush=True,
            )
            return {'exit_code': 0, 'selected_channels': channels, 'stats': stats}
        except (OSError, ValueError) as error:
            message = f'Official TV Guide schedule failed: {error}'
            print(message, flush=True)
            return {'exit_code': None, 'error': message}

    if source.get('adapter') == 'dstv_za':
        try:
            channels, programmes = write_dstv_za_guide(
                output / source['output'],
                upstream=upstream,
            )
            print(f'DStv South Africa official schedule: {channels} channels and {programmes} programmes', flush=True)
            return {'exit_code': 0, 'selected_channels': channels}
        except (OSError, ValueError) as error:
            message = f'Official DStv ZA schedule failed: {error}'
            print(message, flush=True)
            return {'exit_code': None, 'error': message}

    if source.get('adapter') == 'beinsports':
        try:
            channels, programmes = write_beinsports_guide(
                output / source['output'],
                upstream=upstream,
                regions=source.get('regions'),
                countries=source.get('countries'),
            )
            print(f'beIN SPORTS official schedule ({source["id"]}): {channels} channels and {programmes} programmes', flush=True)
            return {'exit_code': 0, 'selected_channels': channels}
        except (OSError, ValueError) as error:
            message = f'Official beIN SPORTS schedule failed: {error}'
            print(message, flush=True)
            return {'exit_code': None, 'error': message}

    if source.get('adapter') == 'dazn':
        try:
            channels, programmes, stats = write_dazn_guide(
                output / source['output'],
                countries=source.get('countries'),
            )
            print(
                f'DAZN official schedule ({source["id"]}): {channels} channels and {programmes} programmes '
                f'(countries={",".join(stats["countries"])}, fetch_errors={stats["fetch_errors"]})',
                flush=True,
            )
            return {'exit_code': 0, 'selected_channels': channels, 'stats': stats}
        except (OSError, ValueError) as error:
            message = f'Official DAZN schedule failed: {error}'
            print(message, flush=True)
            return {'exit_code': None, 'error': message}

    if source.get('adapter') == 'fast':
        try:
            channels, programmes, stats = write_fast_guide(
                output / source['output'],
                upstream=upstream,
                feeds=source.get('feeds'),
                catalogs=source.get('catalogs'),
                max_channels=source.get('max_channels'),
            )
            print(
                f'FAST official schedule ({source["id"]}): {channels} channels and {programmes} programmes '
                f'(feeds_fetched={stats["feeds_fetched"]}, feed_errors={stats["feed_errors"]})',
                flush=True,
            )
            return {'exit_code': 0, 'selected_channels': channels, 'stats': stats}
        except (OSError, ValueError) as error:
            message = f'Official FAST schedule failed: {error}'
            print(message, flush=True)
            return {'exit_code': None, 'error': message}

    if not source['selected_channels']:
        return {'exit_code': None, 'error': 'No channels selected'}

    curr_date = grab_start_date(source)
    days = grab_days(source, curr_date)
    probe_url = source.get('probe_url')
    if probe_url:
        try:
            with urlopen(probe_url.format(date=curr_date), timeout=15):
                pass
        except (URLError, TimeoutError) as error:
            message = f'Public schedule probe failed: {error}'
            print(f"{source['id']}: {message}", flush=True)
            return {'exit_code': None, 'error': message}

    command = [
        'npm', 'run', 'grab', '--', f"--channels={source['channels_file']}",
        f'--days={days}', '--timeout=15000', '--maxConnections=2',
        f"--output={output / source['output']}",
    ]
    env = os.environ.copy()
    env['CURR_DATE'] = curr_date
    try:
        process = subprocess.Popen(command, cwd=upstream, env=env, start_new_session=True)
        try:
            return {'exit_code': process.wait(timeout=600)}
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            return {
                'exit_code': None,
                'error': 'Source exceeded ten-minute budget; process group stopped',
            }
    except OSError as error:
        return {'exit_code': None, 'error': str(error)}


def _source_domain_key(source):
    adapter = source.get('adapter')
    if adapter:
        return f'adapter:{adapter}'
    channels_file = source.get('channels_file')
    if channels_file:
        try:
            first = ET.parse(channels_file).getroot().find('channel')
            site = (first.get('site') or '').strip().lower() if first is not None else ''
            if site:
                return f'site:{site}'
        except (OSError, ET.ParseError):
            pass
    return f'source:{source["id"]}'


def run_all_grabs(manifest, upstream, output, max_workers=3):
    output.mkdir(parents=True, exist_ok=True)
    domain_locks = {}
    for source in manifest:
        key = _source_domain_key(source)
        domain_locks.setdefault(key, threading.Lock())
    results = [None] * len(manifest)
    write_lock = threading.Lock()

    def _run_indexed(index, source):
        lock = domain_locks[_source_domain_key(source)]
        with lock:
            outcome = run_grab(source, upstream, output)
        entry = source | outcome
        with write_lock:
            results[index] = entry
            completed = [r for r in results if r is not None]
            (output / 'grab-results.json').write_text(json.dumps(completed, indent=2) + '\n')
        return entry

    workers = max(1, min(int(max_workers), len(manifest) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_run_indexed, idx, source) for idx, source in enumerate(manifest)]
        for future in as_completed(futures):
            future.result()

    (output / 'grab-results.json').write_text(json.dumps(results, indent=2) + '\n')
    return results


if __name__ == '__main__':
    upstream = Path(sys.argv[1]).resolve()
    manifest = json.loads(Path(sys.argv[2]).read_text())
    output = Path(sys.argv[3]).resolve()
    run_all_grabs(manifest, upstream, output, max_workers=3)
