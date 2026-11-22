"""Run bounded source grabs and record their outcomes."""

import json
import os
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from grab_espn import write_guide as write_espn_guide
from grab_tsn import write_guide


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
            channels, programmes = write_espn_guide(
                output / source['output'],
                include_espn_plus=include_espn_plus,
            )
            print(f'ESPN official schedule: {channels} channel feeds and {programmes} programmes', flush=True)
            return {'exit_code': 0, 'selected_channels': channels}
        except (OSError, ValueError) as error:
            message = f'Official ESPN schedule failed: {error}'
            print(message, flush=True)
            return {'exit_code': None, 'error': message}

    if not source['selected_channels']:
        return {'exit_code': None, 'error': 'No channels selected'}

    probe_url = source.get('probe_url')
    if probe_url:
        date = datetime.now(timezone.utc).date().isoformat()
        try:
            with urlopen(probe_url.format(date=date), timeout=15):
                pass
        except (URLError, TimeoutError) as error:
            message = f'Public schedule probe failed: {error}'
            print(f"{source['id']}: {message}", flush=True)
            return {'exit_code': None, 'error': message}

    command = [
        'npm', 'run', 'grab', '--', f"--channels={source['channels_file']}",
        '--days=2', '--timeout=15000', '--maxConnections=2',
        f"--output={output / source['output']}",
    ]
    try:
        process = subprocess.Popen(command, cwd=upstream, start_new_session=True)
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


if __name__ == '__main__':
    upstream = Path(sys.argv[1]).resolve()
    manifest = json.loads(Path(sys.argv[2]).read_text())
    output = Path(sys.argv[3]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for source in manifest:
        results.append(source | run_grab(source, upstream, output))
        (output / 'grab-results.json').write_text(json.dumps(results, indent=2) + '\n')
