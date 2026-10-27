#!/usr/bin/env python3
"""Rebuild the appended haydn DTB from the stock HyperOS images.

The port appends the stock merged tree (base DTB from vendor_boot + the board's
dtbo overlay). This is a thin wrapper around the proven extractor from the SWEET
bring-up (haydn/extract.py, used on the same ROM to build haydn-merged.dtb/dts):
run it over the unpacked ROM directory and copy the merged tree to the output.

Usage: fetch-haydn-dtb.py <dir-with-boot.vendor_boot-dtbo.img> [OUT]
OUT defaults to build/haydn/haydn-merged.dtb.
"""
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_ROM = '.cache/haydn/stock'


def find_extractor():
    for candidate in (Path('../haydn/extract.py'), Path(__file__).resolve().parents[2] / 'haydn/extract.py',
                      Path('haydn/extract.py')):
        if candidate.is_file():
            return candidate.resolve()
    raise SystemExit('haydn/extract.py not found: pass the SWEET workspace or copy the extractor in')


def main():
    rom = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROM)
    out = Path(sys.argv[2] if len(sys.argv) > 2 else 'build/haydn/haydn-merged.dtb')
    for part in ('boot.img', 'vendor_boot.img', 'dtbo.img'):
        if not (rom / part).is_file():
            raise SystemExit(f'{rom} is missing {part}')
    work = Path('.cache/haydn/extract')
    if work.exists():
        shutil.rmtree(work)
    extractor = find_extractor()
    subprocess.run([sys.executable, str(extractor), str(rom), str(work)], check=True)
    merged = work / 'dtb/haydn-merged.dtb'
    if not merged.is_file():
        raise SystemExit('the extractor produced no merged tree (overlay model must contain "Haydn")')
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(merged, out)
    # The merged root model stays the base string; the board facts are in the
    # overlay's own model ("Haydn based on ... SM8350") and qcom,board-id.
    candidates = sorted((work / 'dtb').glob('dtbo-*.dtb'))
    chosen = None
    for candidate in candidates:
        text = subprocess.run(['dtc', '-I', 'dtb', '-O', 'dts', '-o', '-', str(candidate)],
                              capture_output=True, text=True).stdout
        if 'Haydn based on' in text:
            chosen = candidate
            break
    if chosen is None:
        raise SystemExit('no dtbo overlay with the Haydn model in ' + str(work / 'dtb'))
    splash = subprocess.run(['fdtget', '-t', 's', str(out), '/reserved-memory/splash_region', 'label'],
                            capture_output=True, text=True).stdout.strip()
    if splash != 'cont_splash_region':
        raise SystemExit(f'{out}: splash label unexpected: {splash!r}')
    print(f'{out}: {out.stat().st_size} bytes, overlay model "Haydn", splash label {splash}')


if __name__ == '__main__':
    main()
