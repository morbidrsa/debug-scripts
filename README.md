Random scripts for debugging

[search_log.py](search_log.py):
script for searching a dmesg output for patterns. Currently it is
looking for btrfs RAID stripe tree lookup errors and is trying to
locate debug information on the extent.

[task_kstack.py](task_kstack.py):
drgn script to print the kernel stack for a given task in a crash dump or
optionally for all tasks if the pid is omitted.

[trace-ordered-extent-size.py](trace-ordered-extent-size.py):
Extract statistics out of a ftrace run for the 'btrfs_ordered_extent_add"
tracepoint

[analyze-space_info.py](analyze-space_info.py):
Extract statistics out of the provided dmesg dump about the space\_info debugs
of a BTRFS filesystem mounted with `-o enospc_debug`
