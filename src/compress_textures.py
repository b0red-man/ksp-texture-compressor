import configparser
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# Project root: the folder with main.exe when packaged, otherwise the repo root (one level above src/).
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
TEXCONV_PATH = str(ROOT / "bin" / "Texconv.exe")
CONFIG_PATH = ROOT / "settings.cfg"

KSP_DIR = r"C:\Program Files (x86)\Steam\steamapps\common\Kerbal Space Program"  # default Steam install

DATA_KEYWORDS = ['nrm', 'normal', 'bump']  # these get -reconstructz
MIP_FACTOR = 4 / 3                         # full mip chain adds ~33%

# Global bytes-per-pixel table used for size estimates.
FOURCC_BPP = {
    b'DXT1': 0.5, b'ATI1': 0.5, b'BC4U': 0.5, b'BC4S': 0.5,
    b'DXT2': 1.0, b'DXT3': 1.0, b'DXT4': 1.0, b'DXT5': 1.0,
    b'ATI2': 1.0, b'BC5U': 1.0, b'BC5S': 1.0,
}
DXGI_BPP = {  # DX10 header formats
    71: 0.5, 72: 0.5,            # BC1
    74: 1.0, 75: 1.0,            # BC2
    77: 1.0, 78: 1.0,            # BC3
    80: 0.5, 81: 0.5,            # BC4
    83: 1.0, 84: 1.0,            # BC5
    95: 1.0, 96: 1.0,            # BC6H
    98: 1.0, 99: 1.0,            # BC7
}
DEFAULT_BPP = 4.0  # uncompressed RGBA8 if we can't tell


@dataclass
class Settings:
    downscale: bool = True
    threshold_w: int = 4096       # threshold is a pixel area: w * h
    threshold_h: int = 2048
    loops: int = 2                # 0 = keep halving until under the threshold
    rounding: str = "Off"         # "Off", "Round Down", "Round Up", "Round Nearest"
    dxt: bool = True              # convert to BC3 (DXT5)
    min_size_kb: int = 1          # skip files smaller than this on disk
    source_dir: str = KSP_DIR + r"\GameData"
    backup_dir: str = KSP_DIR + r"\texture_backup"  # originals, same layout as GameData

    @property
    def threshold_px(self):
        return self.threshold_w * self.threshold_h


# ---------- config file ----------
def save_settings(s, path=CONFIG_PATH):
    cp = configparser.ConfigParser(interpolation=None)  # no interpolation: paths may contain '%'
    cp["settings"] = {k: str(v) for k, v in asdict(s).items()}
    with open(path, "w", encoding="utf-8") as f:
        cp.write(f)


def load_settings(path=CONFIG_PATH):
    d = Settings()
    cp = configparser.ConfigParser(interpolation=None)
    try:
        if not cp.read(path, encoding="utf-8") or "settings" not in cp:
            return d
    except (configparser.Error, UnicodeDecodeError):
        return d  # unreadable or malformed file: fall back to defaults
    sec = cp["settings"]
    try:
        return Settings(
            downscale=sec.getboolean("downscale", d.downscale),
            threshold_w=sec.getint("threshold_w", d.threshold_w),
            threshold_h=sec.getint("threshold_h", d.threshold_h),
            loops=sec.getint("loops", d.loops),
            rounding=sec.get("rounding", d.rounding),
            dxt=sec.getboolean("dxt", d.dxt),
            min_size_kb=sec.getint("min_size_kb", d.min_size_kb),
            source_dir=sec.get("source_dir", d.source_dir),
            backup_dir=sec.get("backup_dir", d.backup_dir),
        )
    except ValueError:
        return d  # corrupt value: fall back to defaults


# ---------- DDS reading ----------
@dataclass
class DdsInfo:
    w: int
    h: int
    bpp: float
    mips: int


def read_dds_info(path):
    """Read size, format and mip count from the DDS header. None if unreadable."""
    try:
        with open(path, 'rb') as f:
            head = f.read(132)
    except OSError:
        return None
    if len(head) < 128 or head[:4] != b'DDS ':
        return None
    h, w = struct.unpack_from('<II', head, 12)
    mips = struct.unpack_from('<I', head, 28)[0] or 1
    pf_flags = struct.unpack_from('<I', head, 80)[0]
    fourcc = head[84:88]
    rgb_bits = struct.unpack_from('<I', head, 88)[0]

    if pf_flags & 0x4:  # DDPF_FOURCC: block-compressed
        if fourcc == b'DX10' and len(head) >= 132:
            bpp = DXGI_BPP.get(struct.unpack_from('<I', head, 128)[0], DEFAULT_BPP)
        else:
            bpp = FOURCC_BPP.get(fourcc, DEFAULT_BPP)
    elif rgb_bits:
        bpp = rgb_bits / 8
    else:
        bpp = DEFAULT_BPP
    return DdsInfo(w, h, bpp, mips)


# ---------- folder checks and backup mapping ----------
def check_paths(s):
    """Return an error message if the source/backup folders are unusable, else None."""
    if not s.source_dir.strip() or not s.backup_dir.strip():
        return "Source and backup folders must both be set."
    src = Path(s.source_dir).resolve()
    bak = Path(s.backup_dir).resolve()
    if bak == src or src in bak.parents or bak in src.parents:
        return "The backup folder and the source folder can't be inside each other."
    return None


def input_for(dds, s):
    """The file to work from: the backup if one exists (so re-runs start from the original), else the game file."""
    bak = Path(s.backup_dir) / dds.relative_to(Path(s.source_dir))
    return bak if bak.exists() else dds


# ---------- downscale planning (shared by compress and query) ----------
def target_size(w, h, s):
    """Downscaled (w, h), or None if the texture is already under the threshold."""
    limit = s.threshold_px
    if w * h <= limit:
        return None
    loops = 0
    while w * h > limit and (s.loops == 0 or loops < s.loops):
        w, h = max(1, w // 2), max(1, h // 2)
        loops += 1
    return w, h


def round_pow2(n, mode):
    """Snap n to a power of two according to the rounding mode."""
    if mode == "Off":
        return n
    lo = 1 << (n.bit_length() - 1)   # largest power of two <= n
    hi = lo if lo == n else lo << 1  # smallest power of two >= n
    if mode == "Round Down":
        return lo
    if mode == "Round Up":
        return hi
    return lo if (n - lo) <= (hi - n) else hi  # Round Nearest


@dataclass
class Plan:
    status: str                  # 'process', 'skip' or 'fail'
    reason: str = ""
    info: DdsInfo = None         # None if the header wasn't read
    new_w: int = 0
    new_h: int = 0


def plan(path, s):
    """Decide what would happen to one DDS file, without running Texconv."""
    name = path.name.lower()
    if 'biome' in name:
        return Plan('skip', "SKIPPED (BIOME)")
    try:
        size_kb = path.stat().st_size / 1024
    except OSError:
        return Plan('fail', "FAIL (CAN'T READ FILE)")
    if size_kb < s.min_size_kb:
        return Plan('skip', "SKIPPED (SMALL)")
    info = read_dds_info(path)
    if info is None:
        return Plan('fail', "FAIL (BAD HEADER)")
    if not s.downscale:
        return Plan('skip', "SKIPPED (DOWNSCALE OFF)", info, info.w, info.h)
    target = target_size(info.w, info.h, s)
    if target is None:
        return Plan('skip', "SKIPPED (OK SIZE)", info, info.w, info.h)
    tw = round_pow2(target[0], s.rounding)
    th = round_pow2(target[1], s.rounding)
    return Plan('process', "", info, tw, th)


# ---------- compression ----------
def run_texconv(inp, p, s, out_dir):
    """Write the downscaled texture for `inp` into out_dir, overwriting the file of the same name."""
    name = inp.name.lower()
    cmd = [TEXCONV_PATH, '-m', '0', '-w', str(p.new_w), '-h', str(p.new_h), '-y', '-o', str(out_dir)]
    if s.dxt:
        cmd += ['-f', 'BC3_UNORM']
    if any(key in name for key in DATA_KEYWORDS):
        cmd.append('-reconstructz')
    cmd.append(str(inp))

    try:
        # stdin=DEVNULL: a windowed exe has no valid stdin. CREATE_NO_WINDOW: no console flash per texture.
        proc = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        return False, f"FAIL ({e})"
    if proc.returncode == 0:
        return True, ""
    return False, f"FAIL: {(proc.stderr or proc.stdout).strip()}"


def compress_all(s=None, log=print):
    """Downscale DDS files in source_dir in place. Originals are copied to backup_dir first.

    Settings come from settings.cfg if not given.
    """
    s = s or load_settings()
    err = check_paths(s)
    if err:
        log(f"Error: {err}")
        return
    src = Path(s.source_dir)
    bak_root = Path(s.backup_dir)

    if not src.exists():
        log(f"Error: source folder not found: {src}")
        return
    if not Path(TEXCONV_PATH).exists():
        log(f"Error: Texconv not found at {TEXCONV_PATH}")
        return
    if not s.downscale:
        log("Downscaling is disabled, nothing to do.")
        return

    all_dds = list(src.rglob('*.dds'))
    total = len(all_dds)
    done = skipped = failed = 0
    log(f"Processing {total} DDS files (threshold: {s.threshold_w}x{s.threshold_h} px area)...")

    for i, dds in enumerate(all_dds, 1):
        prog = i / total * 100 if total else 100
        rel = dds.relative_to(src)
        bak = bak_root / rel
        inp = input_for(dds, s)
        p = plan(inp, s)

        if p.status == 'skip':
            skipped += 1
            continue
        if p.status == 'fail':
            failed += 1
            log(f"[{prog:4.1f}%] {p.reason}: {rel.name}")
            continue

        # Back up the original before touching the game file. Never overwrite an existing backup.
        if inp is dds:
            try:
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dds, bak)
            except OSError as e:
                failed += 1
                log(f"[{prog:4.1f}%] BACKUP FAILED, left untouched: {rel.name} ({e})")
                continue
            inp = bak

        ok, err = run_texconv(inp, p, s, dds.parent)
        if ok:
            done += 1
            log(f"[{prog:4.1f}%] Compressed {p.info.w}x{p.info.h} -> {p.new_w}x{p.new_h}: {rel.name}")
        else:
            failed += 1
            log(f"[{prog:4.1f}%] {err}: {rel.name}")

    log(f"Final: {done} Compressed / {skipped} Skipped / {failed} Failed")


if __name__ == "__main__":
    compress_all()
