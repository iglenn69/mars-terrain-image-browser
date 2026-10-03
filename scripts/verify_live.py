"""Opt-in download/conversion/preview smoke test using a saved live catalogue."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mars_pds import Product, acquire, find_preview, resolve, download

products = [Product(**p) for p in json.loads(Path('downloads/sol693/catalogue.json').read_text())]
selected = [next(p for p in products if p.camera == eye) for eye in ('ZL', 'ZR')]
records = acquire(selected, 'downloads/verified-samples', True,
                  progress=lambda *e: print(*e, flush=True) if e[0] != 'bytes' else None)
assert all(r['status'] == 'ready' and r['md5'] for r in records), records
preview = find_preview(selected[0])
path = Path('downloads/verified-samples/browse.png')
download(resolve(preview['uri']), path, preview['archive']['size'], preview['archive'].get('md5', ''))
print('Verified ZL + ZR original checksums, labels, TIFFs, manifests and browse preview.', flush=True)
