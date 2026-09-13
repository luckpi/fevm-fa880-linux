# FEVM FA880 PRO Linux 控制中心

对 Windows 版 `MiniPcControlCenterSetup2.0_FEVM_FA880.exe` 的 Linux 移植。
原软件（PyInstaller 打包的 PyQt5 应用）通过 WMI-ACPI 接口控制硬件，
本项目在 Linux 上提供等价功能。

## 架构

```
┌────────────────────────────────────────────┐
│  GUI  fevmcc (GTK4/Adwaita)                 │
│  CLI  fevmctl                               │
│         │ 读写 sysfs                         │
├─────────▼──────────────────────────────────┤
│  fevm-wmi 内核模块 (DKMS)                    │
│  /sys/bus/wmi/devices/99D89064-.../         │
│         │ wmidev_evaluate_method            │
├─────────▼──────────────────────────────────┤
│  \_SB.WMIB  (PNP0C14, _UID=IP3POWERSWITCH)  │
│  WMAA(instance, method_id, in)  →  EC       │
└────────────────────────────────────────────┘
```

## 功能对照

| 功能 | Windows 原版 | Linux 版 |
|------|------------|----------|
| 电源模式（安静54W/均衡65W/狂暴70W） | SetPowerMode | `power_mode` sysfs |
| 风扇 自动/自定义%/最大 | SetFanControl duty 101/0-100/100 | `fan_mode` `fanN_duty` |
| 双风扇 RPM | GetFanControl | `fanN_rpm` + hwmon |
| CPU 温度 | GetHwTemp(1) | `cpu_temp` + hwmon |
| 系统信息 | WMI 查询 | /sys、/proc |
| 前置面板模式按钮 | WMI event GUID | 切换时 GUI 自动刷新 |

原版 Windows 端还存在但 FA880 固件不支持/无效果、已移除的功能：
OSD 开关、CPU Turbo、灯光模式、键盘灯效（SetFeatureValue/SetKbd* 方法
写入被固件接受但无任何效果或直接拒绝）、BIOS/OSD 升级（FTP 下载
Windows exe）、护眼模式（AMD ADL，Linux 下对应 GNOME 夜灯设置，
由系统设置接管）。

## 安装

```bash
sudo bash install.sh
```

安装脚本会：
1. 用 DKMS 构建并安装 `fevm-wmi` 内核模块（随内核升级自动重建）
2. 加载模块、安装 udev 规则（控制属性放开权限）与开机自载配置
3. 安装 `fevmctl`（CLI）和 `fevmcc`（GUI）到 `/usr/local/bin`

## 使用

```bash
fevmctl status            # 查看电源模式/风扇转速/温度/特性位
fevmctl power quiet       # 安静模式 (54W)
fevmctl power balanced    # 均衡模式 (65W)
fevmctl power performance # 狂暴模式 (70W)
fevmctl fan auto          # 风扇自动
fevmctl fan max           # 风扇全速
fevmctl fan manual 45     # 风扇 45%
fevmcc                    # 图形界面
```

## WMI 接口参考（供调试）

方法设备 GUID `99D89064-8D50-42BB-BEA9-155B2E5D0FCD`（object id "AA"），
方法 ID：1=SetPowerMode 2=GetPowerMode 3=SetFanControl 4=GetFanControl
5/6=键盘背光 7/8=键盘灯模式 9/10=Fn控制 11=GetHwTemp 12=SetFeatureValue
13=GetFeatureValue。事件 GUID `8FAFC061-22DA-46E2-91DB-1FE3D7E5FF3C`
（前置面板模式切换按钮）。

相关 sysfs 属性（驱动绑定后出现在方法设备目录下）：
`power_mode`(rw) `fan_mode`(wo: auto|max) `fan1_duty`/`fan2_duty`(wo: 0-100|101=auto)
`fan1_rpm` `fan2_rpm` `cpu_temp` `feature_mask` `mode_count` `fan_count`
`gpu_present`。
