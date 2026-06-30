#!/usr/bin/env drgn
import sys
import drgn
from drgn.helpers.linux import *

def print_frame(frame):
    print(frame)
    print(frame.locals())
    for var in frame.locals():
        print(f"{var}: {frame[var]}")

def print_task_stack_trace(task):
   stack_trace = prog.stack_trace(task.pid)
   print(stack_trace)
   for frame in stack_trace:
       print_frame(frame)

for t in for_each_task(prog):
    if task_state_to_char(t) == "D":
        print_task_stack_trace(t)
