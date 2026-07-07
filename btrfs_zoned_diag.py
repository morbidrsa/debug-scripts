#!/usr/bin/env drgn
"""
btrfs (zoned) first-pass diagnosis.

Dumps the state most useful for triaging btrfs hangs/ENOSPC, with an emphasis
on zoned filesystems (active zones, block-group zone state, metadata writeback).

Usage:
    # against a core dump / vmcore (fast, random access):
    drgn -c vmcore -s vmlinux btrfs_zoned_diag.py

    # against a live VM via the QEMU QMP socket (slow, sequential reads):
    drgn --qemu /tmp/vngXXXX.sock --kernel-directory /path/to/linux \
         btrfs_zoned_diag.py

For reliable wide (64-bit / per-cpu) counters, dump a *quiesced* system: a
hung/stopped VM or a crash dump. A live, running VM produces a non-atomic
snapshot where such values can be torn (e.g. a bogus dirty_metadata_bytes or a
slightly negative NR_WRITEBACK); the folio-level NR_FILE_DIRTY and the block
group / space_info state remain the authoritative signals.

Environment variables:
    BTRFS_FS=<bdi>       Only look at the fs whose bdi dev_name matches (e.g.
                         "btrfs-5"). Default: all btrfs mounts.
    BTRFS_EB_SCAN=1      Also scan the whole btree buffer_tree for dirty/stale
                         extent buffers and correlate them with their block
                         group's meta_write_pointer. This is EXPENSIVE and can
                         take minutes; only do it on a local core dump.
    BTRFS_EB_SCAN_MAX=N  Cap the number of ebs scanned (default: no cap).
"""

import os
import sys
import drgn
from drgn.helpers.linux.list import list_for_each_entry
from drgn.helpers.linux.rbtree import rbtree_inorder_for_each_entry

# ---- optional / version-dependent helpers -------------------------------

try:
    from drgn.helpers.linux.sched import task_state_to_char
except Exception:
    task_state_to_char = None

try:
    from drgn.helpers.linux.percpu import per_cpu_ptr
    from drgn.helpers.linux.cpumask import for_each_online_cpu
except Exception:
    per_cpu_ptr = None
    for_each_online_cpu = None

try:
    from drgn.helpers.linux.xarray import xa_for_each
except Exception:
    xa_for_each = None


# ---- small utilities ----------------------------------------------------

def const(name, default=None):
    try:
        return prog.constant(name).value_()
    except Exception:
        return default

def bit(n):
    return 1 << n

def hb(n):
    """human bytes"""
    v = float(int(n))
    for unit in ("B", "K", "M", "G", "T", "P"):
        if abs(v) < 1024.0 or unit == "P":
            return f"{v:.1f}{unit}"
        v /= 1024.0

def comm_of(task):
    try:
        return task.comm.string_().decode(errors="replace")
    except Exception:
        return "?"

def state_of(task):
    if task_state_to_char is not None:
        try:
            return task_state_to_char(task)
        except Exception:
            pass
    return "?"

def pcpu_counter_sum(fbc):
    total = int(fbc.count)
    if per_cpu_ptr is not None and for_each_online_cpu is not None:
        try:
            counters = fbc.counters
            for cpu in for_each_online_cpu(prog):
                total += int(per_cpu_ptr(counters, cpu)[0])
        except Exception:
            pass
    return total


# ---- constants ----------------------------------------------------------

BG_DATA = 1
BG_SYSTEM = 2
BG_METADATA = 4

ZONE_ACTIVE_BIT = const("BLOCK_GROUP_FLAG_ZONE_IS_ACTIVE")
BG_FLAG_REMOVED = const("BLOCK_GROUP_FLAG_REMOVED")
BG_FLAG_NEW = const("BLOCK_GROUP_FLAG_NEW")
EB_DIRTY = const("EXTENT_BUFFER_DIRTY")
EB_WB = const("EXTENT_BUFFER_WRITEBACK")
EB_STALE = const("EXTENT_BUFFER_STALE")
EB_ZZ = const("EXTENT_BUFFER_ZONED_ZEROOUT")
FS_ACTIVE_ZONE_TRACKING = const("BTRFS_FS_ACTIVE_ZONE_TRACKING")
FS_NEED_ZONE_FINISH = const("BTRFS_FS_NEED_ZONE_FINISH")

WANT_FS = os.environ.get("BTRFS_FS")
EB_SCAN = os.environ.get("BTRFS_EB_SCAN") == "1"
EB_SCAN_MAX = int(os.environ.get("BTRFS_EB_SCAN_MAX", "0"))


def bg_flag_str(f):
    s = []
    if f & BG_DATA:
        s.append("DATA")
    if f & BG_SYSTEM:
        s.append("SYSTEM")
    if f & BG_METADATA:
        s.append("METADATA")
    return "|".join(s) or hex(f)

def is_active(bg):
    if ZONE_ACTIVE_BIT is None:
        return None
    return bool(int(bg.runtime_flags) & bit(ZONE_ACTIVE_BIT))


# ---- per-subsystem dumps ------------------------------------------------

def iter_btrfs_sbs():
    for sb in list_for_each_entry("struct super_block",
                                  prog["super_blocks"].address_of_(), "s_list"):
        try:
            if sb.s_type.name.string_().decode() != "btrfs":
                continue
        except Exception:
            continue
        try:
            bdi = sb.s_bdi.dev_name.string_().decode()
        except Exception:
            bdi = "?"
        if WANT_FS and bdi != WANT_FS:
            continue
        yield sb, bdi

def dump_space_info(fs, warns):
    print("  space_info:")
    for si in list_for_each_entry("struct btrfs_space_info",
                                  fs.space_info.address_of_(), "list"):
        flags = int(si.flags)
        total = int(si.total_bytes)
        used = int(si.bytes_used)
        pinned = int(si.bytes_pinned)
        reserved = int(si.bytes_reserved)
        may_use = int(si.bytes_may_use)
        ro = int(si.bytes_readonly)
        zu = int(getattr(si, "bytes_zone_unusable", 0))
        full = bool(si.full) if hasattr(si, "full") else "?"
        avail = total - used - pinned - reserved - may_use - ro - zu
        ntk = ptk = 0
        try:
            for _ in list_for_each_entry("struct reserve_ticket",
                                         si.tickets.address_of_(), "list"):
                ntk += 1
        except Exception:
            ntk = -1
        try:
            for _ in list_for_each_entry("struct reserve_ticket",
                                         si.priority_tickets.address_of_(), "list"):
                ptk += 1
        except Exception:
            ptk = -1
        print(f"    {bg_flag_str(flags):16s} total={hb(total)} used={hb(used)} "
              f"resv={hb(reserved)} may_use={hb(may_use)} pinned={hb(pinned)} "
              f"ro={hb(ro)} zone_unusable={hb(zu)} avail={hb(avail)} "
              f"full={full} tickets={ntk} prio={ptk}")
        if ntk > 0 or ptk > 0:
            warns.append(f"{bg_flag_str(flags)} space_info has {ntk}+{ptk} "
                         f"pending reservation tickets (flush stuck?)")

def dump_devices(fs, warns):
    print("  devices (zone info):")
    zoned = False
    try:
        devs = fs.fs_devices.devices.address_of_()
    except Exception:
        return zoned
    for dev in list_for_each_entry("struct btrfs_device", devs, "dev_list"):
        zi = dev.zone_info
        if not zi:
            continue
        zoned = True
        maxz = int(zi.max_active_zones)
        left = int(zi.active_zones_left.counter)
        resv = int(zi.reserved_active_zones)
        nr = int(zi.nr_zones)
        zsz = int(zi.zone_size)
        print(f"    devid={int(dev.devid)} zone_size={hb(zsz)} nr_zones={nr} "
              f"max_active={maxz} active_left={left} reserved_active={resv}")
        if maxz and left <= resv:
            warns.append(f"devid {int(dev.devid)}: no free active zones "
                         f"(active_left={left} <= reserved={resv})")
    return zoned

def dump_active_bgs(fs, warns):
    print("  active zone pointers:")
    for name in ("active_meta_bg", "active_system_bg"):
        try:
            bg = getattr(fs, name)
        except Exception:
            continue
        if not bg:
            print(f"    {name} = NULL")
            continue
        act = is_active(bg)
        print(f"    {name} = bg start={int(bg.start)} active_bit={act} "
              f"used={hb(bg.used)} meta_wp-start={int(bg.meta_write_pointer)-int(bg.start)}")
        if act is False:
            warns.append(f"{name} points at an INACTIVE block group "
                         f"(start={int(bg.start)}) -> stale pointer")
    try:
        treelog = int(fs.treelog_bg)
        if treelog:
            print(f"    treelog_bg = {treelog}")
    except Exception:
        pass

    print("  zone_active_bgs list:")
    n = 0
    try:
        for bg in list_for_each_entry("struct btrfs_block_group",
                                      fs.zone_active_bgs.address_of_(),
                                      "active_bg_list"):
            print(f"    start={int(bg.start)} {bg_flag_str(int(bg.flags))} "
                  f"active_bit={is_active(bg)} used={hb(bg.used)} "
                  f"meta_wp-start={int(bg.meta_write_pointer)-int(bg.start)} "
                  f"alloc_off={hb(bg.alloc_offset)}")
            n += 1
            if n > 64:
                print("    ... (truncated)")
                break
    except Exception as e:
        print("    <error>", e)
    return n

def dump_block_groups(fs, warns):
    """Summarize metadata/system/data block groups and their zone state."""
    try:
        root = fs.block_group_cache_tree.rb_root.address_of_()
    except Exception:
        print("  block_group_cache_tree: <unavailable>")
        return
    meta_total = meta_active = meta_finished = 0
    sys_total = sys_active = 0
    data_total = data_active = 0
    # Keep the detail rows grouped per block-group type so DATA bgs don't get
    # listed under the metadata heading.
    meta_rows, sys_rows, data_rows = [], [], []
    for bg in rbtree_inorder_for_each_entry("struct btrfs_block_group",
                                            root, "cache_node"):
        flags = int(bg.flags)
        if not (flags & (BG_METADATA | BG_SYSTEM | BG_DATA)):
            continue
        start = int(bg.start)
        length = int(bg.length)
        act = is_active(bg)
        mwp = int(bg.meta_write_pointer)
        cap = int(bg.zone_capacity) if int(bg.zone_capacity) else length
        # meta_write_pointer is only meaningful for metadata/system bgs.
        meta_like = bool(flags & (BG_METADATA | BG_SYSTEM))
        mwprel = (mwp - start) if meta_like else None
        finished = meta_like and mwprel >= cap
        row = (start, bg_flag_str(flags), act, int(bg.used), int(bg.ro),
               mwprel, cap, int(bg.zone_unusable), int(bg.alloc_offset),
               bool(finished))
        if flags & BG_METADATA:
            meta_total += 1
            meta_active += 1 if act else 0
            meta_finished += 1 if finished else 0
            meta_rows.append(row)
        elif flags & BG_SYSTEM:
            sys_total += 1
            sys_active += 1 if act else 0
            sys_rows.append(row)
        else:
            data_total += 1
            data_active += 1 if act else 0
            data_rows.append(row)
    print(f"  metadata bgs: {meta_total} (active={meta_active}, "
          f"finished/mwp@end={meta_finished})   system bgs: {sys_total} "
          f"(active={sys_active})   data bgs: {data_total} (active={data_active})")

    def print_rows(label, rows, cap=256):
        if not rows:
            return
        print(f"    {label}:")
        for i, (start, fl, act, used, ro, mwprel, c, zu, aoff, finished) \
                in enumerate(rows):
            if i >= cap:
                print(f"      ... (truncated, {len(rows) - cap} more)")
                break
            tag = "ACTIVE" if act else "inactive"
            note = "  <mwp@zone_end>" if finished else ""
            mwp_str = hb(mwprel) if mwprel is not None else "n/a"
            print(f"      {fl:8s} start={start} {tag} used={hb(used)} ro={ro} "
                  f"alloc_off={hb(aoff)} mwp-start={mwp_str} "
                  f"zone_unusable={hb(zu)}{note}")

    print_rows("metadata block groups", meta_rows)
    print_rows("system block groups", sys_rows)
    print_rows("data block groups", data_rows)
    if meta_total and meta_active == 0:
        warns.append("NO active metadata block group -> metadata writeback "
                     "cannot make progress")

def _bg_cache_addr_set(fs):
    """Set of block_group object addresses currently in the cache rbtree."""
    addrs = set()
    try:
        root = fs.block_group_cache_tree.rb_root.address_of_()
        for bg in rbtree_inorder_for_each_entry("struct btrfs_block_group",
                                                root, "cache_node"):
            addrs.add(int(bg))
    except Exception:
        pass
    return addrs

def dump_bg_lists(fs, warns):
    """Dump the reclaim_bgs and unused_bgs lists (both threaded on bg_list).

    Both are consumed by the cleaner (btrfs_reclaim_bgs_work /
    btrfs_delete_unused_bgs). A block group that is flagged REMOVED (already
    deleted, no longer in the block group cache tree) but still linked here is
    a leak / re-add-after-removal bug and starves processing of the live
    entries behind it.
    """
    intree = _bg_cache_addr_set(fs)
    for name in ("reclaim_bgs", "unused_bgs"):
        try:
            head = getattr(fs, name).address_of_()
        except Exception:
            print(f"  {name}: <unavailable>")
            continue
        print(f"  {name}:")
        n = removed = 0
        try:
            for bg in list_for_each_entry("struct btrfs_block_group",
                                          head, "bg_list"):
                n += 1
                flags = int(bg.flags)
                rf = int(bg.runtime_flags)
                is_removed = bool(BG_FLAG_REMOVED is not None and
                                  rf & bit(BG_FLAG_REMOVED))
                is_new = bool(BG_FLAG_NEW is not None and rf & bit(BG_FLAG_NEW))
                in_tree = int(bg) in intree
                tags = []
                if is_active(bg):
                    tags.append("active")
                if is_removed:
                    tags.append("REMOVED")
                    removed += 1
                if is_new:
                    tags.append("NEW")
                if not in_tree:
                    tags.append("not-in-tree")
                if n <= 128:
                    print(f"    start={int(bg.start)} {bg_flag_str(flags)} "
                          f"used={hb(bg.used)} ro={int(bg.ro)} "
                          f"reclaim_mark={int(bg.reclaim_mark)} "
                          f"zone_unusable={hb(bg.zone_unusable)}"
                          + (f" [{', '.join(tags)}]" if tags else ""))
                elif n == 129:
                    print("    ... (truncated)")
        except Exception as e:
            print("    <error>", e)
        if n == 0:
            print("    (empty)")
        if removed:
            warns.append(f"{name}: {removed} block group(s) flagged REMOVED "
                         f"(already deleted, not in cache tree) are still "
                         f"linked -> leaked / re-added dead bg")

def dump_transaction(fs, warns):
    try:
        rt = fs.running_transaction
    except Exception:
        return
    if not rt:
        print("  running_transaction = NULL (nothing to commit)")
    else:
        st = int(rt.state)
        nw = int(rt.num_writers.counter)
        print(f"  running_transaction: state={st} num_writers={nw} "
              f"use_count={int(rt.use_count.counter)}")
    for name in ("transaction_kthread", "cleaner_kthread"):
        try:
            tk = getattr(fs, name)
        except Exception:
            continue
        if not tk:
            continue
        t = drgn.cast("struct task_struct *", tk)
        print(f"  {name}: pid={int(t.pid)} state={state_of(t)}")

def dump_dirty_meta(fs, warns):
    try:
        dmb = pcpu_counter_sum(fs.dirty_metadata_bytes)
        print(f"  dirty_metadata_bytes ~= {hb(dmb)} "
              f"(count={hb(int(fs.dirty_metadata_bytes.count))}) "
              f"thresh={hb(const('BTRFS_DIRTY_METADATA_THRESH', 32*1024*1024))}")
    except Exception as e:
        print("  dirty_metadata_bytes: <error>", e)
    try:
        print(f"  delalloc_bytes ~= {hb(pcpu_counter_sum(fs.delalloc_bytes))}  "
              f"ordered_bytes ~= {hb(pcpu_counter_sum(fs.ordered_bytes))}")
    except Exception:
        pass
    try:
        print(f"  btree_inode nrpages = {int(fs.btree_inode.i_mapping.nrpages)}")
    except Exception:
        pass


# ---- global (non per-fs) state ------------------------------------------

def dump_global(warns):
    print("== global ==")
    try:
        vns = prog["vm_node_stat"]

        def gstat(name):
            idx = const(name)
            return int(vns[idx].counter) if idx is not None else None
        fd = gstat("NR_FILE_DIRTY")
        wb = gstat("NR_WRITEBACK")
        print(f"  NR_FILE_DIRTY={fd} pages ({hb((fd or 0)*4096)})  "
              f"NR_WRITEBACK={wb} pages ({hb((wb or 0)*4096)})")
        if fd and fd > 100000 and (wb or 0) < 16:
            warns.append(f"~{hb(fd*4096)} dirty pages but almost nothing under "
                         f"writeback -> writeback stalled / dirty pages leaked")
    except Exception as e:
        print("  vm_node_stat: <error>", e)

    print("  uninterruptible (D) tasks:")
    n = 0
    try:
        from drgn.helpers.linux.pid import for_each_task
        for t in for_each_task(prog):
            if state_of(t) != "D":
                continue
            n += 1
            frames = []
            try:
                for f in prog.stack_trace(t):
                    fn = f.name or "?"
                    frames.append(fn)
            except Exception:
                pass
            # print a compact stack: top ~6 non-schedule frames
            interesting = [f for f in frames
                           if f not in ("context_switch", "__schedule",
                                        "__schedule_loop", "schedule",
                                        "schedule_timeout", "io_schedule_timeout")]
            print(f"    pid={int(t.pid)} {comm_of(t):16s} "
                  f"{' <- '.join(interesting[:6])}")
    except Exception as e:
        print("    <error>", e)
    if n == 0:
        print("    (none)")


# ---- optional: full extent-buffer scan ----------------------------------

def scan_ebs(fs):
    if xa_for_each is None:
        print("  (xa_for_each unavailable in this drgn; skipping eb scan)")
        return
    print("  scanning fs->buffer_tree (this can be slow)...")
    # block group lookup table (metadata/system only is enough, but build all)
    bgs = []
    try:
        root = fs.block_group_cache_tree.rb_root.address_of_()
        for bg in rbtree_inorder_for_each_entry("struct btrfs_block_group",
                                                root, "cache_node"):
            bgs.append((int(bg.start), int(bg.start) + int(bg.length), bg))
        bgs.sort()
    except Exception:
        pass

    def find_bg(s):
        for a, b, bg in bgs:
            if a <= s < b:
                return bg
        return None

    tot = dirty = d_stale = d_zz = d_wb = 0
    lt = eq = gt = orphan = 0
    per_bg = {}
    for idx, ent in xa_for_each(fs.buffer_tree.address_of_()):
        eb = drgn.cast("struct extent_buffer *", ent)
        bf = int(eb.bflags)
        tot += 1
        if EB_SCAN_MAX and tot > EB_SCAN_MAX:
            break
        if not (bf & bit(EB_DIRTY)):
            continue
        dirty += 1
        if EB_STALE is not None and bf & bit(EB_STALE):
            d_stale += 1
        if EB_ZZ is not None and bf & bit(EB_ZZ):
            d_zz += 1
        if EB_WB is not None and bf & bit(EB_WB):
            d_wb += 1
        s = int(eb.start)
        bg = find_bg(s)
        if bg is None:
            orphan += 1
            per_bg["orphan"] = per_bg.get("orphan", 0) + 1
            continue
        per_bg[int(bg.start)] = per_bg.get(int(bg.start), 0) + 1
        mwp = int(bg.meta_write_pointer)
        if s < mwp:
            lt += 1
        elif s == mwp:
            eq += 1
        else:
            gt += 1
    print(f"    ebs total={tot} dirty={dirty} "
          f"(stale={d_stale} zeroout={d_zz} writeback={d_wb})")
    print(f"    dirty vs meta_write_pointer:  < wp: {lt}   == wp: {eq}   "
          f"> wp: {gt}   orphan(no bg): {orphan}")
    if dirty and eq == 0 and lt > 0:
        print("    WARNING: no dirty eb is at a bg write-pointer frontier; "
              "all are behind it -> btree_writepages can never advance "
              "(dirty metadata leak).")
    print("    dirty ebs per bg:")
    for k in sorted(per_bg, key=lambda x: str(x)):
        extra = ""
        if isinstance(k, int):
            bg = find_bg(k)
            if bg is not None:
                extra = (f" [{'active' if is_active(bg) else 'inactive'} "
                         f"used={hb(bg.used)} "
                         f"mwp-start={hb(int(bg.meta_write_pointer)-int(bg.start))}]")
        print(f"      {k}: {per_bg[k]}{extra}")


# ---- lock ownership / deadlock analysis ---------------------------------
#
# Two independent sources of "who holds what":
#   1. CONFIG_LOCKDEP: task->held_locks[] gives the exact list of locks each
#      task holds (best, but only if the kernel was built with lockdep).
#   2. Owner fields: struct mutex.owner and struct rw_semaphore.owner encode
#      the owning task_struct in their upper bits. These exist without lockdep
#      (mutex always, rwsem when spin-on-owner is configured) and let us map a
#      blocked task to the task currently holding the lock it waits on.
#
# From the blocked-task -> owner edges we build a wait-for graph and look for
# cycles (classic AB-BA lock inversion). We also apply a heuristic for the
# harder completion/bit-mediated case: a task that holds a mutex (blocking
# others) but is itself parked in wait_for_completion()/wait_on_bit(), where
# the thing it waits for is produced by a worker that is blocked on that very
# mutex. That indirect cycle is exactly the btrfs zoned hang class.

MUTEX_FLAGS_MASK = 0x7
RWSEM_OWNER_FLAGS_MASK = 0x7
RWSEM_READER_OWNED = 0x1

# frame function name -> (kind, local variable holding the lock object)
_BLOCK_FRAMES = (
    ("mutex", ("__mutex_lock", "__mutex_lock_common", "mutex_lock",
               "mutex_lock_nested", "__mutex_lock_slowpath"), "lock"),
    ("rwsem", ("rwsem_down_write_slowpath", "rwsem_down_read_slowpath",
               "down_write", "down_read", "down_write_nested",
               "down_read_nested", "__down_write", "__down_read"), "sem"),
    ("completion", ("wait_for_completion", "wait_for_completion_state",
                    "wait_for_completion_io", "wait_for_completion_timeout",
                    "wait_for_completion_killable",
                    "wait_for_completion_interruptible",
                    "wait_for_common", "__wait_for_common"), "x"),
    ("folio", ("folio_wait_bit_common", "__folio_lock", "folio_lock",
               "folio_wait_bit"), "folio"),
    ("bit", ("__wait_on_bit", "__wait_on_bit_lock", "out_of_line_wait_on_bit",
             "out_of_line_wait_on_bit_lock", "wait_on_bit", "wait_on_bit_io",
             "bit_wait", "bit_wait_io"), None),
)

_WORKER_FRAMES = ("process_one_work", "worker_thread", "btrfs_work_helper")


def _classify_frame(fname):
    for kind, names, var in _BLOCK_FRAMES:
        for n in names:
            if fname == n or fname.startswith(n + "."):
                return kind, var
    return None, None

def _atomic_long_ptr(al):
    try:
        return int(al.counter) & 0xFFFFFFFFFFFFFFFF
    except Exception:
        return 0

def mutex_owner_task(lock):
    """@lock is a 'struct mutex *'."""
    try:
        v = _atomic_long_ptr(lock.owner) & ~MUTEX_FLAGS_MASK
    except Exception:
        return None
    if not v:
        return None
    try:
        return drgn.Object(prog, "struct task_struct *", v)
    except Exception:
        return None

def rwsem_owner_task(sem):
    """@sem is a 'struct rw_semaphore *'. Only write owners are reliable."""
    try:
        raw = _atomic_long_ptr(sem.owner)
    except Exception:
        return None
    if not raw or (raw & RWSEM_READER_OWNED):
        return None
    v = raw & ~RWSEM_OWNER_FLAGS_MASK
    if not v:
        return None
    try:
        return drgn.Object(prog, "struct task_struct *", v)
    except Exception:
        return None

def blocked_on(task):
    """Return {kind, obj, addr, owner} describing what @task is blocked on."""
    try:
        trace = prog.stack_trace(task)
    except Exception:
        return None
    kind = var = None
    for frame in trace:
        k, v = _classify_frame(frame.name or "")
        if k:
            kind, var = k, v
            break
    if kind is None:
        return None
    obj = None
    if var:
        # Only read the lock variable from frames of the same blocking kind, so
        # we don't accidentally pick up an unrelated spinlock named "lock".
        for frame in trace:
            k, _ = _classify_frame(frame.name or "")
            if k != kind:
                continue
            try:
                cand = frame[var]
            except Exception:
                continue
            if cand:
                obj = cand
                break
    owner = addr = None
    if obj is not None:
        try:
            addr = int(obj) & 0xFFFFFFFFFFFFFFFF
        except Exception:
            addr = None
        if kind == "mutex":
            owner = mutex_owner_task(obj)
        elif kind == "rwsem":
            owner = rwsem_owner_task(obj)
    return {"kind": kind, "obj": obj, "addr": addr, "owner": owner}

def held_locks(task):
    """lockdep-based held locks. Return list[(name, dep_map_addr)] or None if
    lockdep is not available."""
    try:
        depth = int(task.lockdep_depth)
    except Exception:
        return None
    out = []
    if depth <= 0:
        return out
    try:
        hls = task.held_locks
    except Exception:
        return None
    for i in range(min(depth, 48)):
        try:
            inst = hls[i].instance
        except Exception:
            continue
        name = "?"
        try:
            name = inst.name.string_().decode(errors="replace")
        except Exception:
            pass
        try:
            addr = int(inst) & 0xFFFFFFFFFFFFFFFF
        except Exception:
            addr = 0
        out.append((name, addr))
    return out

def _is_worker(task):
    try:
        for frame in prog.stack_trace(task):
            if (frame.name or "") in _WORKER_FRAMES:
                return True
    except Exception:
        pass
    return False

def _find_cycles(edges):
    """edges: pid -> pid (functional graph, <=1 out-edge). Return list of
    cycles (each a list of pids)."""
    cycles = []
    seen = set()
    for start in edges:
        local = {}
        node = start
        path = []
        while node in edges and node not in local:
            local[node] = len(path)
            path.append(node)
            node = edges[node]
        if node in local:
            cyc = path[local[node]:]
            key = tuple(sorted(cyc))
            if key not in seen:
                seen.add(key)
                cycles.append(cyc)
    return cycles

def _task_label(pid, task):
    return f"{pid}({comm_of(task)})"

def _iter_all_tasks():
    """Yield every task_struct, trying a few methods so this works across drgn
    versions and against dumps with incomplete symbols."""
    # 1) canonical helper
    try:
        from drgn.helpers.linux.pid import for_each_task
        yielded = False
        for t in for_each_task(prog):
            yielded = True
            yield t
        if yielded:
            return
    except Exception:
        pass
    # 2) drgn Program.threads() (does not need PIDTYPE_PID)
    try:
        for th in prog.threads():
            yield th.object
        return
    except Exception:
        pass
    # 3) manual walk of the init_task task list
    try:
        init = prog["init_task"]
        for t in list_for_each_entry("struct task_struct",
                                     init.tasks.address_of_(), "tasks"):
            yield t
    except Exception:
        return

def dump_locks_and_deadlock(warns):
    print()
    print("== locks / deadlock analysis ==")
    try:
        dtasks = [t for t in _iter_all_tasks() if state_of(t) == "D"]
    except Exception as e:
        print("  <could not iterate tasks:", e, ">")
        return
    if not dtasks:
        print("  (no uninterruptible (D) tasks)")
        return

    # What each D task is blocked on, and (for mutex/rwsem) who owns it.
    binfo = {}
    for t in dtasks:
        b = blocked_on(t)
        if b:
            binfo[int(t.pid)] = (t, b)

    # --- held locks -------------------------------------------------------
    lockdep_seen = False
    lockdep_lines = []
    for t in dtasks:
        hl = held_locks(t)
        if hl is None:
            continue
        lockdep_seen = True
        if hl:
            names = ", ".join(n for n, _ in hl)
            lockdep_lines.append(f"    pid={int(t.pid)} {comm_of(t):16s} "
                                 f"holds: {names}")

    print("  held locks:")
    if lockdep_seen:
        if lockdep_lines:
            for line in lockdep_lines:
                print(line)
        else:
            print("    (no D task holds a tracked lock)")
    else:
        # No lockdep: infer holders from the owner fields of contended locks.
        print("    (CONFIG_LOCKDEP off; inferring from contended lock owners)")
        shown = set()
        for pid, (t, b) in binfo.items():
            owner = b.get("owner")
            if not owner:
                continue
            try:
                opid = int(owner.pid)
            except Exception:
                continue
            key = (opid, b.get("addr"))
            if key in shown:
                continue
            shown.add(key)
            addr = hex(b["addr"]) if b.get("addr") else "?"
            print(f"    pid={opid} {comm_of(owner):16s} holds {b['kind']} "
                  f"{addr} (contended by pid={pid})")
        if not shown:
            print("    (no ownable contended locks found)")

    # --- blocked-on -------------------------------------------------------
    print("  blocked-on:")
    edges = {}
    for pid, (t, b) in binfo.items():
        line = f"    pid={pid} {comm_of(t):16s} blocked on {b['kind']}"
        if b.get("addr"):
            line += f" @ {hex(b['addr'])}"
        owner = b.get("owner")
        if owner:
            try:
                opid = int(owner.pid)
                edges[pid] = opid
                line += f" held by pid={opid} {comm_of(owner)}"
            except Exception:
                pass
        print(line)

    # --- deadlock detection ----------------------------------------------
    found = False

    # (a) hard lock-ownership cycles (AB-BA on mutex/rwsem).
    for cyc in _find_cycles(edges):
        found = True
        chain = " -> ".join(_task_label(p, binfo[p][0]) for p in cyc)
        first = cyc[0]
        chain += f" -> {_task_label(first, binfo[first][0])}"
        msg = f"DEADLOCK (lock ownership cycle): {chain}"
        print("  [!]", msg)
        warns.append(msg)

    # (b) completion/bit-mediated indirect deadlock: a lock holder that is
    #     itself parked in a wait that only a blocked-on-it worker can satisfy.
    owners = set(edges.values())
    for opid in owners:
        if opid not in binfo:
            continue
        ot, ob = binfo[opid]
        if ob["kind"] not in ("completion", "bit", "folio"):
            continue
        waiters = [p for p, o in edges.items() if o == opid]
        workers = [p for p in waiters if _is_worker(binfo[p][0])]
        found = True
        msg = (f"DEADLOCK (likely): pid={_task_label(opid, ot)} holds a lock "
               f"blocking pid(s) {waiters}, but is itself parked in "
               f"{ob['kind']}")
        if ob["kind"] == "completion" and workers:
            msg += (f"; that completion is signalled by worker pid(s) {workers}, "
                    f"which are blocked on the lock pid={opid} holds "
                    f"-> completion-mediated cycle")
        elif ob["kind"] == "bit":
            msg += (" (waking that bit needs forward progress that the blocked "
                    "waiters cannot make)")
        print("  [!]", msg)
        warns.append(msg)

    if not found:
        print("  no lock cycle detected among D tasks (they may be waiting on "
              "I/O or an external event)")


# ---- main ---------------------------------------------------------------

def main():
    warns = []
    dump_global(warns)
    dump_locks_and_deadlock(warns)
    for sb, bdi in iter_btrfs_sbs():
        fs = drgn.cast("struct btrfs_fs_info *", sb.s_fs_info)
        try:
            flags = int(fs.flags)
        except Exception:
            flags = 0
        azt = bool(FS_ACTIVE_ZONE_TRACKING is not None and
                   flags & bit(FS_ACTIVE_ZONE_TRACKING))
        nzf = bool(FS_NEED_ZONE_FINISH is not None and
                   flags & bit(FS_NEED_ZONE_FINISH))
        print()
        print(f"==== btrfs {bdi}  fs_info={hex(int(fs))} ====")
        print(f"  fs flags={hex(flags)} active_zone_tracking={azt} "
              f"need_zone_finish={nzf}")
        try:
            print(f"  generation={int(fs.generation)} "
                  f"last_trans_committed={int(fs.last_trans_committed)}")
        except Exception:
            pass
        dump_space_info(fs, warns)
        zoned = dump_devices(fs, warns)
        if zoned:
            dump_active_bgs(fs, warns)
        dump_block_groups(fs, warns)
        dump_bg_lists(fs, warns)
        dump_transaction(fs, warns)
        dump_dirty_meta(fs, warns)
        if EB_SCAN:
            scan_ebs(fs)

    print()
    print("==== heuristics / warnings ====")
    if warns:
        for w in warns:
            print("  [!]", w)
    else:
        print("  (none triggered)")


main()
