"""Mastcam-Z PDS discovery, integrity-checked downloads and metadata export."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
import threading
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

API = 'https://pds-imaging.jpl.nasa.gov/api'
BUNDLES = {k: 'mars2020_mastcamz_' + k for k in ('sci_calibrated', 'ops_calibrated', 'ops_raw')}

class Cancelled(Exception):
    pass

def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise Cancelled('Cancelled')

def request(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={
        'User-Agent': 'MarsTerrainBrowser/1.0', 'Content-Type': 'application/json'})
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=45)
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)

def json_request(url, body=None):
    with request(url, body) as response:
        return json.load(response)

def resolve(uri):
    result = json_request(API + '/data/' + urllib.parse.quote(uri, safe=':/') + '?output=url')
    url = result['url']
    if urllib.parse.urlparse(url).scheme != 'https':
        raise ValueError('Archive returned a non-HTTPS URL')
    return url

def find_preview(product):
    prefix = Path(product.name).stem[:-2]
    body = {'size': 100, '_source': ['uri', 'archive'], 'query': {'bool': {'must': [
        {'term': {'archive.bundle_id': product.bundle}},
        {'wildcard': {'archive.name': prefix + '*.png'}}]}}}
    hits = json_request(API + '/search/atlas/_search', body)['hits']['hits']
    candidates = [h['_source'] for h in hits if '/browse/' in h['_source']['uri']]
    if not candidates:
        raise ValueError('No browse PNG is available for this product')
    return sorted(candidates, key=lambda e: e['archive']['name'], reverse=True)[0]

@dataclass
class Product:
    name: str
    uri: str
    size: int
    md5: str
    bundle: str
    camera: str
    sol: int
    sequence: str
    kind: str
    geometry: str
    thumbnail: bool
    downsample: int
    compression: str
    focal_mm: int
    version: int
    image_uri: str = ''
    image_size: int = 0
    image_md5: str = ''

    @property
    def full_resolution(self):
        return not self.thumbnail and self.downsample == 0

def decode(entry):
    a = entry['archive']
    name = a['name']
    # Mars 2020 54-character stem, optionally followed by a two-digit version.
    m = re.fullmatch(r'(Z[LR])([0-7])_(\d{4})_(\d{10})_(\d{3})([A-Z]{3})([_A-Z])([NT])(\d{3})(\d{4})(ZCAM\d{5})([_0-9])(\d{3})(\d)([A-Z0-9]{2})([A-Z])(\d{2})?\.[^.]+', name, re.I)
    if not m:
        return None
    g = m.groups()
    return Product(name, entry['uri'], int(a.get('size', 0)), a.get('md5', ''),
                   a['bundle_id'], g[0].upper(), int(g[2]), g[10].upper(),
                   g[5].upper(), g[6], g[7].upper() == 'T', int(g[13]),
                   g[14].upper(), int(g[12]), int(g[16] or 0))

def discover(sol=693, sequence='ZCAM08689', source='sci_calibrated', camera='Both',
             full_only=True, latest=True, kind='All', progress=lambda *_: None, cancel=None):
    if sol < 0 or sol > 9999:
        raise ValueError('Sol must be between 0 and 9999')
    sequence = sequence.strip().upper()
    if sequence and not re.fullmatch(r'ZCAM\d{5}', sequence):
        raise ValueError('Sequence must be ZCAM followed by five digits, or blank')
    bundle = BUNDLES[source]
    must = [{'term': {'archive.bundle_id': bundle}},
            {'wildcard': {'archive.name': f'*{sol:04d}*{sequence}*'}}]
    entries = {}
    offset = 0
    while True:
        check_cancel(cancel)
        body = {'size': 250, 'from': offset, 'query': {'bool': {'must': must}},
                '_source': ['uri', 'archive'], 'track_total_hits': True,
                'sort': [{'archive.name': 'asc'}, {'release_id_num': 'desc'}],
                'collapse': {'field': 'uri'}}
        response = json_request(API + '/search/atlas/_search', body)
        if response.get('timed_out') or response.get('_shards', {}).get('failed', 0):
            raise RuntimeError('NASA catalogue query was incomplete; retry the search')
        hits = response['hits']['hits']
        for hit in hits:
            e = hit['_source']
            if '/data/' in e['uri']:
                entries[e['uri']] = e
        progress('search', len(entries), response['hits']['total']['value'])
        if len(hits) < 250:
            break
        offset += len(hits)
        if offset >= response['hits']['total']['value']:
            break
        if offset >= 10000:
            raise ValueError('More than 10,000 catalogue entries; narrow the search to a sequence')
    products = []
    by_stem = {}
    for entry in entries.values():
        if not entry['archive']['name'].lower().endswith('.xml'):
            continue
        p = decode(entry)
        if not p or p.sol != sol or (sequence and p.sequence != sequence) or (full_only and not p.full_resolution):
            continue
        if camera != 'Both' and p.camera != camera:
            continue
        if kind != 'All' and p.kind != kind:
            continue
        # IMG case is defined by the actual catalogue, rather than guessed.
        stem = p.uri.rsplit('.', 1)[0]
        for ext in ('.IMG', '.img'):
            image = entries.get(stem + ext)
            if image:
                p.image_uri = image['uri']
                p.image_size = int(image['archive'].get('size', 0))
                p.image_md5 = image['archive'].get('md5', '')
                break
        key = re.sub(r'\d{2}$', '', Path(p.name).stem) if len(Path(p.name).stem) == 54 else Path(p.name).stem
        if latest:
            if key not in by_stem or p.version > by_stem[key].version:
                by_stem[key] = p
        else:
            products.append(p)
    return sorted(list(by_stem.values()) if latest else products, key=lambda p: p.name)

def local(tag):
    return tag.rsplit('}', 1)[-1]

def nodes(root, tag):
    return [e for e in root.iter() if local(e.tag) == tag]

def value(root, tag, default=''):
    return next(((e.text or '').strip() for e in root.iter() if local(e.tag) == tag), default)

def metadata(label):
    root = ET.parse(label).getroot()
    leaves = []
    def walk(e, path):
        path = path + '/' + local(e.tag)
        if not len(e):
            leaves.append({'path': path, 'value': (e.text or '').strip(), 'attributes': e.attrib})
        for child in e:
            walk(child, path)
    walk(root, '')
    arrays = []
    files = []
    for area in nodes(root, 'File_Area_Observational'):
        filename = value(area, 'file_name')
        if not filename or Path(filename).name != filename or '\\' in filename:
            raise ValueError('Unsafe or missing image filename in label')
        files.append(filename)
        for e in area:
            if local(e.tag).startswith('Array_') and local(e.tag).endswith('Image'):
                axes = sorted([{'name': value(a, 'axis_name'), 'elements': int(value(a, 'elements')),
                                'sequence': int(value(a, 'sequence_number'))} for a in nodes(e, 'Axis_Array')],
                              key=lambda a: a['sequence'])
                arrays.append({'file': filename, 'offset': int(value(e, 'offset')),
                    'dtype': value(e, 'data_type'), 'axes': axes,
                    'order': value(e, 'axis_index_order'),
                    'scale': float(value(e, 'scaling_factor', '1')),
                    'value_offset': float(value(e, 'value_offset', '0')),
                    'special': [float(c.text) for s in nodes(e, 'Special_Constants') for c in s if c.text]})
    camera = nodes(root, 'Camera_Model_Parameters')
    model = camera[0] if camera else ET.Element('empty')
    vectors = {}
    for key in ('Vector_Center', 'Vector_Axis', 'Vector_Horizontal', 'Vector_Vertical', 'Vector_Optical', 'Radial_Terms'):
        found = nodes(model, key)
        if found:
            vectors[key] = {local(e.tag): {'value': (e.text or '').strip(), 'unit': e.get('unit', '')} for e in found[0]}
    return {'logical_identifier': value(root, 'logical_identifier'), 'start_time': value(root, 'start_date_time'),
        'focal_length_mm': value(root, 'focal_length'), 'exposure_ms': value(root, 'exposure_duration'),
        'filter': value(root, 'filter_name'), 'compression': value(root, 'onboard_compression_type'),
        'bayer_type': value(root, 'color_filter_array_type'), 'bayer_state': value(root, 'color_filter_array_state'),
        'first_line': int(value(root, 'first_line', '1')), 'first_sample': int(value(root, 'first_sample', '1')),
        'camera_model': value(model, 'model_type'), 'camera_frame': value(model, 'coordinate_space_frame_type'),
        'camera_vectors': vectors, 'files': files, 'arrays': arrays, 'label_values': leaves}

def digest(path, algorithm='md5'):
    h = hashlib.new(algorithm)
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

def download(url, path, expected_size=0, expected_md5='', progress=lambda *_: None, cancel=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and ((expected_size and path.stat().st_size == expected_size) or expected_md5):
        if expected_md5 and digest(path) != expected_md5.lower():
            pass
        else:
            return 'verified existing'
    part = path.with_suffix(path.suffix + '.part')
    # Restart interrupted files; never append an unvalidated remote representation.
    with request(url) as response, part.open('wb') as f:
        total = int(response.headers.get('Content-Length', 0))
        done = 0
        h = hashlib.md5()
        for chunk in iter(lambda: response.read(256 * 1024), b''):
            check_cancel(cancel)
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress('bytes', done, total or expected_size)
    if (total and done != total) or (expected_size and done != expected_size):
        raise IOError('Downloaded file size differs from archive size')
    if expected_md5 and h.hexdigest() != expected_md5.lower():
        raise IOError('Downloaded file failed archive MD5 validation')
    part.replace(path)
    return 'downloaded'

def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    temporary.replace(path)

MANIFEST_FIELDS = ['product', 'camera', 'sol', 'sequence', 'product_type', 'image_path', 'tiff_path',
    'label_path', 'width', 'height', 'bands', 'focal_length_mm', 'exposure_ms', 'filter',
    'start_time', 'compression', 'camera_model', 'camera_frame', 'camera_vectors_json',
    'metadata_path', 'source_uri', 'source_url', 'md5', 'sha256', 'status', 'error']

def write_manifest(output, records):
    path = Path(output) / 'realitycapture_manifest.csv'
    temporary = path.with_suffix('.csv.tmp')
    with temporary.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, MANIFEST_FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)
    temporary.replace(path)
    image_list = Path(output) / 'reconstruction_images.txt'
    image_list.write_text('\n'.join(r['tiff_path'] for r in records if r.get('tiff_path')) + '\n', encoding='utf-8')

def acquire(products, output, convert=False, progress=lambda *_: None, cancel=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / 'realitycapture_manifest.csv'
    records = []
    if manifest.exists():
        with manifest.open(encoding='utf-8-sig', newline='') as f:
            records = list(csv.DictReader(f))
    for index, p in enumerate(products):
        check_cancel(cancel)
        progress('product', index, len(products), p.name)
        base = output / p.bundle / p.camera
        label = base / p.name
        record = {'product': p.name, 'camera': p.camera, 'sol': p.sol, 'sequence': p.sequence,
                  'product_type': p.kind, 'source_uri': p.uri, 'label_path': str(label), 'status': 'failed'}
        try:
            label_url = resolve(p.uri)
            download(label_url, label, p.size, p.md5, progress, cancel)
            info = metadata(label)
            metadata_path = label.with_suffix('.metadata.json')
            save_json(metadata_path, info)
            record.update({k: info.get(k, '') for k in ('focal_length_mm', 'exposure_ms', 'filter',
                         'start_time', 'compression', 'camera_model', 'camera_frame')})
            record['camera_vectors_json'] = json.dumps(info['camera_vectors'], separators=(',', ':'))
            record['metadata_path'] = str(metadata_path)
            for filename in info['files']:
                check_cancel(cancel)
                url = resolve(p.image_uri) if p.image_uri and len(info['files']) == 1 else urllib.parse.urljoin(label_url, urllib.parse.quote(filename))
                image = base / filename
                download(url, image, p.image_size, p.image_md5, progress, cancel)
                record.update(image_path=str(image), source_url=url, md5=p.image_md5, sha256=digest(image, 'sha256'))
            if info['arrays']:
                axes = {a['name']: a['elements'] for a in info['arrays'][0]['axes']}
                record.update(width=axes.get('Sample', ''), height=axes.get('Line', ''), bands=axes.get('Band', 1))
            record['status'] = 'downloaded'
            if convert:
                from mars_convert import convert_product
                record['tiff_path'] = str(convert_product(label, output / 'images' / p.bundle / p.camera))
                record['status'] = 'ready'
        except Cancelled:
            record.update(status='cancelled', error='Cancelled; retry to continue')
            raise
        except Exception as exc:
            record['error'] = str(exc)
            if record.get('image_path'):
                record['status'] = 'downloaded; conversion failed'
            progress('error', p.name, str(exc))
        finally:
            records = [r for r in records if r['source_uri'] != p.uri] + [record]
            write_manifest(output, records)
    progress('product', len(products), len(products), 'Finished')
    return records

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sol', type=int, default=693)
    parser.add_argument('--sequence', default='ZCAM08689')
    parser.add_argument('--source', choices=BUNDLES, default='sci_calibrated')
    parser.add_argument('--camera', choices=['Both', 'ZL', 'ZR'], default='Both')
    parser.add_argument('--kind', default='RAD', help='RAD, IOF, EDR, or All')
    parser.add_argument('--output', type=Path, default=Path('downloads'))
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--convert', action='store_true')
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()
    if args.limit < 0:
        parser.error('--limit must be zero or positive')
    products = discover(args.sol, args.sequence, args.source, args.camera, kind=args.kind)
    args.output.mkdir(parents=True, exist_ok=True)
    save_json(args.output / 'catalogue.json', [asdict(p) for p in products])
    print(f'{len(products)} products; ZL={sum(p.camera == "ZL" for p in products)}, ZR={sum(p.camera == "ZR" for p in products)}')
    if args.download:
        records = acquire(products[:args.limit] if args.limit else products, args.output, args.convert,
                          progress=lambda *event: print(*event) if event[0] != 'bytes' else None)
        if any(r.get('error') for r in records):
            raise SystemExit(1)

if __name__ == '__main__':
    main()
