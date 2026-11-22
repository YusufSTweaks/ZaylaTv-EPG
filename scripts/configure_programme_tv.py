"""Use public Programme-TV.net pages without the upstream fixed session cookie."""

import re
import sys
from pathlib import Path


adapter = Path(sys.argv[1]) / 'sites/programme-tv.net/programme-tv.net.config.js'
content = adapter.read_bytes()
content, removed_schedule_cookie = re.subn(
    rb'  request: \{\r?\n    headers: \{\r?\n      cookie: [^\r\n]+\r?\n    \}\r?\n  \},\r?\n',
    b'',
    content,
)
content, removed_catalog_cookie = re.subn(
    rb',\r?\n        \{\r?\n          headers: \{\r?\n            cookie: [^\r\n]+\r?\n          \}\r?\n        \}',
    b'',
    content,
)
if removed_schedule_cookie != 1 or removed_catalog_cookie != 1:
    raise SystemExit('Unexpected Programme-TV.net adapter. Review the pinned upstream version.')
adapter.write_bytes(content)
