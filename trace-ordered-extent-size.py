#!/usr/bin/env python3
#
# Extract statistics out of a ftrace run for the 'btrfs_ordered_extent_add"
# tracepoint
#

import argparse
import re

class OrderedExtent:
    """ Generic container for ordered extents"""
    ino = 0
    start = 0
    dlen = 0

    def __init__(self, ino, start, dlen):
        self.ino = int(ino)
        self.start = int(start)
        self.dlen = int(dlen)

    def __str__(self):
        return f"(ino={self.ino} start={self.start} len={self.dlen})"


def get_stats(extents):
    size = 0
    avg_size = 0
    min_size = 0
    max_size = 0

    for oe in extents:
        if min_size == 0:
            min_size = oe.dlen
        if min_size > oe.dlen:
            min_size = oe.dlen
        if max_size == 0:
            max_size = oe.dlen
        if max_size < oe.dlen:
            max_size = oe.dlen
        size = size + oe.dlen

    avg_size = size / len(extents)

    return (min_size, max_size, avg_size)


def main():
    parser = argparse.ArgumentParser(description="Trace the btrfs ordered-extent size distribution")
    parser.add_argument("infile", metavar='file', help='Trace file to process')
    args = parser.parse_args()

    infile = open(args.infile, "r")

    ordered_re = re.compile(".*.btrfs_ordered_extent_add:.*.ino=(\d+).*.start=(\d+).*.disk_len=(\d+)")

    ino = 0
    start = 0
    dlen = 0
    extents = []

    for line in infile:
        m = ordered_re.match(line)
        if m:
            ino = m.group(1)
            start = m.group(2)
            dlen = m.group(3)

            oe = OrderedExtent(ino, start, dlen)

            extents.append(oe)

    min_size, max_size, avg_size = get_stats(extents)

    print(f"Number of ordered extents in trace: {len(extents)}")
    print(f"Average size: {avg_size}")
    print(f"Smallest ordered extent: {min_size}")
    print(f"Biggest ordered extent: {max_size}")

if __name__ == '__main__':
    main()

# vim: set tabstop=4 shiftwidth=4 softtabstop=4 expandtab
