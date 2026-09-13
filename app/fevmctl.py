#!/usr/bin/env python3
"""fevmctl - command line interface for the FEVM FA880 WMI driver.

All functions talk to the sysfs attributes created by the fevm-wmi
kernel module under the method device directory.
"""

import argparse
import glob
import os
import sys

SYSFS_GLOB = "/sys/bus/wmi/devices/99D89064-8D50-42BB-BEA9-155B2E5D0FCD"

POWER_MODES = {"0": "balanced", "1": "performance", "2": "quiet", "3": "super"}
POWER_MODES_CN = {"balanced": "均衡", "performance": "狂暴", "quiet": "安静", "super": "超级"}


def sysfs_dir() -> str:
    for d in glob.glob(SYSFS_GLOB + "*"):
        if os.path.exists(os.path.join(d, "power_mode")):
            return d
    sys.exit("error: fevm-wmi driver not bound (no sysfs interface found).\n"
             "Load it with: sudo modprobe fevm-wmi")


def rd(name: str) -> str:
    with open(os.path.join(sysfs_dir(), name)) as f:
        return f.read().strip()


def wr(name: str, value: str):
    path = os.path.join(sysfs_dir(), name)
    try:
        with open(path, "w") as f:
            f.write(value)
    except PermissionError:
        sys.exit(f"error: permission denied writing {path} - run with sudo")


def cmd_status(_args):
    d = sysfs_dir()
    print(f"driver:   {d}")
    print(f"power:    {rd('power_mode')}")
    print(f"fan1:     {rd('fan1_rpm')} RPM")
    print(f"fan2:     {rd('fan2_rpm')} RPM")
    print(f"cpu_temp: {rd('cpu_temp')} °C (EC)")
    for k in ("mode_count", "fan_count", "gpu_present", "feature_mask"):
        print(f"{k}: {rd(k)}")


def cmd_power(args):
    if args.mode is None:
        print(rd("power_mode"))
        return
    wr("power_mode", args.mode)
    print("power_mode ->", rd("power_mode"))


def cmd_fan(args):
    if args.fan_mode == "manual":
        if args.duty is None:
            sys.exit("manual mode needs a duty percent, e.g. fevmctl fan manual 50")
        duty = int(args.duty)
        if not 0 <= duty <= 100:
            sys.exit("duty must be 0-100")
        wr("fan1_duty", str(duty))
        wr("fan2_duty", str(duty))
        print(f"fan duty -> {duty}% (manual)")
    else:
        wr("fan_mode", args.fan_mode)
        print(f"fan_mode -> {args.fan_mode}")


def main():
    p = argparse.ArgumentParser(
        prog="fevmctl",
        description="FEVM FA880 PRO control tool (Linux)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show all current readings").set_defaults(fn=cmd_status)

    sp = sub.add_parser("power", help="get/set power mode")
    sp.add_argument("mode", nargs="?",
                    choices=["quiet", "balanced", "performance", "super", "0", "1", "2", "3"])
    sp.set_defaults(fn=cmd_power)

    sp = sub.add_parser("fan", help="set fan mode")
    sp.add_argument("fan_mode", choices=["auto", "max", "manual"])
    sp.add_argument("duty", nargs="?", help="0-100 percent (manual only)")
    sp.set_defaults(fn=cmd_fan)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
