# Mars Terrain Image Browser

Python desktop utility for finding and downloading full-resolution NASA PDS Mastcam-Z products for photogrammetry. Defaults: **Sol 693 / ZCAM08689 / both eyes / science-calibrated RAD**.

## Run on this computer

Double-click **Start Mars Browser.cmd**. A project-local Python installation and image libraries have been installed in `.python`; no PATH changes are needed.

1. Click **Search PDS**. Search can take several minutes when NASA's catalogue is busy.
2. Filter filenames, select rows, and use **Load preview** to inspect an official browse PNG.
3. Choose a download folder. **Select visible** selects the filtered results.
4. Click **Download selected**. Keep TIFF conversion checked for reconstruction images.

The GUI runs networking and conversion in a worker thread. Cancel stops between requests/chunks; a request already in progress can take up to its 45-second timeout. Completed products remain available. Retry the same selection to verify existing files and continue. Partial files are restarted, not byte-resumed.

Use **Load catalogue** with `downloads/sol693/catalogue.json` to browse the verified example search without querying NASA again.

The live search verified on October 3, 2026 found **356 full-resolution RAD products: 178 ZL and 178 ZR**, with approximately **1.43 GB** of original image data before TIFF exports. All carry the `LM` compression code. The saved catalogue includes original image URIs, sizes, and archive checksums. Only sample products were downloaded during validation; choose the full set in the GUI when ready. Checked ZL/ZR examples and a browse PNG are in `downloads/verified-samples/`.

## Verified collection endpoints

NASA's current [Atlas archive explorer](https://pds-imaging.jpl.nasa.gov/beta/archive-explorer?mission=mars_2020&bundle=mars2020_mastcamz_sci_calibrated) uses:

- Catalogue: `POST https://pds-imaging.jpl.nasa.gov/api/search/atlas/_search`
- Download resolver: `GET https://pds-imaging.jpl.nasa.gov/api/data/{atlas_uri}?output=url`
- Science collection: `mars2020_mastcamz_sci_calibrated`, data under `data/0693/rad/` and `data/0693/iof/`.
- Operations collections: `mars2020_mastcamz_ops_raw` and `mars2020_mastcamz_ops_calibrated`, with their catalogue paths preserved.

Resolve each catalogue URI instead of guessing an old archive URL. A verified science label resolves to:

`https://d1ejlg980osaur.cloudfront.net/m20/cumulative/mars2020_mastcamz_sci_calibrated/data/0693/rad/ZR0_0693_0728464455_818RAD_N0321214ZCAM08689_1100LMA01.xml`

Some legacy `planetarydata.jpl.nasa.gov/img/data/...` listings stop before Sol 693. The current resolver avoids that problem. Atlas's public application endpoints are not a versioned API contract; a future NASA interface change may require an update.

## Quality and reconstruction workflow

Science-calibrated **RAD** is the default. Its radiometric calibration corrects instrument effects; **IOF** is also available. The GUI exposes raw and operations-calibrated alternatives. The highest-resolution filter rejects thumbnails and products marked as downsampled in their filenames; it does not imply that every product covers the entire sensor. Compression codes remain visible so lossless and lossy acquisitions can be distinguished. `LM` is present in the tested sample; its XML identifies **MSSS Lossless** compression.

Full-resolution originals are `.IMG`, not the PNG previews. Labels specify byte offsets, axis ordering, signedness, byte order, scaling, missing values, and camera geometry. The tested image is **1648 × 1200**, Bayer RGGB encoded, 110 mm focal length, and CAHVOR geometry in `ROVER_NAV_FRAME`.

TIFF conversion:

- Reads the image array from the XML-defined offset and applies its physical-value scale and offset.
- Demosaics encoded Bayer data using bilinear interpolation, accounting for sensor crop origin and preserving measured channel samples.
- Writes a scientific float32 TIFF and a 16-bit reconstruction TIFF at the original pixel dimensions.
- Uses a shared RGB range from the image's 0.1/99.9 percentiles, then gamma 2.2, for the reconstruction TIFF. This display stretch clips extremes and is recorded in a conversion JSON. It does not add source detail or undo lossy compression.
- Performs no geometric undistortion, white balance, or tone matching between exposures. Original `.IMG` and XML files remain unchanged.

The built-in converter supports a single uncompressed 2D/3D label-described image array with supported integer/float types, RGB or monochrome bands. Unsupported products are retained and reported in the manifest; they are not silently interpreted as another format.

For RealityCapture, import the reconstruction TIFFs from `images/<bundle>/<ZL or ZR>`. Use one radiometric product family per reconstruction, avoiding duplicate RAD and IOF versions of the same exposure. `realitycapture_manifest.csv` is an audit/preparation manifest containing paths, exposure, focal length, and original camera vectors. **It is not a directly importable RealityCapture flight log or a calibrated-pose file.** CAHVOR/CAHVORE and rover frame transforms need deliberate conversion before using them as pose or lens priors. No GPS coordinates, Euler angles, or COLMAP poses are fabricated. All label values and frame definitions are preserved in metadata JSON for that later work.

For LichtFeld Studio, first align these images in RealityCapture/RealityScan or COLMAP, then export a supported reconstruction with its undistorted images. Downloading images and metadata alone does not create a Gaussian-splat training dataset. A stereo pair/panorama from a single rover position may have limited baseline and scene coverage; inspect overlap and use additional rover viewpoints when available.

## Output

```text
downloads/
  realitycapture_manifest.csv
  reconstruction_images.txt
  <bundle>/ZL or ZR/
    <product>.xml
    <product>.IMG
    <product>.metadata.json
  images/<bundle>/ZL or ZR/
    <product>.tif
    <product>.conversion.json
    scientific/<product>.float32.tif
```

Files are written through temporary paths. Label and image sizes and catalogue MD5 checksums are verified when available; the manifest also records an independently calculated SHA-256 for downloaded images. A manifest row is saved after each product, including failures, and existing rows are retained when another selection is downloaded.

## Command line

```powershell
# Enumerate and save catalogue only
.\.python\python.exe mars_pds.py --sol 693 --sequence ZCAM08689 --output downloads\sol693

# Download and convert two products for a small initial check
.\.python\python.exe mars_pds.py --sol 693 --sequence ZCAM08689 --download --convert --limit 2

# Left camera only; omit --limit to acquire the entire selection
.\.python\python.exe mars_pds.py --camera ZL --download --convert

# Raw products: avoid the default RAD filter
.\.python\python.exe mars_pds.py --source ops_raw --kind All
```

CLI defaults to RAD and full resolution. The GUI changes its product filter to All when selecting an operations collection. Latest filename versions are retained. Catalogue pagination is capped at 10,000 archive entries; narrow broad searches to a sequence if that limit is reached.

## Setup elsewhere and tests

Install Python 3.11+ with Tkinter, then:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe mars_gui.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use a real NASA XML fixture and synthetic image arrays to verify metadata, filename quality flags, version selection, binary decoding, Bayer channel/crop handling, TIFF creation, integrity checks, and cancellation persistence.

## References

- [Mastcam-Z team: calibrated archive guide](https://mastcamz.asu.edu/mastcam-z-data-for-all/)
- [PDS Mastcam-Z Derived Product SIS](https://planetarydata.jpl.nasa.gov/img/data/mars2020/mars2020_mastcamz_sci_calibrated/document/mastcamz_derived_product_sis_v1.4.pdf)
- [RealityCapture flight-log import](https://rchelp.capturingreality.com/en-US/tools/flightlogimport.htm)
- [COLMAP tutorial](https://colmap.github.io/tutorial.html)
- [LichtFeld Studio: reconstruction interchange](https://portal.lichtfeld.io/newsletter/v054-is-close-kristof-joins-the-team-and-what-you-asked-for/)
