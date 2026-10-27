#!/usr/bin/env python3
"""Validate a generated haydn ADT offline (Linux-capable; the kernel build needs macOS).

Checks the Apple node encoding, the required trees, the CHRP NVRAM proxy checksums
that upstream IODTNVRAM verifies, and that every board address matches the facts
recorded in the port notes (haydn/FACTS.md, haydn/M1.md):

  GICD 0x17a00000 + 0x10000, GICR 0x17a60000 + 0x100000, stride 0x20000
  arm-io window 0x17000000 (child reg values are offsets inside it)
  eight CPUs, MPIDR 0x000..0x700, cpu0 running
  timebase 19.2 MHz

Usage: check-adt-haydn.py ADT [--dram-base 0x80000000 --dram-size 0x180000000]
Exit code is the number of failed checks (0 means the tree is loadable).
"""
import argparse
import struct
import zlib
from pathlib import Path


def parse(buf, off=0):
    """Returns (node, next_offset). node = {name, props[(name, flags, bytes)], children[]}."""
    nprops, nchildren = struct.unpack_from('<II', buf, off)
    off += 8
    props = []
    for _ in range(nprops):
        name = buf[off:off + 32].split(b'\0')[0].decode()
        size, = struct.unpack_from('<I', buf, off + 32)
        flags, length = size >> 31, size & 0x7fffffff
        props.append((name, flags, buf[off + 36:off + 36 + length]))
        off += 36 + ((length + 3) & ~3)
    children = []
    for _ in range(nchildren):
        child, off = parse(buf, off)
        children.append(child)
    return {'props': props, 'children': children}, off


def prop(node, name):
    for n, flags, value in node['props']:
        if n == name:
            return value
    return None


def child(node, name):
    want = name.encode() + b'\0'
    for c in node['children']:
        if prop(c, 'name') == want:
            return c
    return None


def strings(value):
    return [s.decode() for s in (value or b'').split(b'\0') if s]


GICD, GICD_SIZE = 0x17a00000, 0x10000
GICR, GICR_SIZE = 0x17a60000, 0x100000
GICR_STRIDE = 0x20000
ARMIO_BASE, ARMIO_SIZE = 0x17000000, 0x08000000
TIMEBASE = 19200000
MPIDRS = [0x000, 0x100, 0x200, 0x300, 0x400, 0x500, 0x600, 0x700]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('adt', type=Path)
parser.add_argument('--dram-base', type=lambda v: int(v, 0), default=0x80000000)
parser.add_argument('--dram-size', type=lambda v: int(v, 0), default=0x180000000)
args = parser.parse_args()

buf = args.adt.read_bytes()
root, consumed = parse(buf)
checks = []

def check(name, ok, detail=''):
    checks.append((name, bool(ok), detail))

check('encoding consumes the whole blob', consumed == len(buf), f'{consumed} != {len(buf)}')
check('root is device-tree', prop(root, 'name') == b'device-tree\0')
check('root compatible names the board',
      'qcom,sm8350' in strings(prop(root, 'compatible')) and 'xiaomi,haydn' in strings(prop(root, 'compatible')),
      str(strings(prop(root, 'compatible'))))
check('root address/size cells are 2/2',
      prop(root, '#address-cells') == struct.pack('<I', 2) and prop(root, '#size-cells') == struct.pack('<I', 2))

chosen = child(root, 'chosen')
check('/chosen present', chosen is not None)
if chosen:
    seed = prop(chosen, 'random-seed')
    check('random-seed is 64 bytes', seed is not None and len(seed) == 64, str(len(seed or b'')))
    check('dram-base matches the board', prop(chosen, 'dram-base') == struct.pack('<Q', args.dram_base))
    check('dram-size matches the request', prop(chosen, 'dram-size') == struct.pack('<Q', args.dram_size))
    check('crypto-hash-method is sha2-384', prop(chosen, 'crypto-hash-method') == b'sha2-384\0')
    check('osenvironment is main', prop(chosen, 'osenvironment') == b'main\0')
    image = prop(chosen, 'nvram-proxy-data')
    if image:
        def header_ok(at, name):
            body = bytearray(image[at:at + 16])
            stored = body[1]
            body[1] = 0
            total = sum(body)
            while total > 255:
                total = (total & 255) + (total >> 8)
            return (body[0] == 0x70 and stored == total
                    and struct.unpack_from('<H', image, at + 2)[0] >= 1
                    and image[at + 4:at + 4 + len(name)] == name)
        adler = struct.unpack_from('<I', image, 16)[0]
        check('nvram proxy headers', header_ok(0, b'nvram') and header_ok(32, b'common'),
              'bank header checksums')
        check('nvram proxy adler32', adler == zlib.adler32(image[20:]),
              f'{adler:#x} != {zlib.adler32(image[20:]):#x}')
        bank = prop(chosen, 'nvram-bank-size')
        check('nvram bank size is the image', bank == struct.pack('<I', len(image)))
    mmap = child(chosen, 'memory-map')
    check('/chosen/memory-map present', mmap is not None)
    check('/chosen/manifest-properties present', child(chosen, 'manifest-properties') is not None)
    for pname in ('RAMDisk', 'PackedRAMDisk', 'TrustCache'):
        value = prop(mmap, pname) if mmap else None
        if value is not None:
            check(f'memory-map {pname} placeholder is 16 zero bytes', len(value) == 16 and not any(value))

cpus = child(root, 'cpus')
check('/cpus present', cpus is not None)
if cpus:
    cores = [c for c in cpus['children'] if (prop(c, 'device_type') or b'') == b'cpu\0']
    check('eight CPUs', len(cores) == 8, f'{len(cores)}')
    regs = [struct.unpack('<I', prop(c, 'reg'))[0] for c in cores]
    check('CPU MPIDRs are the haydn set', regs == MPIDRS, str([hex(r) for r in regs]))
    check('cpu0 is running, others waiting',
          prop(cores[0], 'state') == b'running\0' and all(prop(c, 'state') == b'waiting\0' for c in cores[1:]))
    check('every cpu has the 19.2 MHz timebase',
          all(prop(c, 'timebase-frequency') == struct.pack('<I', TIMEBASE) for c in cores))

psci = child(root, 'psci')
check('/psci is arm,psci-1.0 via smc',
      psci is not None and prop(psci, 'compatible') == b'arm,psci-1.0\0' and prop(psci, 'method') == b'smc\0')

for node_name in ('defaults', 'options', 'product', 'buttons'):
    check(f'/{node_name} present', child(root, node_name) is not None)
check('/arm-io present', child(root, 'arm-io') is not None)
armio = child(root, 'arm-io')
if armio:
    ranges = prop(armio, 'ranges')
    check('arm-io ranges describe the APSS window',
          ranges is not None and struct.unpack('<QQQ', ranges) == (0, ARMIO_BASE, ARMIO_SIZE),
          str(struct.unpack('<QQQ', ranges)) if ranges and len(ranges) == 24 else 'bad length')
    intc = child(armio, 'intc')
    check('/arm-io/intc is arm,gic-v3',
          intc is not None and prop(intc, 'compatible') == b'arm,gic-v3\0')
    if intc:
        reg = prop(intc, 'reg')
        want = struct.pack('<4Q', GICD - ARMIO_BASE, GICD_SIZE, GICR - ARMIO_BASE, GICR_SIZE)
        check('intc reg matches the live DT GIC addresses', reg == want, str(struct.unpack('<4Q', reg)) if reg and len(reg) == 32 else 'bad')
        check('intc redistributor stride is 0x20000',
              prop(intc, 'redistributor-stride') == struct.pack('<Q', GICR_STRIDE))
    check('/arm-io has the ufshc class node', child(armio, 'ufshc') is not None)

failed = sum(1 for _, ok, _ in checks if not ok)
for name, ok, detail in checks:
    print(('PASS ' if ok else 'FAIL ') + name + (f'  [{detail}]' if detail and not ok else ''))
print(f'{len(checks) - failed}/{len(checks)} checks passed')
raise SystemExit(failed)
