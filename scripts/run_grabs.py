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


def run_grab(source, upstream, output):
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
