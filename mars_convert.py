"""Convert label-described uncompressed PDS image arrays without discarding originals."""
from pathlib import Path
import numpy as np
import tifffile
from mars_pds import metadata, save_json

DTYPES = {'SignedMSB2': '>i2', 'SignedLSB2': '<i2', 'UnsignedMSB2': '>u2',
          'UnsignedLSB2': '<u2', 'UnsignedByte': 'u1', 'SignedByte': 'i1',
          'IEEE754MSBSingle': '>f4', 'IEEE754LSBSingle': '<f4',
          'SignedMSB4': '>i4', 'SignedLSB4': '<i4'}

def read_array(label, info=None):
    label = Path(label)
    info = info or metadata(label)
    if len(info['arrays']) != 1:
        raise ValueError('Conversion supports exactly one image array; originals are retained')
    spec = info['arrays'][0]
    if spec['dtype'] not in DTYPES:
        raise ValueError('Unsupported PDS data type: ' + spec['dtype'])
    axes = spec['axes']
    names = [a['name'] for a in axes]
    if set(names) - {'Line', 'Sample', 'Band'} or 'Line' not in names or 'Sample' not in names:
        raise ValueError('Unsupported image axes')
    shape = tuple(a['elements'] for a in axes)
    if spec['order'] not in ('Last Index Fastest', 'First Index Fastest'):
        raise ValueError('Unknown array index order')
    dtype = np.dtype(DTYPES[spec['dtype']])
    count = int(np.prod(shape))
    with (label.parent / spec['file']).open('rb') as f:
        f.seek(spec['offset'])
        raw = np.fromfile(f, dtype=dtype, count=count)
    if raw.size != count:
        raise ValueError('Image is truncated')
    raw = raw.reshape(shape, order='C' if spec['order'] == 'Last Index Fastest' else 'F')
    raw = raw.transpose([names.index(n) for n in ('Line', 'Sample', 'Band') if n in names])
    invalid = np.zeros(raw.shape, dtype=bool)
    for special in spec['special']:
        invalid |= raw == special
    image = raw.astype(np.float32) * spec['scale'] + spec['value_offset']
    image[invalid] = np.nan
    return image, info

def demosaic(image, info):
    if image.ndim != 2 or info['bayer_state'].lower() != 'encoded':
        return image
    patterns = {'Bayer RGGB': 'RGGB', 'Bayer BGGR': 'BGGR', 'Bayer GRBG': 'GRBG', 'Bayer GBRG': 'GBRG'}
    pattern = patterns.get(info['bayer_type'])
    if pattern is None:
        raise ValueError('Unknown Bayer pattern; refusing to guess color order')
    h, w = image.shape
    yy, xx = np.indices((h, w))
    # PDS first_line/first_sample are one-based sensor coordinates.
    index = ((yy + info['first_line'] - 1) % 2) * 2 + (xx + info['first_sample'] - 1) % 2
    rgb = []
    def convolve(a):
        p = np.pad(a, 1, mode='reflect')
        out = np.zeros((h, w), dtype=np.float32)
        for dy in range(3):
            for dx in range(3):
                out += p[dy:dy+h, dx:dx+w] * (2 if dy == 1 else 1) * (2 if dx == 1 else 1)
        return out
    for channel in 'RGB':
        mask = np.isin(index, [i for i, color in enumerate(pattern) if color == channel]) & np.isfinite(image)
        weights = convolve(mask.astype(np.float32))
        data = convolve(np.where(mask, image, 0))
        interpolated = np.divide(data, weights, out=np.zeros_like(data), where=weights > 0)
        rgb.append(np.where(mask, image, interpolated))
    return np.stack(rgb, axis=-1)

def convert_product(label, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    image, info = read_array(label)
    image = demosaic(image, info)
    if image.ndim == 3 and image.shape[-1] not in (1, 3):
        raise ValueError('Only monochrome and RGB arrays can be exported for reconstruction')
    if image.ndim == 3 and image.shape[-1] == 1:
        image = image[..., 0]
    # Shared range for all RGB channels preserves their relative brightness.
    finite = image[np.isfinite(image)]
    if not finite.size:
        raise ValueError('Image contains no valid pixels')
    low, high = map(float, np.percentile(finite, (0.1, 99.9)))
    if high <= low:
        raise ValueError('Image has no usable contrast')
    normalized = np.nan_to_num((image - low) / (high - low), nan=0, posinf=1, neginf=0)
    reconstruction = np.round(np.clip(normalized, 0, 1) ** (1/2.2) * 65535).astype(np.uint16)
    name = Path(label).stem
    destination = output / (name + '.tif')
    scientific = output / 'scientific' / (name + '.float32.tif')
    scientific.parent.mkdir(exist_ok=True)
    photometric = 'rgb' if image.ndim == 3 else 'minisblack'
    tifffile.imwrite(scientific, image.astype(np.float32), photometric=photometric,
                     description='PDS physical values with bilinear Bayer demosaic when encoded; see conversion JSON')
    temporary = destination.with_suffix('.tif.tmp')
    tifffile.imwrite(temporary, reconstruction, photometric=photometric)
    temporary.replace(destination)
    save_json(output / (name + '.conversion.json'), {
        'label': str(Path(label).resolve()), 'scientific_tiff': str(scientific.resolve()),
        'reconstruction_tiff': str(destination.resolve()), 'range_low': low, 'range_high': high,
        'percentiles': [0.1, 99.9], 'gamma': 2.2, 'bayer': info['bayer_type'],
        'demosaic': 'bilinear' if info['bayer_state'].lower() == 'encoded' else 'none',
        'note': 'Per-image display stretch; scientific TIFF retains scaled values. No geometric resampling.'})
    return destination
