"""Fix OnTVTonight carry-over overnight rows shifting entire daily schedules +24h."""

import re
import sys
from pathlib import Path


TARGET_PATTERN = re.compile(
    rb'  parser\(\{ content, date, channel \}\) \{\r?\n'
    rb'    const programs = \[\]\r?\n'
    rb'    const \[\$, items\] = parseItems\(content\)\r?\n'
    rb'    items\.forEach\(item => \{\r?\n'
    rb'      const prev = programs\[programs\.length - 1\]\r?\n'
    rb'      const \$item = \$\(item\)\r?\n'
    rb'      let start = parseStart\(\$item, date, channel\)\r?\n'
    rb'      if \(prev\) \{\r?\n'
    rb'        if \(start\.isBefore\(prev\.start\)\) \{\r?\n'
    rb'          start = start\.add\(1, \'d\'\)\r?\n'
    rb'          date = date\.add\(1, \'d\'\)\r?\n'
    rb'        \}\r?\n'
    rb'        prev\.stop = start\r?\n'
    rb'      \}\r?\n'
)

REPLACEMENT = (
    b'  parser({ content, date, channel }) {\n'
    b'    const programs = []\n'
    b'    const [$, items] = parseItems(content)\n'
    b'    let prevNightCount = 0\n'
    b'    if (items.length >= 10) {\n'
    b'      const first = parseStart($(items[0]), date, channel)\n'
    b'      if (first.hour() >= 18) {\n'
    b'        let prevRaw = first\n'
    b'        for (let i = 1; i < Math.min(items.length, 6); i++) {\n'
    b'          const curRaw = parseStart($(items[i]), date, channel)\n'
    b'          if (curRaw.isBefore(prevRaw) && curRaw.hour() < 6) {\n'
    b'            prevNightCount = i\n'
    b'            break\n'
    b'          }\n'
    b'          prevRaw = curRaw\n'
    b'        }\n'
    b'      }\n'
    b'    }\n'
    b'    items.forEach((item, index) => {\n'
    b'      const prev = programs[programs.length - 1]\n'
    b'      const $item = $(item)\n'
    b'      let start = parseStart($item, date, channel)\n'
    b'      if (index < prevNightCount) {\n'
    b'        start = start.subtract(1, \'d\')\n'
    b'      } else if (prev) {\n'
    b'        if (start.isBefore(prev.start)) {\n'
    b'          start = start.add(1, \'d\')\n'
    b'          date = date.add(1, \'d\')\n'
    b'        }\n'
    b'      }\n'
    b'      if (prev) {\n'
    b'        prev.stop = start\n'
    b'      }\n'
)


def patch_ontvtonight(upstream_dir):
    adapter = Path(upstream_dir) / 'sites/ontvtonight.com/ontvtonight.com.config.js'
    content = adapter.read_bytes()
    updated, count = TARGET_PATTERN.subn(REPLACEMENT, content)
    if count != 1:
        raise SystemExit('Unexpected OnTVTonight adapter. Review the pinned upstream version.')
    adapter.write_bytes(updated)


if __name__ == '__main__':
    patch_ontvtonight(sys.argv[1])
