#!/usr/bin/env python3
import sys
import re

def in_range(start: int, end: int, search: int) -> bool:
    if search < start:
        return False
    if search > end:
        return False

class Dmesg():
    lines = []

    def __init__(self):
        pass

    def add_line(self, line: str) -> None:
        if not line.startswith('['):
            return
        line = re.sub('^\\[\\s+\\d+\\.\\d+\\] ', '', line)
        self.lines.append(line)

    def find_rst_lookup_error(self) -> list[int]:
        regex = 'BTRFS error \\(device.*.\\): cannot find raid-stripe for logical \\[(\\d+), (\\d+)\\] devid \\d, profile'
        for line in self.lines:
            entry = re.search(regex, line)
            if not entry:
                continue
            start = int(entry.group(1))
            end = int(entry.group(2))
            return [start, end]

    def find_ordered_extent_add(self) -> list[list[int]]:
        ret = []
        for line in self.lines:
            regex='btrfs_ordered_extent_add:.*.start=(\\d+) len=(\\d+)'
            entry = re.search(regex, line)
            if not entry:
                continue
            start = int(entry.group(1))
            end = start +  int(entry.group(2))
            ret.append([start, end])
        return ret


def main(logfile: str) -> None:

    dmesg = Dmesg()

    with open(logfile) as log:
        for line in log:
            dmesg.add_line(line.rstrip('\r'))

    start, end = dmesg.find_rst_lookup_error()
    length = end - start
    print(f"start: {start}, end {end}, length {length}")

    ordered_extents = dmesg.find_ordered_extent_add()
    for oe in ordered_extents:
        if in_range(start, end, oe[0]):
            print(oe)

    return

if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
