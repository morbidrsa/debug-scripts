Random scripts for debugging

[search_log.py](search_log.py):
script for searching a dmesg output for patterns. Currently it is
looking for btrfs RAID stripe tree lookup errors and is trying to
locate debug information on the extent.

[task_kstack.py](task_kstack.py):
drgn script to print the kernel stack for a given task in a crash dump or
optionally for all tasks if the pid is omitted.
