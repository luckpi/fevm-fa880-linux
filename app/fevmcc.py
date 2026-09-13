#!/usr/bin/env python3
"""FEVM Control Center - Linux GUI for the FA880 PRO WMI interface.

Requires the fevm-wmi kernel module (sysfs interface) and
python3-gi with GTK4 + libadwaita.
"""

import configparser
import glob
import os
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

WMI_GLOB = "/sys/bus/wmi/devices/99D89064-8D50-42BB-BEA9-155B2E5D0FCD*"
HWMON_GLOB = "/sys/class/hwmon/hwmon*"

MODES = [("quiet", "安静模式", "54W"),
         ("balanced", "均衡模式", "65W"),
         ("performance", "狂暴模式", "70W")]


def find_sysfs() -> str | None:
    for d in glob.glob(WMI_GLOB):
        if os.path.exists(os.path.join(d, "power_mode")):
            return d
    return None


def find_hwmon_fan(name: str) -> str | None:
    """Return fan input path from the fa880_ec_hwmon or fevm_wmi hwmon."""
    for n in ("fa880_ec_hwmon", "fevm_wmi"):
        p = find_hwmon_attr(n, name)
        if p:
            return p
    return None


def find_hwmon_attr(driver: str, name: str) -> str | None:
    for h in glob.glob(HWMON_GLOB):
        try:
            with open(os.path.join(h, "name")) as f:
                if f.read().strip() == driver:
                    p = os.path.join(h, name)
                    if os.path.exists(p):
                        return p
        except OSError:
            pass
    return None


class Sysfs:
    def __init__(self):
        self.dir = find_sysfs()

    @property
    def ok(self):
        return self.dir is not None

    def read(self, attr):
        if not self.ok:
            return None
        try:
            with open(os.path.join(self.dir, attr)) as f:
                return f.read().strip()
        except OSError:
            return None

    def write(self, attr, value):
        if not self.ok:
            raise OSError("driver not present")
        with open(os.path.join(self.dir, attr), "w") as f:
            f.write(str(value))

    def try_write(self, attr, value):
        try:
            self.write(attr, value)
            return True
        except PermissionError:
            return False
        except OSError:
            return False


def read_file(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


CONF_PATH = os.path.expanduser("~/.config/fevmcc.ini")


def load_conf():
    c = configparser.ConfigParser()
    c.read(CONF_PATH)
    if "localdb" not in c:
        c["localdb"] = {"FanFlag": "1", "customfanone": "50", "customfantwo": "50"}
    return c


def save_conf(c):
    os.makedirs(os.path.dirname(CONF_PATH), exist_ok=True)
    with open(CONF_PATH, "w") as f:
        c.write(f)


class FanCard(Gtk.Box):
    def __init__(self, hw: Sysfs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.hw = hw
        self.conf = load_conf()
        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(16)
        self.set_margin_end(16)

        self.fan1_path = find_hwmon_fan("fan1_input")
        self.fan2_path = find_hwmon_fan("fan2_input")
        # PPT (package power) from amdgpu; real die temp (Tctl) from k10temp
        self.power_path = find_hwmon_attr("amdgpu", "power1_average")
        self.tctl_path = find_hwmon_attr("k10temp", "temp1_input")

        # --- mode selector ---
        mode_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.auto_btn = Gtk.ToggleButton(label="自动")
        self.manual_btn = Gtk.ToggleButton(label="自定义")
        self.max_btn = Gtk.ToggleButton(label="最大")
        for b in (self.auto_btn, self.manual_btn, self.max_btn):
            b.set_hexpand(True)
            mode_row.append(b)
        self.manual_btn.set_group(self.auto_btn)
        self.max_btn.set_group(self.auto_btn)

        self.auto_btn.connect("toggled", self.on_mode, "auto")
        self.manual_btn.connect("toggled", self.on_mode, "manual")
        self.max_btn.connect("toggled", self.on_mode, "max")
        self.append(mode_row)

        # --- sliders ---
        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.attach(Gtk.Label(label="风扇 1", xalign=0), 0, 0, 1, 1)
        self.s1 = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.s1.set_hexpand(True)
        self.s1.set_value(50)
        self.s1.connect("value-changed", self.on_duty, 1)
        self.s1_label = Gtk.Label(label="--")
        grid.attach(self.s1, 1, 0, 1, 1)
        grid.attach(self.s1_label, 2, 0, 1, 1)

        grid.attach(Gtk.Label(label="风扇 2", xalign=0), 0, 1, 1, 1)
        self.s2 = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.s2.set_hexpand(True)
        self.s2.set_value(50)
        self.s2.connect("value-changed", self.on_duty, 2)
        self.s2_label = Gtk.Label(label="--")
        grid.attach(self.s2, 1, 1, 1, 1)
        grid.attach(self.s2_label, 2, 1, 1, 1)
        self.append(grid)

        # --- RPM / temp / power readout ---
        info = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        info.set_halign(Gtk.Align.CENTER)
        self.rpm1 = Gtk.Label(label="风扇1: -- RPM")
        self.rpm2 = Gtk.Label(label="风扇2: -- RPM")
        self.temp = Gtk.Label(label="CPU: -- °C")
        self.power = Gtk.Label(label="功率: -- W")
        info.append(self.rpm1)
        info.append(self.rpm2)
        info.append(self.temp)
        info.append(self.power)
        self.append(info)

        self._updating = False
        self.set_sliders_sensitive(False)
        # restore persisted fan mode (mirrors the Windows tool's test.ini)
        try:
            flag = self.conf.getint("localdb", "FanFlag")
            d1 = self.conf.getint("localdb", "customfanone")
            d2 = self.conf.getint("localdb", "customfantwo")
        except (ValueError, configparser.Error):
            flag, d1, d2 = 1, 50, 50
        self.s1.set_value(d1)
        self.s2.set_value(d2)
        {1: self.auto_btn, 2: self.manual_btn, 3: self.max_btn}.get(flag, self.auto_btn).set_active(True)
        GLib.timeout_add_seconds(2, self.refresh_readings)

    def set_sliders_sensitive(self, on):
        self.s1.set_sensitive(on)
        self.s2.set_sensitive(on)

    def on_mode(self, btn, mode):
        if not btn.get_active() or self._updating:
            return
        self.set_sliders_sensitive(mode == "manual")
        if mode in ("auto", "max"):
            if not self.hw.try_write("fan_mode", mode):
                self.show_write_error()
            self.conf.set("localdb", "FanFlag", "1" if mode == "auto" else "3")
        else:  # manual: push current slider values
            self.conf.set("localdb", "FanFlag", "2")
            self.hw.try_write("fan1_duty", int(self.s1.get_value()))
            self.hw.try_write("fan2_duty", int(self.s2.get_value()))
        save_conf(self.conf)

    def on_duty(self, scale, fan):
        v = int(scale.get_value())
        (self.s1_label if fan == 1 else self.s2_label).set_text(f"{v}%")
        if self.manual_btn.get_active():
            self.hw.try_write(f"fan{fan}_duty", v)
            self.conf.set("localdb", f"customfan{'one' if fan == 1 else 'two'}", str(v))
            save_conf(self.conf)

    def refresh_readings(self):
        if self.fan1_path:
            v = read_file(self.fan1_path)
            if v:
                self.rpm1.set_text(f"风扇1: {v} RPM")
        if self.fan2_path:
            v = read_file(self.fan2_path)
            if v:
                self.rpm2.set_text(f"风扇2: {v} RPM")
        # 优先用 k10temp 的真实核心温度，退化到 EC 温度
        t = read_file(self.tctl_path) if self.tctl_path else None
        if t and t.isdigit():
            self.temp.set_text(f"CPU: {int(t) // 1000} °C")
        else:
            t = self.hw.read("cpu_temp")
            if t:
                self.temp.set_text(f"CPU: {t} °C (EC)")
        if self.power_path:
            v = read_file(self.power_path)
            if v and v.isdigit():
                w = int(v) / 1_000_000
                if w >= 1:  # 滤掉偶发的异常低读数
                    self.power.set_text(f"功率: {w:.0f} W")
        return True

    def show_write_error(self):
        root = self.get_root()
        if hasattr(root, "toast_overlay"):
            root.toast_overlay.add_toast(Adw.Toast.new(
                "写入失败：需要权限。请以 root 运行或安装 udev 规则。"))


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="FEVM 控制中心")
        self.set_default_size(480, 640)
        self.hw = Sysfs()

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)

        if not self.hw.ok:
            banner = Adw.Banner(title="未检测到 fevm-wmi 驱动，请先安装并加载内核模块")
            banner.set_revealed(True)
            view.add_top_bar(banner)

        self.toast_overlay = Adw.ToastOverlay()
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(maximum_size=520)
        scroll.set_child(clamp)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        clamp.set_child(box)
        self.toast_overlay.set_child(scroll)
        view.set_content(self.toast_overlay)
        self.set_content(view)

        # ========== 性能模式 ==========
        box.append(self.section_label("性能模式"))
        frame = Gtk.Frame()
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        mode_box.set_margin_top(12)
        mode_box.set_margin_bottom(12)
        mode_box.set_margin_start(12)
        mode_box.set_margin_end(12)
        self.mode_btns = {}
        first = None
        for key, label, watts in MODES:
            b = Gtk.ToggleButton()
            b.set_child(Gtk.Label(label=f"{label}\n{watts}"))
            b.get_child().set_justify(Gtk.Justification.CENTER)
            b.set_hexpand(True)
            b.connect("toggled", self.on_power_mode, key)
            if first is None:
                first = b
            else:
                b.set_group(first)
            self.mode_btns[key] = b
            mode_box.append(b)
        frame.set_child(mode_box)
        box.append(frame)

        # ========== 风扇 ==========
        box.append(self.section_label("风扇控制"))
        frame2 = Gtk.Frame()
        self.fan_card = FanCard(self.hw)
        frame2.set_child(self.fan_card)
        box.append(frame2)

        # ========== 系统信息 ==========
        box.append(self.section_label("系统信息"))
        info_grp = Adw.PreferencesGroup()
        for title, val in self.sysinfo():
            row = Adw.ActionRow(title=title, subtitle=val)
            info_grp.add(row)
        box.append(info_grp)

        self.refresh_power_mode()

    @staticmethod
    def section_label(text):
        l = Gtk.Label(label=text, xalign=0)
        l.add_css_class("heading")
        l.set_margin_start(4)
        return l

    def on_power_mode(self, btn, key):
        if not btn.get_active():
            return
        if not self.hw.try_write("power_mode", key):
            self.toast_overlay.add_toast(Adw.Toast.new("设置失败：无权限或固件拒绝"))
            GLib.idle_add(self.refresh_power_mode)
            return
        c = load_conf()
        c["localdb"]["PowerMode"] = key
        save_conf(c)

    def refresh_power_mode(self):
        cur = self.hw.read("power_mode")
        for key, b in self.mode_btns.items():
            b.handler_block_by_func(self.on_power_mode)
            b.set_active(key == cur)
            b.handler_unblock_by_func(self.on_power_mode)
        return False

    def sysinfo(self):
        def cpu():
            try:
                for line in open("/proc/cpuinfo"):
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
            except OSError:
                pass
            return "-"

        def mem():
            try:
                for line in open("/proc/meminfo"):
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        return f"{kb/1048576:.1f} GB"
            except OSError:
                pass
            return "-"

        rows = [
            ("系统", read_file("/etc/os-release") and
             dict(l.split("=", 1) for l in open("/etc/os-release")
                  if "=" in l).get("PRETTY_NAME", "-").strip('"') or "-"),
            ("内核", os.uname().release),
            ("处理器", cpu()),
            ("主板", f"{read_file('/sys/class/dmi/id/board_name') or '-'} "
                     f"({read_file('/sys/class/dmi/id/sys_vendor') or '-'})"),
            ("BIOS 版本", read_file("/sys/class/dmi/id/bios_version") or "-"),
            ("BIOS 日期", read_file("/sys/class/dmi/id/bios_date") or "-"),
            ("内存", mem()),
        ]
        return rows


class FevmApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.fevm.controlcenter",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_activate(self):
        win = MainWindow(self)
        win.present()


def main():
    app = FevmApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
