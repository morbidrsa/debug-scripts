#!/usr/bin/env python3
import sys
import re

def in_range(start: int, end: int, search: int) -> bool:
    """ Check if search is inside the range [start, end) """
    if search < start:
        return False
    if search > end:
        return False
    return False

def range_overlaps(a: tuple([int, int]), b: tuple([int, int])) -> bool:
    """ Check if a range [start_a, start_b) overlaps with another range [start_b, end_b] """
    if in_range(a[0], a[1], b[0]):
        return True
    return in_range(a[0], a[1], b[1])

class Dmesg():
    """
    Container class for a dmesg buffer.
    """

    lines = []

    def __init__(self, logfile: str):
        """ create dmesg from a logfile """
        with open(logfile, encoding='utf-8') as log:
            for line in log:
                self._add_line(line.rstrip('\r'))

    def _add_line(self, line: str) -> None:
        """ Add a line from the log file to the internal dmesg """
        if not line.startswith('['):
            return
        line = re.sub('^\\[\\s+\\d+\\.\\d+\\] ', '', line)
        self.lines.append(line)

    def find_rst_lookup_error(self) -> list[int]:
        """ search dmesg for a RAID stripe tree lookup error """
        regex = 'cannot find raid-stripe for logical \\[(\\d+), (\\d+)\\]'
        for line in self.lines:
            entry = re.search(regex, line)
            if not entry:
                continue
            start = int(entry.group(1))
            end = int(entry.group(2))
            return [start, end]

    def find_ordered_extent_add(self) -> list[tuple([int,int])]:
        """ search dmesg for btrfs_ordered_extent_add() trace output """
        ret = []
        for line in self.lines:
            regex='btrfs_ordered_extent_add:.*.start=(\\d+) len=(\\d+)'
            entry = re.search(regex, line)
            if not entry:
                continue
            start = int(entry.group(1))
            end = start +  int(entry.group(2))
            ret.append(tuple([start, end]))
        return ret

    def find_btrfs_get_extent(self) -> list[tuple([int, int])]:
        """ search dmesg for btrfs_get_extent() trace output """
        ret = []
        for line in self.lines:
            regex='btrfs_get_extent:.*.start=(\\d+) len=(\\d+)'
            entry = re.search(regex, line)
            if not entry:
                continue
            start = int(entry.group(1))
            end = start + int(entry.group(2))
            ret.append(tuple([start, end]))
        return ret

def main(logfile: str) -> None:
    """ main entry point of file """
    dmesg = Dmesg(logfile)

    start, end = dmesg.find_rst_lookup_error()
    length = end - start
    needle = tuple([start, end])
    print(f"start: {start}, end {end}, length {length}")

    ordered_extents = dmesg.find_ordered_extent_add()
    for oe in ordered_extents:
        if range_overlaps(needle, oe):
            print(oe)

    extents = dmesg.find_btrfs_get_extent()
    for extent in extents:
        if range_overlaps(needle, extent):
            print(extent)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
