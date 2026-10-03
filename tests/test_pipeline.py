import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import tifffile
import mars_pds as pds
from mars_convert import read_array, demosaic, convert_product

SAMPLE = Path(__file__).resolve().parents[1] / 'sample.xml'
NAME = 'ZR0_0693_0728464455_818RAD_N0321214ZCAM08689_1100LMA01.xml'

def entry(name=NAME):
    return {'uri': 'atlas:pds4:mars_2020:perseverance:/bundle/data/0693/' + name,
            'archive': {'name': name, 'size': 1, 'bundle_id': 'mars2020_mastcamz_sci_calibrated', 'md5': ''}}

class PipelineTests(unittest.TestCase):
    def test_actual_filename_and_quality_flags(self):
        p = pds.decode(entry())
        self.assertEqual((p.camera, p.sol, p.sequence, p.focal_mm, p.compression), ('ZR', 693, 'ZCAM08689', 110, 'LM'))
        self.assertTrue(p.full_resolution)
        self.assertFalse(pds.decode(entry(NAME.replace('RAD_N', 'RAD_T'))).full_resolution)
        self.assertFalse(pds.decode(entry(NAME.replace('_1100LM', '_1101LM'))).full_resolution)

    def test_real_label_metadata(self):
        m = pds.metadata(SAMPLE)
        self.assertEqual(m['camera_model'], 'CAHVOR')
        self.assertEqual(m['camera_frame'], 'ROVER_NAV_FRAME')
        self.assertEqual(m['arrays'][0]['offset'], 49440)
        self.assertEqual(m['arrays'][0]['axes'][1]['elements'], 1648)
        self.assertEqual(m['bayer_type'], 'Bayer RGGB')
        self.assertIn('Vector_Center', m['camera_vectors'])

    def test_catalogue_pairing_and_latest_version(self):
        old = entry()
        new = entry(NAME.replace('A01', 'A02'))
        image = entry(new['archive']['name'].replace('.xml', '.IMG'))
        image['archive']['size'] = 4000000
        thumb = entry(NAME.replace('RAD_N', 'RAD_T'))
        hits = [{'_source': e} for e in (old, new, image, thumb)]
        with patch.object(pds, 'json_request', return_value={'hits': {'hits': hits, 'total': {'value': 4}}}):
            products = pds.discover()
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].version, 2)
        self.assertEqual(products[0].image_size, 4000000)

    def test_bayer_channels_and_crop_phase(self):
        image = np.tile(np.array([[10, 20], [20, 30]], dtype=np.float32), (4, 4))
        info = {'bayer_state': 'Encoded', 'bayer_type': 'Bayer RGGB', 'first_line': 1, 'first_sample': 1}
        rgb = demosaic(image, info)
        np.testing.assert_allclose(rgb, np.broadcast_to([10, 20, 30], rgb.shape))
        info.update(first_line=2, first_sample=2)
        rgb = demosaic(image, info)
        np.testing.assert_allclose(rgb, np.broadcast_to([30, 20, 10], rgb.shape))

    def test_endian_offset_scaling_and_tiff(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            raw = np.arange(1, 17, dtype='>i2').reshape(4, 4)
            (path / 'test.IMG').write_bytes(b'header!!' + raw.tobytes())
            label = path / 'test.xml'
            label.write_text('''<Product_Observational><File_Area_Observational><File><file_name>test.IMG</file_name></File>
            <Array_2D_Image><offset>8</offset><axis_index_order>Last Index Fastest</axis_index_order>
            <Element_Array><data_type>SignedMSB2</data_type><scaling_factor>2</scaling_factor><value_offset>3</value_offset></Element_Array>
            <Axis_Array><axis_name>Line</axis_name><elements>4</elements><sequence_number>1</sequence_number></Axis_Array>
            <Axis_Array><axis_name>Sample</axis_name><elements>4</elements><sequence_number>2</sequence_number></Axis_Array>
            </Array_2D_Image></File_Area_Observational></Product_Observational>''')
            image, _ = read_array(label)
            np.testing.assert_array_equal(image, raw.astype(np.float32) * 2 + 3)
            result = convert_product(label, path / 'out')
            self.assertEqual(tifffile.imread(result).dtype, np.uint16)
            np.testing.assert_array_equal(tifffile.imread(path / 'out/scientific/test.float32.tif'), image)
            self.assertEqual((path / 'test.IMG').read_bytes(), b'header!!' + raw.tobytes())

    def test_demosaic_preserves_sensor_samples(self):
        image = np.arange(64, dtype=np.float32).reshape(8, 8)
        rgb = demosaic(image, {'bayer_state': 'Encoded', 'bayer_type': 'Bayer RGGB', 'first_line': 1, 'first_sample': 1})
        np.testing.assert_array_equal(rgb[::2, ::2, 0], image[::2, ::2])
        np.testing.assert_array_equal(rgb[::2, 1::2, 1], image[::2, 1::2])
        np.testing.assert_array_equal(rgb[1::2, ::2, 1], image[1::2, ::2])
        np.testing.assert_array_equal(rgb[1::2, 1::2, 2], image[1::2, 1::2])

    def test_integrity_and_atomic_download(self):
        import io
        class Response(io.BytesIO):
            headers = {'Content-Length': '4'}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'image.IMG'
            with patch.object(pds, 'request', return_value=Response(b'data')):
                pds.download('https://example.com/image', path, 4, hashlib.md5(b'data').hexdigest())
            self.assertEqual(path.read_bytes(), b'data')
            with patch.object(pds, 'request', return_value=Response(b'bad!')):
                with self.assertRaises(IOError):
                    pds.download('https://example.com/image', path, 4, '0' * 32)
            self.assertEqual(path.read_bytes(), b'data')

    def test_cancellation_keeps_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            p = pds.decode(entry())
            with patch.object(pds, 'resolve', return_value='https://example.com/label'), patch.object(pds, 'download', side_effect=pds.Cancelled()):
                with self.assertRaises(pds.Cancelled):
                    pds.acquire([p], d)
            self.assertIn('cancelled', (Path(d) / 'realitycapture_manifest.csv').read_text())

if __name__ == '__main__':
    unittest.main()
