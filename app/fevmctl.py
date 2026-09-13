#!/usr/bin/env python3
"""fevmctl - command line interface for the FEVM FA880 WMI driver.

All functions talk to the sysfs attributes created by the fevm-wmi
kernel module under the method device directory.
"""

import argparse
import configparser
import glob
import os
import sys

SYSFS_GLOB = "/sys/bus/wmi/devices/99D89064-8D50-42BB-BEA9-155B2E5D0FCD"
CONF_PATH = os.path.expanduser("~/.config/fevmcc.ini")

POWER_MODES = {"0": "balanced", "1": "performance", "2": "quiet", "3": "super"}
POWER_MODES_CN = {"balanced": "均衡", "performance": "狂暴", "quiet": "安静", "super": "超级"}


def load_conf():
    c = configparser.ConfigParser()
    c.read(CONF_PATH)
    if "localdb" not in c:
        c["localdb"] = {}
    return c


def save_conf(c):
    os.makedirs(os.path.dirname(CONF_PATH), exist_ok=True)
    with open(CONF_PATH, "w") as f:
        c.write(f)


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


def hwmon_attr(driver: str, name: str):
    """Find a sysfs file inside the hwmon device of the given driver."""
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(os.path.join(h, "name")) as f:
                if f.read().strip() == driver:
                    p = os.path.join(h, name)
                    if os.path.exists(p):
                        with open(p) as f:
                            return f.read().strip()
        except OSError:
            pass
    return None


def cmd_status(_args):
    d = sysfs_dir()
    print(f"driver:   {d}")
    print(f"power:    {rd('power_mode')}")
    print(f"fan1:     {rd('fan1_rpm')} RPM")
    print(f"fan2:     {rd('fan2_rpm')} RPM")
    print(f"cpu_temp: {rd('cpu_temp')} °C (EC)")
    tctl = hwmon_attr("k10temp", "temp1_input")
    if tctl and tctl.isdigit():
        print(f"cpu_die:  {int(tctl) // 1000} °C (Tctl)")
    ppt = hwmon_attr("amdgpu", "power1_average")
    if ppt and ppt.isdigit():
        print(f"pkg_power:{int(ppt) / 1_000_000:.1f} W (PPT)")
    for k in ("mode_count", "fan_count", "gpu_present", "feature_mask"):
        print(f"{k}: {rd(k)}")


def cmd_power(args):
    if args.mode is None:
        print(rd("power_mode"))
        return
    wr("power_mode", args.mode)
    c = load_conf()
    c["localdb"]["PowerMode"] = args.mode
    save_conf(c)
    print("power_mode ->", rd("power_mode"))


def cmd_fan(args):
    c = load_conf()
    if args.fan_mode == "manual":
        if args.duty is None:
            sys.exit("manual mode needs a duty percent, e.g. fevmctl fan manual 50")
        duty = int(args.duty)
        if not 0 <= duty <= 100:
            sys.exit("duty must be 0-100")
        wr("fan1_duty", str(duty))
        wr("fan2_duty", str(duty))
        c["localdb"]["FanFlag"] = "2"
        c["localdb"]["customfanone"] = str(duty)
        c["localdb"]["customfantwo"] = str(duty)
        print(f"fan duty -> {duty}% (manual)")
    else:
        wr("fan_mode", args.fan_mode)
        c["localdb"]["FanFlag"] = "1" if args.fan_mode == "auto" else "3"
        print(f"fan_mode -> {args.fan_mode}")
    save_conf(c)


def cmd_apply(_args):
    """Re-apply saved settings (invoked by the login autostart entry)."""
    try:
        sysfs_dir()
    except SystemExit:
        return  # driver not loaded/bound yet; nothing to do
    c = load_conf()["localdb"]
    if c.get("PowerMode"):
        wr("power_mode", c["PowerMode"])
    flag = c.get("FanFlag")
    if flag == "3":
        wr("fan_mode", "max")
    elif flag == "2":
        for f, k in (("fan1_duty", "customfanone"), ("fan2_duty", "customfantwo")):
            if c.get(k):
                wr(f, c[k])
    elif flag == "1":
        wr("fan_mode", "auto")


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

    sub.add_parser("apply", help="re-apply saved settings (autostart)").set_defaults(fn=cmd_apply)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
