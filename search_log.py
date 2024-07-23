#!/usr/bin/env python3
import sys
import re

def in_range(start: int, end: int, search: int) -> bool:
    """ Check if search is inside the range [start, end) """
    if search < start:
        return False
    if search > end:
        return False
    return True

def range_overlaps(a: tuple([int, int]), b: tuple([int, int])) -> bool:
    """
    Check if a range [start_a, start_b) overlaps with another range
    [start_b, end_b]
    """
    if in_range(a[0], a[1], b[0]):
        return True
    return in_range(a[0], a[1], b[1])


class Extent:
    """
    Represent an extent as (start, end) tuple
    """
    def __init__(self, start: int, end: int):
        self.start = start
        self.end = end

    def __repr__(self):
        return f'Extent({self.start}, {self.end})'

    def to_tuple(self):
        return tuple([self.start, self.end])

    def length(self):
        return self.end - self.start


class BtrfsKey:
    """
    Represent a btrfs key
    """

    def __init__(self, objectid: int, type: int, offset: int):
        self.objectid = objectid
        self.type = type
        self.offset = offset

    def __repr__(self):
        return f'({self.objectid}, {self.type}, {self.offset})'


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

    def search_regex(self, regex: str) -> list[re.Match]:
        """
        Perform a regex search on the dmesg buffer and return a list of matches.
        """
        matches = []
        for line in self.lines:
            entry = re.search(regex, line)
            if entry:
                matches.append(entry)
        return matches


class BtrfsDmesg(Dmesg):
    """
    Container class for a dmesg buffer specific to btrfs log entries.
    """

    def find_rst_lookup_error(self) -> Extent:
        """ search dmesg for a RAID stripe tree lookup error """
        regex = 'cannot find raid-stripe for logical \\[(\\d+), (\\d+)\\]'
        matches = self.search_regex(regex)
        for entry in matches:
            start = int(entry.group(1))
            end = int(entry.group(2))
            return Extent(start, end)

    def find_ordered_extent_start(self) -> list[tuple([int,int])]:
        """ search dmesg for btrfs_ordered_extent_start() trace output """
        ret = []
        regex='btrfs_ordered_extent_start:.*.start=(\\d+) len=(\\d+)'
        matches = self.search_regex(regex)
        for entry in matches:
            start = int(entry.group(1))
            end = start + int(entry.group(2))
            ret.append(tuple([start, end]))
        return ret

    def find_btrfs_get_extent(self) -> list[tuple([int, int])]:
        """ search dmesg for btrfs_get_extent() trace output """
        regex='btrfs_get_extent:.*.start=(\\d+) len=(\\d+)'
        ret = []
        matches = self.search_regex(regex)
        for entry in matches:
            start = int(entry.group(1))
            end = start + int(entry.group(2))
            ret.append(tuple([start, end]))
        return ret

    def find_btrfs_reserve_extent(self) -> tuple([int, Extent]):
        """ search dmesg for btrfs_reserve_extent() trace output """
        regex='btrfs_reserve_extent:.*.block_group=(\\d+).*.start=(\\d+) len=(\\d+)'
        ret = []
        matches = self.search_regex(regex)
        for entry in matches:
            bg = int(entry.group(1))
            start = int(entry.group(2))
            end = start + int(entry.group(3))
            extent = Extent(start, end)
            ret.append(tuple([bg, extent]))
        return ret

    def find_btrfs_key(self) -> list[BtrfsKey]:
        regex='key\\.objectid=(\\d+), key.type=(\\d+), key.offset=(\\d+)';
        ret = []
        matches = self.search_regex(regex)
        for entry in matches:
            key = BtrfsKey(int(entry.group(1)), int(entry.group(2)) , int(entry.group(3)))
            ret.append(key)
        return ret

    def find_btrfs_block_group_relocate(self) -> list[int]:
        regex = 'relocating block group (\\d+)'
        ret = []
        matches = self.search_regex(regex)
        for entry in matches:
            ret.append(int(entry.group(1)))
        return ret

def main(logfile: str) -> None:
    """ main entry point of file """
    dmesg = BtrfsDmesg(logfile)

    needle = dmesg.find_rst_lookup_error()
    print(f"RST lookup failure for {needle} (length {needle.length()})")

    extent_items = dmesg.find_btrfs_key()
    for ei in extent_items:
        if in_range(needle.start, needle.end, ei.objectid):
            print(f"{str(needle)} matches key={ei}")

    ordered_extents = dmesg.find_ordered_extent_start()
    for oe in ordered_extents:
        if range_overlaps(needle.to_tuple(), oe):
            print(oe)

    extents = dmesg.find_btrfs_get_extent()
    for extent in extents:
        if range_overlaps(needle.to_tuple(), extent):
            print(extent)

    block_groups = dmesg.find_btrfs_block_group_relocate()
    for bg in block_groups:
        if in_range(needle.start, needle.end, bg):
            print(f"relocated block-group: {bg} is in [{str(needle)}]")

    matches = dmesg.find_btrfs_reserve_extent()
    for m in matches:
        e = m[1]
        bg = m[0]
        if range_overlaps(needle.to_tuple(), e.to_tuple()):
            print(f"extent {e} (length {e.length()}) in block-group {bg}")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage {sys.argv[0]} logfile")
        sys.exit(1)

    sys.exit(main(sys.argv[1]))
