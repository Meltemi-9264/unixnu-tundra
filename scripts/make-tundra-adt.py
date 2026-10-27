#!/usr/bin/env python3
"""Generate the tundra (Motorola moto s30 pro, SM8350) Apple-format DT for the first UniXNU boot.

Structure follows the proven sheng generator and the QEMU entry fixture: only nodes and
properties that XNU consumes, with values pinned to the tundra device tree and the live
measurements recorded in the porting notes:

  - GICv3 distributor 0x17a00000 (size 0x10000), redistributor 0x17a60000 (size 0x100000,
    stride 0x20000) -- /soc/interrupt-controller@17a00000 in the merged stock DT
    (haydn/dtb/haydn-merged.dtb), arm-io window base 0x17000000.
  - CPU timebase 19.2 MHz (CNTFRQ, M1 live measurement: 30 IRQs/300 ms at INTID 27).
  - Virtual timer INTID 27 = PPI 11, the standard number (tundra porting notes; unlike sweet's 19).
  - Physical timer CNTP_* is UNDEFed by the QHEE hypervisor (M1 坑 7); the kernel must use
    the virtual timer only -- same finding as sheng/spacewar.
  - CPUs 0..7, MPIDR Affinity 0x000..0x700 (1x Cortex-X1, 3x Cortex-A78, 4x Cortex-A55).
  - UFS host 0x1d84000 lies below the arm-io window, so the child node is for class
    matching only (the driver takes physical windows from board constants).
  - DRAM base 0x80000000; --dram-size is the actual observed extent (8 GB board: 0x180000000).

No Apple device identity is claimed beyond what --ipad-identity writes for the iPad7,1
userland; panel geometry is haydn's own (1080x2400, presented iPhone X class 1125x2436).
"""
from pathlib import Path
import argparse
import os
import struct
import zlib

ROOT = Path(__file__).resolve().parents[1]

DRAM_BASE = 0x80000000
ARMIO_BASE = 0x17000000          # APSS peripheral block containing the GIC
ARMIO_SIZE = 0x08000000
GIC_DIST = 0x17a00000
GIC_DIST_SIZE = 0x10000
GIC_REDIST = 0x17a60000
GIC_REDIST_SIZE = 0x100000
GIC_REDIST_STRIDE = 0x20000      # live DT redistributor-stride (8 PEs, M1 woke R0..R7)
TIMEBASE = 19200000


def node(name, properties=(), children=()):
    properties = [('name', name.encode() + b'\0'), *properties]
    result = struct.pack('<II', len(properties), len(children))
    for key, data in properties:
        result += struct.pack('<32sI', key.encode(), len(data))
        result += data + bytes(-len(data) % 4)
    return result + b''.join(children)


def empty_nvram_proxy():
    """A fresh volatile CHRP bank for upstream IODTNVRAM, with no controller."""
    def header(name, size):
        raw = bytearray(struct.pack('<BBH12s', 0x70, 0, size // 16, name))
        checksum = sum(raw)
        while checksum > 255:
            checksum = (checksum & 255) + (checksum >> 8)
        raw[1] = checksum
        return raw

    image = bytearray(8192)
    image[:16] = header(b'nvram', 32)
    image[32:48] = header(b'common', 16 + 2048)
    free_at = 32 + 16 + 2048
    image[free_at:free_at + 16] = header(b'free', len(image) - free_at)
    struct.pack_into('<I', image, 16, zlib.adler32(image[20:]))
    return [('nvram-bank-size', struct.pack('<I', len(image))),
            ('nvram-bank-count', struct.pack('<I', 1)),
            ('nvram-current-bank', struct.pack('<I', 0)),
            ('nvram-proxy-data', bytes(image))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'build/tundra/boot/kernel.macho.adt')
    parser.add_argument('--dram-size', type=lambda value: int(value, 0), required=True,
                        help='Actual observed DRAM size from 0x80000000, e.g. 0x180000000 (8 GB)')
    parser.add_argument('--owned-memory-size', type=lambda value: int(value, 0), default=None,
                        help='DarwinBoot contiguous window request, 256 MiB..8 GiB in 2 MiB steps')
    parser.add_argument('--root-match-volume-uuid', default=None,
                        help='/chosen/root-matching: select the root by the UUID of an IOMedia')
    parser.add_argument('--unique-chip-id', type=lambda value: int(value, 16), default=None,
                        help='/chosen/unique-chip-id (8 bytes), with --chip-id (4 bytes): seeds IOPlatformUUID')
    parser.add_argument('--chip-id', type=lambda value: int(value, 16), default=None)
    parser.add_argument('--ipad-identity', action='store_true',
                        help='/product and /buttons describe an iPad-class device without a Home button '
                             '(MobileGestalt reads these); values follow the unchanged iPad7,1 tree, '
                             "the panel geometry is haydn's")
    parser.add_argument('--cpu-regs', default='0,0x100,0x200,0x300,0x400,0x500,0x600,0x700',
                        help='Comma-separated CPU MPIDR affinity values; the first is the boot CPU')
    parser.add_argument('--psci-method', choices=['smc', 'hvc'], default='smc',
                        help='PSCI conduit (M1: SMCCC 1.1 / PSCI 1.1 via SMC on haydn)')
    parser.add_argument('--packed-data-ramdisk', action='store_true',
                        help='PackedRAMDisk placeholder for the sparse Data handoff')
    parser.add_argument('--data-ramdisk', action='store_true',
                        help='/chosen/memory-map RAMDisk placeholder for a scratch Data seed')
    parser.add_argument('--trust-cache', action='store_true',
                        help='Zeroed TrustCache sidecar placeholder')
    args = parser.parse_args()

    if (args.unique_chip_id is None) != (args.chip_id is None):
        parser.error('--unique-chip-id and --chip-id must be supplied together')
    if args.unique_chip_id is not None and not (0 < args.unique_chip_id < 1 << 64 and 0 < args.chip_id < 1 << 32):
        parser.error('--unique-chip-id must fit 8 bytes and --chip-id 4 bytes, both nonzero')
    if args.owned_memory_size is not None and (
            not 0x10000000 <= args.owned_memory_size <= 0x200000000
            or args.owned_memory_size % 0x200000):
        parser.error('Owned memory must be 256 MiB to 8 GiB, aligned to 2 MiB')
    try:
        cpu_regs = [int(value, 0) for value in args.cpu_regs.split(',')]
    except ValueError:
        parser.error('--cpu-regs must be integers')
    if (not cpu_regs or cpu_regs[0] != 0 or len(set(cpu_regs)) != len(cpu_regs) or len(cpu_regs) > 8
            or any(reg < 0 or reg > 0xffff or reg & 0xff for reg in cpu_regs)):
        parser.error('--cpu-regs: up to eight distinct Affinity1-numbered cores, boot CPU 0 first')

    cpus = [node('cpu%d' % index, [('state', b'running\0' if index == 0 else b'waiting\0'),
                                   ('reg', struct.pack('<I', reg)),
                                   ('device_type', b'cpu\0'), ('compatible', b'arm,cortex-a78\0'),
                                   ('timebase-frequency', struct.pack('<I', TIMEBASE))])
            for index, reg in enumerate(cpu_regs)]

    chosen_props = [
        ('random-seed', os.urandom(64)),
        *([('unique-chip-id', struct.pack('<Q', args.unique_chip_id)),
            ('chip-id', struct.pack('<I', args.chip_id))]
           if args.unique_chip_id is not None else []),
        ('osenvironment', b'main\0'),
        *([('certificate-production-status', struct.pack('<I', 0)),
            ('effective-production-status-ap', struct.pack('<I', 0)),
            ('certificate-security-mode', struct.pack('<I', 1)),
            ('effective-security-mode-sep', struct.pack('<I', 1))]
           if args.ipad_identity else []),
        *([('ephemeral-storage', struct.pack('<I', 1))]
           if (args.data_ramdisk or args.packed_data_ramdisk) else []),
        *([('darwinboot-memory-size', struct.pack('<Q', args.owned_memory_size))]
           if args.owned_memory_size else []),
        ('dram-base', struct.pack('<Q', DRAM_BASE)),
        ('dram-size', struct.pack('<Q', args.dram_size)),
        ('crypto-hash-method', b'sha2-384\0'),
        *([('root-matching', ('<dict><key>IOProviderClass</key><string>IOMedia</string>'
                              '<key>IOPropertyMatch</key><dict><key>UUID</key><string>' +
                              args.root_match_volume_uuid.upper() +
                              '</string></dict></dict>\0').encode())]
           if args.root_match_volume_uuid else []),
        ('amfi-only-platform-code', struct.pack('<I', 1)),
        *empty_nvram_proxy(),
    ]

    # /chosen/memory-map always exists (matches the proven sheng tree); it lists a
    # 16-byte zero placeholder for each loader input. DarwinBoot refuses a missing
    # placeholder for an input it was given, but an empty map is valid when the
    # first boot has no Data seed and no trust cache.
    placeholders = []
    if args.data_ramdisk or args.packed_data_ramdisk:
        placeholders.append(('PackedRAMDisk' if args.packed_data_ramdisk else 'RAMDisk', bytes(16)))
    if args.trust_cache:
        placeholders.append(('TrustCache', bytes(16)))
    memory_map_children = [node('memory-map', placeholders),
                           # Read by the original AppleImage4 at start-up; present and empty here.
                           node('manifest-properties')]

    buttons = (node('buttons', [('compatible', b'buttons\0'), ('device_type', b'buttons\0'),
                                ('button-names', b'hold\0voldown\0volup\0'),
                                ('home-button-type', struct.pack('<I', 2))])
               if args.ipad_identity else node('buttons', []))

    tree = node('device-tree', [
        ('model', b'iPad7,1\0' if args.ipad_identity else b'tundra-first-boot\0'),
        *([('target-type', b'J120\0')] if args.ipad_identity else []),
        # Root compatible strings of the compiled board DT (tundra based on SM8350).
        ('compatible', b'motorola,tundra\0qcom,sm8350\0'),
        ('#address-cells', struct.pack('<I', 2)),
        ('#size-cells', struct.pack('<I', 2)),
    ], [
        node('chosen', chosen_props, memory_map_children),
        node('cpus', children=cpus),
        *([node('psci', [('compatible', b'arm,psci-1.0\0'),
                         ('method', args.psci_method.encode() + b'\0')])] if len(cpus) > 1 else []),
        node('defaults', [('sleep-disabled', struct.pack('<I', 1))]),
        node('options', []),
        buttons,
        node('product', [('product-name',
                          b'iPad Pro (12.9-inch) (2nd generation)\0' if args.ipad_identity else b'Redmi K40 Pro\0'),
                         ('model', b'motorola,tundra\0'),
                         *([('product-description', b'iPad Pro (12.9-inch) (2nd generation)\0'),
                            ('artwork-device-idiom', b'pad\0'),
                            ('artwork-device-subtype', struct.pack('<I', 2388)),   # haydn panel: 1080x2400
                            ('artwork-scale-factor', struct.pack('<I', 3)),        # 1080x2400 / 3 = 360x800 pt
                            ('artwork-display-gamut', b'P3\0'),
                            ('compatible-device-fallback', b'iPad6,7\0'),
                            ('thin-bezel', struct.pack('<I', 1))] if args.ipad_identity else [])]),
        node('arm-io', [('device_type', b'arm-io\0'),
                        ('#address-cells', struct.pack('<I', 2)),
                        ('#size-cells', struct.pack('<I', 2)),
                        ('ranges', struct.pack('<QQQ', 0, ARMIO_BASE, ARMIO_SIZE))],
             [node('intc', [('compatible', b'arm,gic-v3\0'),
                            ('reg', struct.pack('<4Q',
                                                GIC_DIST - ARMIO_BASE, GIC_DIST_SIZE,
                                                GIC_REDIST - ARMIO_BASE, GIC_REDIST_SIZE)),
                            ('redistributor-stride', struct.pack('<Q', GIC_REDIST_STRIDE))]),
              # UFS (0x1d84000) lies below the arm-io window: the node exists only for class
              # matching (the device drivers take their physical windows from board constants).
              node('ufshc', [('compatible', b'qcom,sm8350-ufshc\0')])]),
    ])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(tree)
    print(f'tundra ADT: {len(tree)} bytes, dram {args.dram_size:#x}, timebase {TIMEBASE}')


if __name__ == '__main__':
    main()
