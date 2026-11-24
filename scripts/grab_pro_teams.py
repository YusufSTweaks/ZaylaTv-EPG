"""Export official NBA, NHL, NFL, MLB, and UK football club schedules from ESPN's public scoreboard API."""

import json
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SCOREBOARD_URL = 'https://site.api.espn.com/apis/site/v2/sports/{sport_path}/scoreboard'
EASTERN = ZoneInfo('America/New_York')
XMLTV_TIME = '%Y%m%d%H%M%S %z'
MAX_PAYLOAD_BYTES = 8_000_000
DEFAULT_DAYS = 7
DEFAULT_LEAGUES = ('nba', 'nhl', 'nfl', 'mlb')

ESTIMATE_DURATION_NOTE = 'End time estimated from typical game duration.'

LEAGUE_CONFIG = {
    'nba': {
        'label': 'NBA',
        'id_prefix': 'NBATeams',
        'country': 'us',
        'sport_path': 'basketball/nba',
        'duration_minutes': 150,
        'default_days': 7,
        'name_prefixes': ('NBA Teams - ',),
    },
    'nhl': {
        'label': 'NHL',
        'id_prefix': 'NHLTeams',
        'country': 'us',
        'sport_path': 'hockey/nhl',
        'duration_minutes': 150,
        'default_days': 7,
        'name_prefixes': ('NHL Teams - ',),
    },
    'nfl': {
        'label': 'NFL',
        'id_prefix': 'NFLTeams',
        'country': 'us',
        'sport_path': 'football/nfl',
        'duration_minutes': 195,
        'default_days': 14,
        'name_prefixes': ('NFL Teams - ',),
    },
    'mlb': {
        'label': 'MLB',
        'id_prefix': 'MLBTeams',
        'country': 'us',
        'sport_path': 'baseball/mlb',
        'duration_minutes': 180,
        'default_days': 7,
        'name_prefixes': ('MLB Teams - ',),
    },
    'epl': {
        'label': 'Premier League',
        'id_prefix': 'EPLTeams',
        'country': 'uk',
        'sport_path': 'soccer/eng.1',
        'duration_minutes': 120,
        'default_days': 14,
        'name_prefixes': ('EPL - ', 'Premier League - '),
        'supplemental_paths': (
            'soccer/uefa.champions',
            'soccer/uefa.europa',
            'soccer/uefa.europa.conf',
            'soccer/eng.fa',
            'soccer/eng.league_cup',
        ),
    },
    'efl_champ': {
        'label': 'EFL Championship',
        'id_prefix': 'EFLChampTeams',
        'country': 'uk',
        'sport_path': 'soccer/eng.2',
        'duration_minutes': 120,
        'default_days': 14,
        'name_prefixes': ('EFL-CHAMP - ', 'EFL Championship - '),
        'supplemental_paths': (
            'soccer/eng.fa',
            'soccer/eng.league_cup',
        ),
    },
    'sco_prem': {
        'label': 'Scottish Premiership',
        'id_prefix': 'ScottishPremTeams',
        'country': 'uk',
        'sport_path': 'soccer/sco.1',
        'duration_minutes': 120,
        'default_days': 14,
        'name_prefixes': ('Scottish Prem - ',),
        'supplemental_paths': (
            'soccer/uefa.champions',
            'soccer/uefa.europa',
            'soccer/uefa.europa.conf',
        ),
    },
}

SUPPLEMENTAL_LABELS = {
    'soccer/uefa.champions': 'UEFA Champions League',
    'soccer/uefa.europa': 'UEFA Europa League',
    'soccer/uefa.europa.conf': 'UEFA Conference League',
    'soccer/eng.fa': 'FA Cup',
    'soccer/eng.league_cup': 'EFL Cup',
}

# Disambiguate ESPN team IDs that share an abbreviation within the same league (e.g. Dundee vs Dundee United).
TEAM_ID_ABBR_OVERRIDES = {
    ('sco_prem', '264'): 'DUNU',
}

# Franchise / club display-name aliases matching common playlist naming variants and relocations.
EXTRA_TEAM_ALIASES = {
    ('nba', 'LAC'): ('Los Angeles Clippers',),
    ('nhl', 'UTA'): ('Utah Hockey Club', 'Arizona Coyotes'),
    ('nhl', 'UTAH'): ('Utah Hockey Club', 'Arizona Coyotes'),
    ('mlb', 'ATH'): ('Oakland Athletics', 'Sacramento Athletics'),
    ('mlb', 'OAK'): ('Oakland Athletics', 'Sacramento Athletics'),
    ('epl', 'BHA'): ('Brighton',),
    ('epl', 'MAN'): ('Manchester Utd',),
    ('epl', 'NEW'): ('Newcastle Utd',),
    ('epl', 'TOT'): ('Tottenham',),
    ('efl_champ', 'BOL'): ('Bolton',),
    ('efl_champ', 'WHU'): ('West Ham',),
    ('efl_champ', 'WOL'): ('Wolves',),
    ('sco_prem', 'ABE'): ('Aberdeen FC', 'Aberdeen FC (Reds TV)'),
    ('sco_prem', 'CEL'): ('Celtic TV', 'Celtic FC'),
    ('sco_prem', 'DUN'): ('Dundee FC',),
    ('sco_prem', 'DUNU'): ('Dundee United FC',),
    ('sco_prem', 'FALK'): ('Falkirk FC',),
    ('sco_prem', 'HIBS'): ('Hibs', 'Hibs FC', 'Hibernian FC'),
    ('sco_prem', 'HOM'): ('Hearts', 'Hearts FC'),
    ('sco_prem', 'KIL'): ('Kilmarnock FC', 'Kilimarnock FC'),
    ('sco_prem', 'MOT'): ('Motherwell FC',),
    ('sco_prem', 'RAN'): ('Rangers TV', 'Rangers FC'),
    ('sco_prem', 'STJ'): ('St. Johnstone', 'St. Johnstone FC'),
    ('sco_prem', 'STM'): ('St. Mirren', 'St. Mirren FC'),
}

NON_FRANCHISE_ABBRS = frozenset({
    'TBD', 'TBA', 'AFC', 'NFC', 'AL', 'NL', 'EAST', 'WEST', 'WORLD', 'USA',
})
SKIPPED_STATUSES = frozenset({'STATUS_POSTPONED', 'STATUS_CANCELED'})


def _clean_text(value):
    if value is None or isinstance(value, bool):
        return ''
    return re.sub(r'\s+', ' ', str(value).strip())


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


def _normalize_leagues(leagues):
    if leagues is None:
        return list(DEFAULT_LEAGUES)
    cleaned = []
    seen = set()
    for item in leagues:
        key = _clean_text(item).lower()
        if key not in LEAGUE_CONFIG:
            raise ValueError(f'Unsupported pro_teams league: {item!r}')
        if key not in seen:
            seen.add(key)
            cleaned.append(key)
    if not cleaned:
        raise ValueError('At least one league must be configured for pro_teams')
    return cleaned


def fetch_scoreboard_day(sport_path, day_date):
    url = f'{SCOREBOARD_URL.format(sport_path=sport_path)}?{urlencode({"dates": day_date.strftime("%Y%m%d")})}'
    request = Request(
        url,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0',
        },
    )
    with urlopen(request, timeout=15) as response:
        payload = response.read(MAX_PAYLOAD_BYTES + 1)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f'ESPN scoreboard response exceeds {MAX_PAYLOAD_BYTES // 1_000_000} MB')
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise ValueError('Expected JSON object from ESPN scoreboard API')
    events = parsed.get('events')
    if events is None:
        return []
    if not isinstance(events, list):
        raise ValueError('Expected list of events from ESPN scoreboard API')
    return events


def _extract_broadcasts(competition, home_away=None):
    preferred_markets = {'national'}
    if home_away in {'home', 'away'}:
        preferred_markets.add(home_away)

    preferred = []
    fallback = []

    def _add(target, name):
        text = _clean_text(name)
        if text and text.casefold() not in {x.casefold() for x in target}:
            target.append(text)

    broadcasts = competition.get('broadcasts')
    if isinstance(broadcasts, list):
        for item in broadcasts:
            if not isinstance(item, dict):
                continue
            market = _clean_text(item.get('market')).lower()
            names = item.get('names')
            if not isinstance(names, list):
                continue
            for name in names:
                if market in preferred_markets:
                    _add(preferred, name)
                _add(fallback, name)

    if not preferred and not fallback:
        geo = competition.get('geoBroadcasts')
        if isinstance(geo, list):
            for item in geo:
                if not isinstance(item, dict):
                    continue
                market_obj = item.get('market')
                market = _clean_text(market_obj.get('type') if isinstance(market_obj, dict) else '').lower()
                media = item.get('media')
                name = media.get('shortName') if isinstance(media, dict) else None
                if market in preferred_markets:
                    _add(preferred, name)
                _add(fallback, name)

    chosen = preferred or fallback
    return ', '.join(chosen) if chosen else None


def _extract_venue(competition):
    venue = competition.get('venue')
    if not isinstance(venue, dict):
        return None
    full_name = _clean_text(venue.get('fullName'))
    if not full_name:
        return None
    address = venue.get('address')
    if isinstance(address, dict):
        city = _clean_text(address.get('city'))
        state = _clean_text(address.get('state'))
        if city and re.search(rf'\({re.escape(city)}\)\s*$', full_name, re.I):
            full_name = re.sub(rf'\s*\({re.escape(city)}\)\s*$', '', full_name, flags=re.I).strip()
        loc = ', '.join(part for part in (city, state) if part)
        if loc:
            return f'{full_name} ({loc})'
    return full_name


def _team_display_names(league_key, name_prefixes, abbr, display_name, short_name):
    names = []
    seen = set()

    def _append(candidate):
        text = _clean_text(candidate)
        if not text:
            return
        folded = text.casefold()
        if folded not in seen:
            seen.add(folded)
            names.append(text)

    variants = [display_name]
    if short_name:
        variants.append(short_name)
    variants.extend(EXTRA_TEAM_ALIASES.get((league_key, abbr), ()))
    for prefix in name_prefixes:
        for variant in variants:
            _append(f'{prefix}{variant}')
    return names


def _build_description(label, title, venue=None, broadcast=None):
    parts = [f'{label}: {title}']
    if venue:
        parts[0] = f'{parts[0]} — {venue}'
    if broadcast:
        parts.append(f'Broadcast: {broadcast}')
    parts.append(ESTIMATE_DURATION_NOTE.rstrip('.'))
    return '. '.join(parts) + '.'


def _iter_valid_events(events, duration, cutoff_stop):
    seen_event_ids = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = _clean_text(event.get('id'))
        if event_id:
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)

        competitions = event.get('competitions')
        if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):
            continue
        competition = competitions[0]

        status_obj = competition.get('status') or event.get('status')
        status_name = ''
        if isinstance(status_obj, dict) and isinstance(status_obj.get('type'), dict):
            status_name = _clean_text(status_obj['type'].get('name'))
        if status_name in SKIPPED_STATUSES:
            continue

        start = _parse_iso(competition.get('date') or event.get('date'))
        if start is None:
            continue
        start_utc = start.astimezone(timezone.utc)
        stop_utc = start_utc + duration
        if stop_utc <= cutoff_stop:
            continue

        competitors = competition.get('competitors')
        if not isinstance(competitors, list) or len(competitors) < 2:
            continue

        title = _clean_text(event.get('name'))
        venue = _extract_venue(competition)

        parsed_competitors = []
        away_name = ''
        home_name = ''
        for comp in competitors:
            if not isinstance(comp, dict):
                continue
            team = comp.get('team')
            if not isinstance(team, dict):
                continue
            team_id = _clean_text(team.get('id'))
            abbr = re.sub(r'[^A-Za-z0-9]', '', _clean_text(team.get('abbreviation'))).upper()
            display_name = _clean_text(team.get('displayName'))
            short_name = _clean_text(team.get('shortDisplayName') or team.get('name'))
            home_away = _clean_text(comp.get('homeAway')).lower()
            if home_away == 'away' and display_name:
                away_name = display_name
            elif home_away == 'home' and display_name:
                home_name = display_name
            if (
                not abbr
                or abbr in NON_FRANCHISE_ABBRS
                or not display_name
                or display_name.upper() in {'TBD', 'TBA'}
                or display_name.lower().startswith('team ')
                or 'all-star' in display_name.lower()
                or 'all star' in display_name.lower()
                or team_id.startswith('-')
            ):
                continue
            parsed_competitors.append((team_id, abbr, display_name, short_name, home_away))

        if not title:
            if away_name and home_name:
                title = f'{away_name} at {home_name}'
            else:
                continue

        yield start_utc, stop_utc, title, venue, competition, parsed_competitors


def build_guide_data(events_by_league, now, leagues=DEFAULT_LEAGUES, supplemental_events_by_path=None):
    selected_leagues = _normalize_leagues(leagues)
    supplemental_events_by_path = supplemental_events_by_path or {}
    now_utc = now.astimezone(timezone.utc)
    cutoff_stop = now_utc - timedelta(hours=12)

    channel_names = {}
    channel_league = {}
    raw_by_channel = {}
    league_team_ids = {key: {} for key in selected_leagues}
    league_stats = {key: {'channels': 0, 'programmes': 0} for key in selected_leagues}

    for league_key in selected_leagues:
        cfg = LEAGUE_CONFIG[league_key]
        label = cfg['label']
        id_prefix = cfg['id_prefix']
        country = cfg.get('country', 'us')
        name_prefixes = cfg.get('name_prefixes', (f'{label} Teams - ',))
        duration = timedelta(minutes=cfg['duration_minutes'])

        for start_utc, stop_utc, title, venue, competition, competitors in _iter_valid_events(
            events_by_league.get(league_key, ()), duration, cutoff_stop
        ):
            for team_id, abbr, display_name, short_name, home_away in competitors:
                resolved_abbr = TEAM_ID_ABBR_OVERRIDES.get((league_key, team_id), abbr)
                channel_id = f'{id_prefix}.{resolved_abbr}.{country}'
                channel_names[channel_id] = _team_display_names(
                    league_key, name_prefixes, resolved_abbr, display_name, short_name
                )
                channel_league[channel_id] = league_key
                if team_id:
                    league_team_ids[league_key][team_id] = channel_id
                broadcast = _extract_broadcasts(competition, home_away=home_away)
                desc = _build_description(label, title, venue=venue, broadcast=broadcast)
                raw_by_channel.setdefault(channel_id, []).append({
                    'start': start_utc,
                    'stop': stop_utc,
                    'title': title,
                    'sub_title': broadcast,
                    'desc': desc,
                })

        # Attach supplemental competition fixtures (e.g. UEFA Champions/Europa League, FA/EFL Cup)
        # strictly to clubs already discovered in this primary league — never create foreign club channels.
        known_clubs = league_team_ids[league_key]
        if known_clubs:
            for supp_path in cfg.get('supplemental_paths', ()):
                supp_events = supplemental_events_by_path.get(supp_path, ())
                if not supp_events:
                    continue
                supp_label = SUPPLEMENTAL_LABELS.get(supp_path, label)
                for start_utc, stop_utc, title, venue, competition, competitors in _iter_valid_events(
                    supp_events, duration, cutoff_stop
                ):
                    for team_id, _, _, _, home_away in competitors:
                        channel_id = known_clubs.get(team_id)
                        if not channel_id:
                            continue
                        broadcast = _extract_broadcasts(competition, home_away=home_away)
                        desc = _build_description(supp_label, title, venue=venue, broadcast=broadcast)
                        raw_by_channel.setdefault(channel_id, []).append({
                            'start': start_utc,
                            'stop': stop_utc,
                            'title': title,
                            'sub_title': broadcast,
                            'desc': desc,
                        })

    channels = {}
    programmes = {}

    for channel_id, items in sorted(raw_by_channel.items()):
        # Sort chronologically and deduplicate identical start/title entries.
        items.sort(key=lambda entry: (entry['start'], entry['title']))
        deduped = []
        seen_keys = set()
        for item in items:
            key = (item['start'], item['title'].casefold())
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(item)

        # Clamp overlaps (e.g. MLB doubleheaders) against the next game's start time.
        clamped = []
        for idx, item in enumerate(deduped):
            start = item['start']
            stop = item['stop']
            if idx + 1 < len(deduped):
                next_start = deduped[idx + 1]['start']
                if next_start > start and stop > next_start:
                    stop = next_start
            if stop > start:
                clamped.append((start, stop, item['title'], item['sub_title'], item['desc']))

        if not any(stop > now_utc for _, stop, _, _, _ in clamped):
            continue

        channels[channel_id] = channel_names[channel_id]
        league_key = channel_league[channel_id]
        league_stats[league_key]['channels'] += 1
        for start, stop, title, sub_title, desc in clamped:
            programmes[(channel_id, start, stop)] = (title, sub_title, desc)
            league_stats[league_key]['programmes'] += 1

    stats = {
        'leagues': league_stats,
        'fetch_errors': 0,
    }
    return channels, programmes, stats


def write_guide(output, now=None, leagues=None, days=None):
    now = now or datetime.now(EASTERN)
    selected_leagues = _normalize_leagues(leagues)
    days_override = None
    if days is not None:
        days_override = int(days)
        if not 1 <= days_override <= 14:
            raise ValueError('pro_teams days must be between 1 and 14')

    # Anchor to Eastern time minus 6 hours so late-night West Coast games still live
    # after midnight ET remain covered on the first day bucket.
    start_day = (now.astimezone(EASTERN) - timedelta(hours=6)).date()
    days_by_league = {
        league_key: (days_override if days_override is not None else LEAGUE_CONFIG[league_key]['default_days'])
        for league_key in selected_leagues
    }

    # Deduplicate (sport_path, day_date) requests across primary leagues and shared supplemental cups.
    path_max_days = {}
    for league_key in selected_leagues:
        cfg = LEAGUE_CONFIG[league_key]
        d = days_by_league[league_key]
        path_max_days[cfg['sport_path']] = max(path_max_days.get(cfg['sport_path'], 0), d)
        for supp_path in cfg.get('supplemental_paths', ()):
            path_max_days[supp_path] = max(path_max_days.get(supp_path, 0), d)

    requests = [
        (sport_path, start_day + timedelta(days=offset))
        for sport_path, max_d in path_max_days.items()
        for offset in range(max_d)
    ]

    events_by_path = {sport_path: [] for sport_path in path_max_days}
    fetch_errors = 0

    def _fetch_one(task):
        sport_path, day_date = task
        try:
            return sport_path, fetch_scoreboard_day(sport_path, day_date), False
        except (OSError, ValueError):
            return sport_path, [], True

    with ThreadPoolExecutor(max_workers=4) as executor:
        for sport_path, events, failed in executor.map(_fetch_one, requests):
            if failed:
                fetch_errors += 1
            else:
                events_by_path[sport_path].extend(events)

    events_by_league = {
        league_key: events_by_path.get(LEAGUE_CONFIG[league_key]['sport_path'], [])
        for league_key in selected_leagues
    }
    channels, programmes, stats = build_guide_data(
        events_by_league,
        now,
        leagues=selected_leagues,
        supplemental_events_by_path=events_by_path,
    )
    stats['days_by_league'] = days_by_league
    stats['fetch_errors'] = fetch_errors

    now_utc = now.astimezone(timezone.utc)
    if not any(stop > now_utc for _, _, stop in programmes):
        raise ValueError('Pro teams schedule returned no usable current or upcoming programmes')

    root = ET.Element('tv')
    for channel_id, display_names in sorted(channels.items()):
        definition = ET.SubElement(root, 'channel', id=channel_id)
        for name in display_names:
            ET.SubElement(definition, 'display-name').text = name
    for (channel_id, start, stop), (title, sub_title, desc) in sorted(programmes.items()):
        programme = ET.SubElement(
            root, 'programme', channel=channel_id,
            start=start.astimezone(timezone.utc).strftime(XMLTV_TIME),
            stop=stop.astimezone(timezone.utc).strftime(XMLTV_TIME),
        )
        ET.SubElement(programme, 'title', lang='en').text = title
        if sub_title:
            ET.SubElement(programme, 'sub-title', lang='en').text = sub_title
        if desc:
            ET.SubElement(programme, 'desc', lang='en').text = desc
    ET.ElementTree(root).write(output, encoding='utf-8', xml_declaration=True)
    return len(channels), len(programmes), stats
