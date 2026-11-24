"""Estimate what Compress would do to the game folder: files affected, pixels and bytes before/after.

The estimate is planned with the same rules as compress_textures.py. Byte counts use the
bytes-per-pixel table for each format, so they are close to real output but not exact.
"""
from pathlib import Path

from compress_textures import MIP_FACTOR, check_paths, input_for, load_settings, plan


def format_bytes(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def pct_change(before, after):
    return (after - before) / before * 100 if before else 0.0


def scan(s=None, log=print):
    """Print the estimate. Settings come from settings.cfg if not given. Returns a dict or None."""
    s = s or load_settings()
    err = check_paths(s)
    if err:
        log(f"Error: {err}")
        return None
    src = Path(s.source_dir)
    if not src.exists():
        log(f"Error: Folder not found at {src}")
        return None

    log(f"Scanning: {src}")
    log(f"Threshold: {s.threshold_w}x{s.threshold_h} = {s.threshold_px:,} pixels")
    log(f"Downscaling: {'on' if s.downscale else 'off'}, loops {s.loops}, rounding {s.rounding}, "
        f"DXT {'on (BC3)' if s.dxt else 'off'}\n")

    total_before = total_after = 0.0   # whole folder, bytes
    px_before = px_after = 0           # processed textures only
    proc_before = proc_after = 0.0     # processed textures only, bytes (per the DXT setting)
    proc_plain = proc_dxt = 0.0        # processed textures only: downscale only vs downscale + BC3
    files = over = failed = 0

    for dds in src.rglob('*.dds'):
        files += 1
        p = plan(input_for(dds, s), s)

        if p.info is None:  # unreadable or skipped before the header: count the file size as-is
            try:
                size = dds.stat().st_size
            except OSError:
                size = 0
            total_before += size
            total_after += size
            if p.status == 'fail':
                failed += 1
            continue

        info = p.info
        mip = MIP_FACTOR if info.mips > 1 else 1.0
        before = info.w * info.h * info.bpp * mip
        total_before += before

        if p.status != 'process':
            total_after += before  # untouched
            continue

        new_px = p.new_w * p.new_h
        plain = new_px * info.bpp * MIP_FACTOR  # downscale only: Texconv keeps the source format
        dxt = new_px * 1.0 * MIP_FACTOR         # downscale + BC3 (1 byte per pixel)
        after = dxt if s.dxt else plain
        total_after += after

        over += 1
        px_before += info.w * info.h
        px_after += new_px
        proc_before += before
        proc_after += after
        proc_plain += plain
        proc_dxt += dxt
        log(f"Found: {dds.name} ({info.w}x{info.h} -> {p.new_w}x{p.new_h}) "
            f"{format_bytes(before)} -> {format_bytes(after)}")
    log("\n")
    log(f"DDS files found:              {files}")
    log(f"Over threshold (downscaled):  {over}")
    if failed:
        log(f"Unreadable headers:           {failed}")
    if over:
        log(f"  Pixels:  {px_before:,} -> {px_after:,}  ({pct_change(px_before, px_after):+.1f}%)")
        log(f"  Size, downscale only: {format_bytes(proc_before)} -> {format_bytes(proc_plain)}  "
            f"({pct_change(proc_before, proc_plain):+.1f}%)")
        log(f"  Size, + DXT (BC3):    {format_bytes(proc_before)} -> {format_bytes(proc_dxt)}  "
            f"({pct_change(proc_before, proc_dxt):+.1f}%)")
        dxt_change = pct_change(proc_plain, proc_dxt)
        log(f"  DXT on top of downscaling: {'saves' if dxt_change < 0 else 'adds'} "
            f"{abs(dxt_change):.1f}%")
    log(f"Whole folder (estimate): {format_bytes(total_before)} -> {format_bytes(total_after)}  "
        f"({pct_change(total_before, total_after):+.1f}%)")

    return {
        "files": files, "over": over, "failed": failed,
        "pixels_before": px_before, "pixels_after": px_after,
        "bytes_before": proc_before, "bytes_after": proc_after,
        "folder_before": total_before, "folder_after": total_after,
    }


if __name__ == "__main__":
    scan()
