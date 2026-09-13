#!/usr/bin/env bash
# FEVM FA880 PRO Linux Control Center installer.
# Installs: fevm-wmi DKMS kernel module, fevmctl CLI, fevmcc GUI.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DKMS_SRC="/usr/src/fevm-wmi-1.0.0"

if [ "$(id -u)" -ne 0 ]; then
    echo "请以 root 运行:  sudo bash $0" >&2
    exit 1
fi

echo "[1/5] 安装内核模块源码到 $DKMS_SRC"
mkdir -p "$DKMS_SRC"
cp "$SRC_DIR/kernel-module/fevm-wmi.c" "$DKMS_SRC/"
cp "$SRC_DIR/kernel-module/Makefile"   "$DKMS_SRC/"
cp "$SRC_DIR/kernel-module/dkms.conf"  "$DKMS_SRC/"

echo "[2/5] DKMS 构建并安装"
dkms remove fevm-wmi/1.0.0 --all 2>/dev/null || true
dkms add     fevm-wmi/1.0.0
dkms build   fevm-wmi/1.0.0
dkms install fevm-wmi/1.0.0

echo "[3/5] 加载模块"
modprobe fevm-wmi || { echo "modprobe 失败，请检查 dmesg"; exit 1; }

echo "[4/5] 安装 udev 规则与自动加载配置"
install -m644 "$SRC_DIR/packaging/99-fevm-wmi.rules" /etc/udev/rules.d/
install -m644 "$SRC_DIR/packaging/fevm-wmi.conf"     /etc/modules-load.d/
udevadm control --reload
udevadm trigger -c bind -s wmi || true

echo "[5/5] 安装用户态工具"
install -m755 "$SRC_DIR/app/fevmctl.py" /usr/local/bin/fevmctl
install -m755 "$SRC_DIR/app/fevmcc.py"  /usr/local/bin/fevmcc
install -m644 "$SRC_DIR/app/com.fevm.controlcenter.desktop" /usr/local/share/applications/
mkdir -p /etc/xdg/autostart
install -m644 "$SRC_DIR/app/fevm-apply.desktop" /etc/xdg/autostart/

echo
echo "完成。验证："
echo "  fevmctl status        # 查看状态"
echo "  fevmctl power quiet   # 安静模式"
echo "  fevmcc                # 图形界面"
