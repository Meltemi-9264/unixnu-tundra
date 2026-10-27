#!/usr/bin/env python3
"""Verify a UniXNU haydn boot.img byte for byte against its inputs.

Layout (bootimg.py): page-aligned header; kernel field = gzip(BootShim + UEFI FD)
with the DTB appended after the gzip stream; ramdisk = the payload FAT volume.
This checks the header, gunzips the kernel field, splits it back into
shim/FD/DTB by the known sizes, and verifies the arm64 image header inside the
shim describes shim+FD at the FD base from Resources/Configs/haydn.toml.
Exit 0 = every part round-trips.

Usage: verify-bootimg.py [BOOT_IMG] [UEFI_DIR] [HAYDN_DIR] [OUT_ROOT]
Defaults match the bring-up layout in this workspace.
"""
import hashlib, struct, sys, tomllib, zlib
from pathlib import Path

boot_path = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/haydn-out/haydn/boot/boot.img')
uefi = Path(sys.argv[2] if len(sys.argv) > 2 else '/root/k40p_ios/uefi-haydn')
haydn = Path(sys.argv[3] if len(sys.argv) > 3 else '/root/k40p_ios/haydn')
out = Path(sys.argv[4] if len(sys.argv) > 4 else '/tmp/haydn-out')

boot = boot_path.read_bytes()
config = tomllib.loads((uefi / 'Resources/Configs/haydn.toml').read_text())
fd = (uefi / 'Build/haydnPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd').read_bytes()
dtb = (haydn / 'dtb/haydn-merged.dtb').read_bytes()
payload = (out / 'haydn/payload/payload.img').read_bytes()

fails = 0
def check(name, ok, extra=''):
    global fails
    print(('PASS ' if ok else 'FAIL ') + name + (f'  {extra}' if extra else ''))
    fails += 0 if ok else 1

magic, = struct.unpack_from('<8s', boot, 0)
kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr, tags, page, ver = struct.unpack_from('<9I', boot, 8)
check('ANDROID! magic', magic == b'ANDROID!')
check('header v1, no second stage', ver == 1 and second_size == 0)
check('page size 4096', page == 4096)
check('load addresses', kernel_addr == 0x8000 and tags == 0x1e00000,
      f'kernel={kernel_addr:#x} ramdisk={ramdisk_addr:#x} tags={tags:#x}')

kpages = page * -(-kernel_size // page)
field = boot[page:page + kernel_size]
# the kernel field is gzip(compressed, mtime=0) + raw DTB: a single member,
# the tree in unused_data (same as the bootimg.py verifier)
unpacker = zlib.decompressobj(16 + zlib.MAX_WBITS)
compressed = unpacker.decompress(field)
extra = unpacker.unused_data
check('kernel field is one gzip member', bool(unpacker.eof), f'{len(compressed)} bytes')
shim = compressed[:len(compressed) - len(fd)]
tail_dtb = extra
middle_fd = compressed[len(shim):]
check('appended DTB matches the source tree', tail_dtb == dtb,
      f'{len(tail_dtb)} bytes sha {hashlib.sha256(tail_dtb).hexdigest()[:16]}')
check('UEFI firmware volume matches the build', middle_fd == fd,
      f'{len(fd)} bytes sha {hashlib.sha256(fd).hexdigest()[:16]}')
check('FD size matches the device config', len(fd) == config['uefi_fd']['size'],
      f'{len(fd):#x}')
check('shim carries the arm64 image header', len(shim) > 64 and shim[56:60] == b'ARM\x64')
text_offset, image_size = struct.unpack_from('<QQ', shim, 8)
check('image header describes shim+FD at the FD base',
      text_offset == config['uefi_fd']['base'] and image_size == len(shim) + len(fd),
      f'base={text_offset:#x} size={image_size:#x}')

rd = boot[page + kpages:page + kpages + ramdisk_size]
check('ramdisk is the payload FAT volume', rd == payload,
      f'{ramdisk_size} bytes sha {hashlib.sha256(rd).hexdigest()[:16]}')
check('no trailing data beyond the last padded page',
      len(boot) == page + kpages + page * -(-ramdisk_size // page), f'file {len(boot)}')

print('boot.img sha256:', hashlib.sha256(boot).hexdigest())
raise SystemExit(min(fails, 125))
