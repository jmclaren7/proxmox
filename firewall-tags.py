#!/usr/bin/env python3
# Tag VMs/containers when their firewall config isn't fully locked down.
# Checks per guest: firewall enabled = 1, input policy = DROP, every netN has firewall=1.
# If any check fails, ensures the tag is set. If all pass, removes the tag.
#
# Reads come from /etc/pve/ directly (pmxcfs replicates cluster-wide, sub-ms reads).
# Writes go through `pvesh set` in a thread pool so multiple updates run in parallel.
#
#   --verbose / -v   : print every read and decision
#   --dry-run / -n   : don't actually call pvesh set
#   --jobs N / -j N  : parallel pvesh set workers (default 8)
#   Cron example: * * * * * /path/to/firewall-tags.py --loop >/dev/null

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

LOOP_INTERVAL = 5    # seconds between iteration starts when --loop is set
LOOP_DURATION = 60   # total wall-clock seconds the --loop runs

TAG = "_firewall_"
PVE_NODES = "/etc/pve/nodes"
PVE_FIREWALL = "/etc/pve/firewall"
NET_KEY = re.compile(r"^net\d+$")
KV_LINE = re.compile(r"^([A-Za-z0-9_]+):\s*(.*)$")
SECTION = re.compile(r"^\[([^\]]+)\]\s*$")


def parse_conf(path):
    """Parse top section of a Proxmox guest .conf. Stops at first [section]."""
    cfg = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if SECTION.match(line):
                break  # snapshot section — ignore
            m = KV_LINE.match(line)
            if m:
                cfg[m.group(1)] = m.group(2)
    return cfg


def parse_fw_options(path):
    """Parse the [OPTIONS] section of a .fw file."""
    if not os.path.exists(path):
        return {}
    opts = {}
    in_options = False
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            sec = SECTION.match(line)
            if sec:
                in_options = sec.group(1).upper() == "OPTIONS"
                continue
            if not in_options:
                continue
            m = KV_LINE.match(line)
            if m:
                opts[m.group(1)] = m.group(2)
    return opts


def discover_guests():
    """Yield dicts {node, vmid, type, name, conf_path} for non-template guests."""
    if not os.path.isdir(PVE_NODES):
        return
    for node in sorted(os.listdir(PVE_NODES)):
        for vmtype, subdir, name_key in (
            ("qemu", "qemu-server", "name"),
            ("lxc", "lxc", "hostname"),
        ):
            d = os.path.join(PVE_NODES, node, subdir)
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".conf"):
                    continue
                vmid = fn[:-5]
                if not vmid.isdigit():
                    continue
                conf_path = os.path.join(d, fn)
                try:
                    cfg = parse_conf(conf_path)
                except OSError as e:
                    print(f"[{node}] {vmtype} {vmid}: cannot read {conf_path}: {e}",
                          file=sys.stderr)
                    continue
                if cfg.get("template", "0") == "1":
                    yield_template = True  # noqa: F841 — keep verbose hint readable
                    yield {"skip_reason": "template", "node": node,
                           "vmid": int(vmid), "type": vmtype,
                           "name": cfg.get(name_key, ""), "config": cfg}
                    continue
                yield {
                    "node": node, "vmid": int(vmid), "type": vmtype,
                    "name": cfg.get(name_key, ""),
                    "config": cfg,
                    "conf_path": conf_path,
                }


def split_tags(s):
    return [t for t in re.split(r"[;,]", s or "") if t]


def parse_net(value):
    return dict(p.split("=", 1) for p in value.split(",") if "=" in p)


def evaluate(guest, log):
    """Return (all_pass, current_tags) for a guest, logging each step."""
    cfg = guest["config"]
    vmid = guest["vmid"]
    fw_path = os.path.join(PVE_FIREWALL, f"{vmid}.fw")

    log(f"   read {fw_path}")
    fw = parse_fw_options(fw_path)
    log(f"   raw firewall options = {fw}")

    enable_raw = fw.get("enable", "0")
    enable_ok = enable_raw.strip().lower() in ("1", "true", "yes", "on")

    # Proxmox default policy_in is DROP and may be omitted when at default.
    policy_raw = fw.get("policy_in")
    policy_in = (policy_raw or "DROP").upper()
    policy_ok = policy_in == "DROP"

    log(f"   firewall.enable    = {enable_raw!r} -> {'ok' if enable_ok else 'FAIL'}")
    log(f"   firewall.policy_in = {policy_raw!r} (effective {policy_in!r}) -> "
        f"{'ok' if policy_ok else 'FAIL'}")

    nets_ok = True
    net_items = [(k, v) for k, v in cfg.items() if NET_KEY.match(k)]
    if not net_items:
        log(f"   no netN entries in config")
    for k, v in net_items:
        fw_flag = parse_net(v).get("firewall")
        ok = fw_flag == "1"
        log(f"   {k}: firewall={fw_flag!r} {'ok' if ok else 'FAIL'}  ({v})")
        if not ok:
            nets_ok = False

    all_pass = enable_ok and policy_ok and nets_ok
    log(f"   => all_pass = {all_pass}")
    return all_pass, split_tags(cfg.get("tags", ""))


def pvesh_set_tags(node, vmtype, vmid, tags_list):
    """Apply a tag change. tags_list=[] clears tags via --delete."""
    path = f"/nodes/{node}/{vmtype}/{vmid}/config"
    if tags_list:
        cmd = ["pvesh", "set", path, "--tags", ";".join(tags_list)]
    else:
        cmd = ["pvesh", "set", path, "--delete", "tags"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()


def run_once(args):
    def log(msg):
        if args.verbose:
            print(msg)

    log(f"==> scanning {PVE_NODES}")
    guests = list(discover_guests())
    log(f"    found {len(guests)} entr(ies)")

    pending = []  # (prefix, action, node, vmtype, vmid, new_tags_list)
    checked = 0

    for g in guests:
        prefix = f"[{g['node']}] {g['type']} {g['vmid']} {g['name']}".rstrip()
        if g.get("skip_reason") == "template":
            log(f"-- skipping template {prefix}")
            continue
        checked += 1
        log(f"\n== {prefix}")

        all_pass, tags = evaluate(g, log)
        has_tag = TAG in tags
        log(f"   current tags = {tags}  (has {TAG!r}: {has_tag})")

        if not all_pass and not has_tag:
            new_tags = tags + [TAG]
            print(f"{prefix}: adding {TAG!r} tag -> {';'.join(new_tags)!r}")
            pending.append(("add", prefix, g["node"], g["type"], g["vmid"], new_tags))
        elif all_pass and has_tag:
            new_tags = [t for t in tags if t != TAG]
            shown = ";".join(new_tags) if new_tags else "(cleared)"
            print(f"{prefix}: removing {TAG!r} tag -> {shown}")
            pending.append(("remove", prefix, g["node"], g["type"], g["vmid"], new_tags))
        else:
            log(f"   no change needed")

    if not pending:
        log(f"\n==> done. checked {checked} guest(s), 0 changes")
        return

    if args.dry_run:
        print(f"\n[dry-run] {len(pending)} change(s) would be applied")
        return

    workers = max(1, min(args.jobs, len(pending)))
    log(f"\n==> applying {len(pending)} change(s) with {workers} worker(s)")
    changed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(pvesh_set_tags, node, vmtype, vmid, new_tags):
                (action, prefix)
            for (action, prefix, node, vmtype, vmid, new_tags) in pending
        }
        for fut in as_completed(futures):
            action, prefix = futures[fut]
            ok, err = fut.result()
            if ok:
                changed += 1
                log(f"   {prefix}: {action} ok")
            else:
                print(f"   {prefix}: ERROR pvesh set failed: {err}",
                      file=sys.stderr)

    log(f"\n==> done. checked {checked} guest(s), changed {changed}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-n", "--dry-run", action="store_true")
    ap.add_argument("-j", "--jobs", type=int, default=8,
                    help="parallel pvesh set workers (default 8)")
    ap.add_argument("-l", "--loop", action="store_true",
                    help=f"run repeatedly every {LOOP_INTERVAL}s for "
                         f"{LOOP_DURATION}s total (accounts for run time)")
    args = ap.parse_args()

    if not args.loop:
        run_once(args)
        return

    start = time.monotonic()
    deadline = start + LOOP_DURATION
    iteration = 0
    while True:
        iteration_start = time.monotonic()
        if iteration_start >= deadline:
            break
        iteration += 1
        if args.verbose:
            print(f"\n#### iteration {iteration} "
                  f"(t+{iteration_start - start:.1f}s)")
        run_once(args)

        next_start = start + iteration * LOOP_INTERVAL
        now = time.monotonic()
        sleep_for = min(next_start, deadline) - now
        if sleep_for > 0:
            time.sleep(sleep_for)


if __name__ == "__main__":
    main()
