#!/usr/bin/env python3
import re
import sys
from typing import Dict, List, Tuple

def format_bytes(bytes_val: int) -> str:
    """Format bytes in human-readable form."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"

def parse_space_info(log_lines: List[str]) -> List[Dict]:
    """Parse BTRFS space_info debug messages."""
    space_infos = []

    # Pattern for space_info type line
    type_pattern = re.compile(r'space_info (\w+) \(sub-group')

    # Pattern for space_info data line
    data_pattern = re.compile(
        r'space_info total=(\d+), used=(\d+), pinned=(\d+), '
        r'reserved=(\d+), may_use=(\d+), readonly=(\d+) zone_unusable=(\d+)'
    )

    current_type = None

    for line in log_lines:
        # Check for type line
        type_match = type_pattern.search(line)
        if type_match:
            current_type = type_match.group(1)
            continue

        # Check for data line
        if current_type:
            data_match = data_pattern.search(line)
            if data_match:
                info = {
                    'type': current_type,
                    'total': int(data_match.group(1)),
                    'used': int(data_match.group(2)),
                    'pinned': int(data_match.group(3)),
                    'reserved': int(data_match.group(4)),
                    'may_use': int(data_match.group(5)),
                    'readonly': int(data_match.group(6)),
                    'zone_unusable': int(data_match.group(7)),
                }
                space_infos.append(info)
                current_type = None  # Reset for next entry

    return space_infos

def print_analysis(space_info: Dict):
    """Print detailed analysis of space_info."""
    print(f"\n{'='*70}")
    print(f"Space Type: {space_info['type']}")
    print(f"{'='*70}")

    print(f"\nRaw Values:")
    print(f"  Total:          {format_bytes(space_info['total']):>15} ({space_info['total']} bytes)")
    print(f"  Used:           {format_bytes(space_info['used']):>15} ({space_info['used']} bytes)")
    print(f"  Pinned:         {format_bytes(space_info['pinned']):>15} ({space_info['pinned']} bytes)")
    print(f"  Reserved:       {format_bytes(space_info['reserved']):>15} ({space_info['reserved']} bytes)")
    print(f"  May Use:        {format_bytes(space_info['may_use']):>15} ({space_info['may_use']} bytes)")
    print(f"  Zone Unusable:  {format_bytes(space_info['zone_unusable']):>15} ({space_info['zone_unusable']} bytes)")

    actually_usable = space_info['total'] - space_info['zone_unusable']
    committed = space_info['used'] + space_info['pinned'] + space_info['reserved'] + space_info['may_use']
    consumed = space_info['used'] + space_info['zone_unusable']
    zone_unusable_pct = (space_info['zone_unusable'] / space_info['total'] * 100) if space_info['total'] > 0 else 0
    may_use_pct = (space_info['may_use'] / space_info['total'] * 100) if space_info['total'] > 0 else 0

    print(f"\nCalculated Metrics:")
    print(f"  Actually Usable: {format_bytes(actually_usable):>15} "
          f"(total - zone_unusable)")
    print(f"  Committed:       {format_bytes(committed):>15} "
          f"(used + pinned + reserved + may_use)")
    print(f"  Consumed:        {format_bytes(consumed):>15} "
          f"(used + zone_unusable)")

    print(f"\nPercentages:")
    print(f"  Zone Unusable:  {zone_unusable_pct:>6.2f}% of total")
    print(f"  May Use:        {may_use_pct:>6.2f}% of total")

def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r') as f:
            log_lines = f.readlines()
    else:
        print("Reading from stdin (paste BTRFS debug output, then Ctrl+D):")
        log_lines = sys.stdin.readlines()

    space_infos = parse_space_info(log_lines)

    if not space_infos:
        print("No space_info entries found in input")
        return 1

    print(f"\nFound {len(space_infos)} space_info entries")

    for space_info in space_infos:
        print_analysis(space_info)

if __name__ == '__main__':
    sys.exit(main())

