#!/usr/bin/env python3
"""Check the haydn UEFI memory descriptors against the merged stock device tree.

The tree is the one this port compiled from the stock HyperOS images
(../haydn/dtb/haydn-merged.dtb: base-00 Lahaina V2.1 + the dtbo-10 haydn overlay).
Every no-map reservation with a fixed reg must appear as a Reserv descriptor in
haydnPkg/Library/MemoryMapLib/MemoryMapLib.c; no descriptor may overlap another;
the FD base/size from Resources/Configs/haydn.toml and the UEFI stack base from
haydn.dsc must match the descriptors. This runs before the firmware build.

The stock uefiplat.cfg of the K40 Pro is not in this checkout (the HyperOS abl
blob is an ELF, not a config): runtime_regions_checked stays 0 until it is
extracted from abl.img and dropped at .cache/firmware/haydn/uefiplat.cfg.
"""
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path('.').resolve()
DTB = Path(sys.argv[1]) if len(sys.argv) > 1 else None
PKG = Path('Platforms/Xiaomi/haydnPkg')
CFG = Path('Resources/Configs/haydn.toml')


def locate_dtb():
    for candidate in (ROOT / 'build/haydn/haydn-merged.dtb',
                      ROOT / 'Platforms/Xiaomi/haydnPkg/FdtBlob_compat/haydn.dtb',
                      ROOT / '.cache/haydn/haydn-merged.dtb'):
        if candidate.is_file():
            return candidate
    raise SystemExit('no haydn DTB: run scripts/fetch-haydn-dtb.py or pass the path')


def cells(dtb, node, prop):
    result = subprocess.run(['fdtget', '-t', 'x', str(dtb), node, prop],
                            capture_output=True, text=True)
    return [] if result.returncode else [int(word, 16) for word in result.stdout.split()]


def main():
    dtb = locate_dtb()
    reservations = []
    for node in subprocess.check_output(['fdtget', '-l', str(dtb), '/reserved-memory'],
                                        text=True).split():
        values = cells(dtb, '/reserved-memory/' + node, 'reg')
        if len(values) % 4:
            continue
        for index in range(0, len(values), 4):
            base = (values[index] << 32) | values[index + 1]
            size = (values[index + 2] << 32) | values[index + 3]
            if not size or base == 0:
                continue
            reservations.append((node, base, size))
    source = (PKG / 'Library/MemoryMapLib/MemoryMapLib.c').read_text()
    rows = [(m[1], int(m[2], 16), int(m[3], 16), m[4]) for m in re.finditer(
        r'\{"([^"]+)"\s*,\s*(0x[0-9a-fA-F]+),\s*(0x[0-9a-fA-F]+),([^}]+)\}', source)]
    errors = []
    if not rows or len(rows) > 128:
        errors.append('Empty map or descriptor capacity exceeded')
    reserv = sorted((a, a + s) for _, a, s, attrs in rows if re.search(r'\bReserv\b', attrs))
    for name, base, size in reservations:
        # the reservation must be covered by the union of Reserv descriptors
        covered, cursor = True, base
        for lo, hi in reserv:
            if lo <= cursor and hi > cursor:
                cursor = hi
            if cursor >= base + size:
                break
        if cursor < base + size:
            covered = False
        if not covered:
            errors.append(f'Missing reservation: {name}')
    for index, (name, base, size, attrs) in enumerate(rows):
        if base % 4096 or size % 4096 or not size:
            errors.append(f'Invalid UEFI page alignment: {name}')
        for other, other_base, other_size, _ in rows[index + 1:]:
            if max(base, other_base) < min(base + size, other_base + other_size):
                errors.append(f'Overlapping intervals: {name}, {other}')
    config = tomllib.loads(CFG.read_text())
    fd = config['uefi_fd']
    if not any(n == 'UEFI FD' and a == fd['base'] and s == fd['size'] for n, a, s, _ in rows):
        errors.append('FD configuration does not match memory descriptor')
    dsc = (PKG / 'haydn.dsc').read_text()
    stack = re.search(r'PcdCPUCoresStackBase\|(0x[0-9a-fA-F]+)', dsc)
    if not stack or not any(n == 'UEFI Stack' and a == int(stack[1], 16) for n, a, _, _ in rows):
        errors.append('Stack configuration does not match memory descriptor')
    heap = next((a, s) for n, a, s, _ in rows if n == 'DXE Heap')
    for name, base, size in reservations:
        # a fixed stock reservation must not fall inside the allocatable DXE Heap
        if base < heap[0] + heap[1] and base + size > heap[0]:
            errors.append(f'Stock reservation inside DXE Heap: {name}')
    dependencies = set()
    for inc in (PKG / 'Include').glob('*.inc'):
        dependencies.update(re.findall(r'Binaries/[a-z0-9]+/[^\s}]+', inc.read_text()))
    missing = sorted(p for p in dependencies if not (ROOT / p).is_file())
    firmware = Path('.cache/firmware/haydn/uefiplat.cfg')
    stock_count = 0
    if firmware.exists():
        import csv
        text = firmware.read_text().split('[MemoryMap]', 1)[1].split('[RegisterMap]', 1)[0]
        for line in text.splitlines():
            if not line.strip().startswith('0x'):
                continue
            record = next(csv.reader([line], skipinitialspace=True))
            base, size = int(record[0], 16), int(record[1], 16)
            stock_count += 1
            remaining = [(base, base + size)]
            for name, address, extent, attributes in rows:
                remaining = [piece for lo, hi in remaining
                             for piece in [(lo, min(hi, address)), (max(lo, address + extent), hi)]
                             if piece[0] < piece[1]]
                if max(base, address) < min(base + size, address + extent):
                    if record[3] == 'NoMap' and 'HobOnlyNoCacheSetting' not in attributes:
                        errors.append(f'Stock NoMap range is mapped: {name}')
                    if record[6] == 'Reserv' and not re.search(r'\bReserv\b', attributes):
                        errors.append(f'Stock reserved range is allocatable: {name}')
            if remaining:
                errors.append(f'Uncovered stock region: {record[2]}')
    result = {'dtb': str(dtb), 'descriptors': len(rows), 'source_reservations': len(reservations),
              'stock_regions_checked': stock_count,
              'static_errors': errors, 'missing_vendor_inputs': missing,
              'runtime_ram_verified': False, 'hardware_boot_demonstrated': False}
    (ROOT / 'build/haydn').mkdir(parents=True, exist_ok=True)
    (ROOT / 'build/haydn/check.json').write_text(json.dumps(result, indent=2) + '\n')
    print(f'{len(reservations)} DTS reservations checked against {len(rows)} descriptors')
    print(f'{len(errors)} static errors; {len(missing)} vendor inputs still missing; hardware unverified')
    for error in errors:
        print(error)
    raise SystemExit(bool(errors))


if __name__ == '__main__':
    main()
