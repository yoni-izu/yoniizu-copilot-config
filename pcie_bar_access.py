#!/usr/bin/env python3
"""Read/write PCIe BAR registers via sysfs resource files."""

import argparse
import mmap
import os
import struct
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="Access PCIe BAR space through sysfs resource files."
    )
    parser.add_argument(
        "action", choices=["read", "write"], help="Transaction type: read or write"
    )
    parser.add_argument(
        "bdf",
        help="PCI Bus:Device.Function (e.g. 0000:03:00.0)",
    )
    parser.add_argument("bar", type=int, help="BAR number (0-5)")
    parser.add_argument("offset", help="Offset within the BAR (hex or decimal)")
    parser.add_argument(
        "-v", "--value", help="Value to write (hex or decimal, required for write)"
    )
    parser.add_argument(
        "-s",
        "--size",
        type=int,
        choices=[1, 2, 4, 8],
        default=4,
        help="Access width in bytes (default: 4)",
    )
    return parser.parse_args()


def auto_int(value):
    """Parse an integer from hex (0x...) or decimal string."""
    return int(value, 0)


PCI_COMMAND_REG = 0x04
PCI_COMMAND_MEMORY = 0x0002
PCI_COMMAND_BUS_MASTER = 0x0004


def enable_mem_and_busmaster(bdf):
    """Enable Memory Space and Bus Master bits in the PCI Command register."""
    config_path = f"/sys/bus/pci/devices/{bdf}/config"
    with open(config_path, "r+b") as f:
        f.seek(PCI_COMMAND_REG)
        cmd = struct.unpack("<H", f.read(2))[0]
        needed = PCI_COMMAND_MEMORY | PCI_COMMAND_BUS_MASTER
        if (cmd & needed) != needed:
            cmd |= needed
            f.seek(PCI_COMMAND_REG)
            f.write(struct.pack("<H", cmd))
            print(f"Enabled Memory Space + Bus Master on {bdf} (Command=0x{cmd:04x})")


def main():
    args = parse_args()

    if args.action == "write" and args.value is None:
        print("Error: --value is required for write operations.", file=sys.stderr)
        sys.exit(1)

    offset = auto_int(args.offset)
    bdf = args.bdf

    # Normalise BDF: accept both "03:00.0" (short) and "0000:03:00.0" (full domain)
    if bdf.count(":") == 1:
        bdf = "0000:" + bdf

    enable_mem_and_busmaster(bdf)

    resource_path = f"/sys/bus/pci/devices/{bdf}/resource{args.bar}"

    if not os.path.exists(resource_path):
        print(f"Error: {resource_path} does not exist.", file=sys.stderr)
        sys.exit(1)

    resource_size = os.path.getsize(resource_path)
    if resource_size == 0:
        print(f"Error: {resource_path} has zero size (BAR not enabled?).", file=sys.stderr)
        sys.exit(1)

    if offset + args.size > resource_size:
        print(
            f"Error: offset 0x{offset:x} + size {args.size} exceeds BAR size 0x{resource_size:x}.",
            file=sys.stderr,
        )
        sys.exit(1)

    fmt = {1: "B", 2: "<H", 4: "<I", 8: "<Q"}[args.size]

    fd = os.open(resource_path, os.O_RDWR | os.O_SYNC)
    try:
        mm = mmap.mmap(fd, resource_size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        try:
            if args.action == "read":
                data = mm[offset : offset + args.size]
                value = struct.unpack(fmt, data)[0]
                print(f"0x{value:0{args.size * 2}x}")
            else:
                value = auto_int(args.value)
                mm[offset : offset + args.size] = struct.pack(fmt, value)
                print(f"Wrote 0x{value:0{args.size * 2}x} to {bdf} BAR{args.bar}+0x{offset:x}")
        finally:
            mm.close()
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
