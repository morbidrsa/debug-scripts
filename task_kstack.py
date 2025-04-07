#!/usr/bin/env drgn
# Copyright (C) 2024 Johannes Thumshirn <jth@kernel.org>

import sys
import drgn
from drgn.helpers.linux import *

def print_frame(frame):
    print(frame)
    print(frame.locals())
    for var in frame.locals():
        print(f"{var}:")
        print(frame[var])

def print_stacktrace_for_pid(pid):
    stack_trace = prog.stack_trace(pid)
    print(stack_trace)
    for frame in stack_trace:
        print_frame(frame)

def main(args):
    if len(args) > 2:
        print(f"usage: {args[0]} pid")
        return 1

    if len(args) == 2:
        pid = int(args[1])
        print_stacktrace_for_pid(pid)
    else:
        for task in for_each_task():
            print_stacktrace_for_pid(task.pid)
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
