#!/usr/bin/env python3
"""Generate ``src/connector/iana_registrars.py`` from the IANA registrar CSV.

Reads the IANA Registrar IDs CSV on stdin and writes the vendored Python
module on stdout. Reserved / placeholder rows are dropped.

Usage:
    curl -s https://www.iana.org/assignments/registrar-ids/registrar-ids-1.csv \\
      | python3 tools/gen_iana_registrars.py > src/connector/iana_registrars.py
"""

import csv
import sys

_HEADER = '''"""IANA registrar ID -> registrar name lookup (vendored reference data).

Whisper stores a domain's CURRENT registrar (HAS_REGISTRAR) as a
``REGISTRAR`` node named ``iana:<id>`` — an opaque IANA registrar ID
rather than a human-readable name (historical PREV_REGISTRAR nodes carry
readable ``registrar:<name>`` strings). This table resolves those IDs to
names so the connector emits a meaningful Identity SDO (issue #61).

Source: IANA Registrar IDs registry (public, Apache-compatible terms).
  https://www.iana.org/assignments/registrar-ids/registrar-ids-1.csv
Regenerate:
  curl -s https://www.iana.org/assignments/registrar-ids/registrar-ids-1.csv \\
    | python3 tools/gen_iana_registrars.py > src/connector/iana_registrars.py
"""

'''

_FOOTER = '''

def resolve_registrar_name(name: str) -> str:
    """Resolve a Whisper REGISTRAR node ``name`` to a display name.

    - ``iana:<id>``        -> the IANA registrar name, or ``IANA Registrar
      #<id>`` when the ID isn't in the vendored table (new/unknown).
    - ``registrar:<name>`` -> the trailing name (raw WHOIS string).
    - anything else        -> returned unchanged.
    """
    if name.startswith("iana:"):
        raw = name[len("iana:") :].strip()
        try:
            rid = int(raw)
        except ValueError:
            return name
        resolved = IANA_REGISTRAR_NAMES.get(rid)
        return resolved if resolved else f"IANA Registrar #{rid}"
    if name.lower().startswith("registrar:"):
        return name[len("registrar:") :].strip() or name
    return name
'''


def main() -> None:
    rows = list(csv.reader(sys.stdin))
    data: dict[int, str] = {}
    for r in rows[1:]:  # skip header
        if len(r) < 3:
            continue
        try:
            rid = int(r[0])
        except ValueError:
            continue
        name, status = r[1].strip(), r[2].strip()
        if (
            not name
            or status == "Reserved"
            or name in ("Reserved", "Registry Installation")
        ):
            continue
        data[rid] = name

    out = sys.stdout
    out.write(_HEADER)
    out.write("IANA_REGISTRAR_NAMES: dict[int, str] = {\n")
    for rid in sorted(data):
        out.write("    %d: %r,\n" % (rid, data[rid]))
    out.write("}\n")
    out.write(_FOOTER)


if __name__ == "__main__":
    main()
