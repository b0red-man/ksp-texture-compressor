# KSP Texture Compressor

Texture compressor for Kerbal Space Program. It shrinks oversized `.dds` textures in your `GameData` folder to cut down on memory and disk use. Originals are backed up before anything is changed.

Windows only.

## Install

1. Go to **Releases** and download **Source code (zip)** from the latest release.
2. Extract the zip anywhere.
3. Run `main.exe`.

Keep `bin\Texconv.exe` next to `main.exe`; the app uses it to do the actual conversion. Windows may show a warning for an unsigned exe.

## How to use

1. Close KSP.
2. Click **Query** to see which textures would be changed and a size estimate.
3. Click **Compress** and wait for the `Final:` line in the log.

Compress replaces textures **in place** in your `GameData` folder. Before a texture is changed, the original is copied to the backup folder (default: a `texture_backup` folder next to `GameData`, in your Kerbal Space Program folder), using the same folder layout as `GameData`. Only textures that get compressed are backed up.

**To undo:** copy the contents of the backup folder over your KSP `GameData` folder and replace the files when asked.

If the game or a mod is updated, delete the matching files in the backup folder before compressing again. Compress always starts from an existing backup, so a stale backup would overwrite the updated texture.

## Settings

Settings are saved to `settings.cfg` next to `main.exe` each time you click Query or Compress.

| Setting | What it does |
|---|---|
| Source | The `GameData` folder to compress. Defaults to the standard Steam location. |
| Backup folder | Where originals are copied before they are changed. |
| Enable downscaling | Master switch. |
| Threshold (w x h) | A pixel area. Textures with more pixels than `w * h` are downscaled. |
| Downscale loops | How many times to halve a texture's width and height. `0` keeps halving until it is under the threshold. |
| Round to power of two | Off, Round Down, Round Up or Round Nearest, applied to the new size. |
| Skip files under (KB) | Files smaller than this are left alone. |
| Convert to DXT / BC3 | Writes downscaled textures as BC3. Only affects textures that are downscaled. |

Textures with `biome` in the file name are always skipped.

## Query estimates

Query estimates sizes from each texture's format (bytes per pixel) plus mipmaps. It also shows what converting to DXT would add on top of downscaling. Estimates are close but not exact.

## Build from source

```
pip install -r requirements.txt
python src/gui.py
```

To build `main.exe`:

```
python -m PyInstaller --onefile --windowed --name main --collect-all customtkinter --distpath . src/gui.py
```

## Credits

Conversion is done by `Texconv` from Microsoft's [DirectXTex](https://github.com/microsoft/DirectXTex) (MIT licensed).

## License

MIT. See [LICENSE](LICENSE).
