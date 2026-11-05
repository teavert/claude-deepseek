"""Fingerprint configuration without shell-dependent Python command quoting."""
import hashlib
import json
import sys
from pathlib import Path


if __name__ == '__main__':
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8-sig'))
    print(hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest())
