// SPDX-License-Identifier: GPL-2.0
/*
 * FEVM FA880 PRO (B39-880) WMI control driver
 *
 * Binds the "Ip3PowerSwitch" WMI interface exposed by \_SB.WMIB
 * (PNP0C14, _UID "IP3POWERSWITCH").  This is the same interface the
 * vendor's Windows "ControlCenter" application talks to.
 *
 * Method device GUID : 99D89064-8D50-42BB-BEA9-155B2E5D0FCD (object id "AA")
 * Event  device GUID : 8FAFC061-22DA-46E2-91DB-1FE3D7E5FF3C (\_SB.AMW0)
 *
 * Method summary (WmiMethodId, from the firmware MOF):
 *   1  SetPowerMode      in: u8 mode            out: u8 status
 *   2  GetPowerMode      in: -                  out: u8 mode
 *   3  SetFanControl     in: u8 fan, u8 duty    out: u8 status
 *   4  GetFanControl     in: u8 fan             out: u32 packed RPM
 *   5  SetKbdBltBright.  in: u8 mode            out: u8 status
 *   6  GetKbdBltBright.  in: u8 mode            out: u8 status
 *   7  SetKbdLightMode   in: u8 mode            out: u8 status
 *   8  GetKbdLightMode   in: u8 mode            out: u8 status
 *   9  SetKbdFnCtrl      in: u8 ctrl, u8 status out: u8 status
 *  10  GetKbdFnCtrl      in: u8 ctrl            out: u16 status
 *  11  GetHwTemp         in: u8 type            out: u32 temp
 *  12  SetFeatureValue   in: u8 x4              out: u8 status
 *  13  GetFeatureValue   in: u8 x4              out: u32 status
 */

#include <linux/acpi.h>
#include <linux/device.h>
#include <linux/hwmon.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/types.h>
#include <linux/wmi.h>

#define FEVM_WMI_METHOD_GUID	"99D89064-8D50-42BB-BEA9-155B2E5D0FCD"
#define FEVM_WMI_EVENT_GUID	"8FAFC061-22DA-46E2-91DB-1FE3D7E5FF3C"

enum fevm_method_id {
	FEVM_SET_POWER_MODE	= 1,
	FEVM_GET_POWER_MODE	= 2,
	FEVM_SET_FAN_CONTROL	= 3,
	FEVM_GET_FAN_CONTROL	= 4,
	FEVM_SET_KBD_BLIGHT	= 5,
	FEVM_GET_KBD_BLIGHT	= 6,
	FEVM_SET_KBD_LMODE	= 7,
	FEVM_GET_KBD_LMODE	= 8,
	FEVM_SET_KBD_FNCTRL	= 9,
	FEVM_GET_KBD_FNCTRL	= 10,
	FEVM_GET_HW_TEMP	= 11,
	FEVM_SET_FEATURE	= 12,
	FEVM_GET_FEATURE	= 13,
};

/* Power modes reported/accepted by SetPowerMode/GetPowerMode */
#define FEVM_MODE_BALANCED	0
#define FEVM_MODE_PERFORMANCE	1
#define FEVM_MODE_QUIET		2
#define FEVM_MODE_SUPER		3

/* SetFanControl duty values */
#define FEVM_FAN_AUTO		101	/* firmware auto curve */
#define FEVM_FAN_MAX		100	/* full speed */

struct fevm_wmi {
	struct wmi_device *mdev;	/* method device */
	struct device *hwmon;
	struct mutex lock;		/* serialises WMAA calls */
};

static struct fevm_wmi fevm = {
	.lock = __MUTEX_INITIALIZER(fevm.lock),
};

/*
 * Call a WMAA method.  @in may be NULL/empty, @out receives the first
 * ACPI object of the result converted to an unsigned 64-bit value
 * (INTEGER results) or its first bytes (BUFFER results, little-endian).
 */
static int fevm_wmi_call(u32 method_id, const u8 *in_data, size_t in_len,
			 u64 *out_val)
{
	struct acpi_buffer input = { in_len, (void *)in_data };
	struct acpi_buffer output = { ACPI_ALLOCATE_BUFFER, NULL };
	struct wmi_device *mdev;
	union acpi_object *obj;
	acpi_status status;
	int ret = 0;

	mdev = fevm.mdev;
	if (!mdev)
		return -ENODEV;

	mutex_lock(&fevm.lock);
	status = wmidev_evaluate_method(mdev, 0, method_id, &input, &output);
	mutex_unlock(&fevm.lock);

	if (ACPI_FAILURE(status)) {
		dev_dbg(&mdev->dev, "WMAA method %u failed: %s\n",
			method_id, acpi_format_exception(status));
		return -EIO;
	}

	obj = output.pointer;
	if (!obj)
		return -ENODATA;

	if (out_val) {
		switch (obj->type) {
		case ACPI_TYPE_INTEGER:
			*out_val = obj->integer.value;
			break;
		case ACPI_TYPE_BUFFER: {
			u64 v = 0;
			u32 i;

			for (i = 0; i < obj->buffer.length && i < 8; i++)
				v |= (u64)obj->buffer.pointer[i] << (8 * i);
			*out_val = v;
			break;
		}
		default:
			ret = -ENODATA;
		}
	}

	kfree(obj);
	return ret;
}

static int fevm_set_power_mode(u8 mode)
{
	u64 status;
	int ret;

	ret = fevm_wmi_call(FEVM_SET_POWER_MODE, &mode, 1, &status);
	if (ret)
		return ret;
	return status ? -EIO : 0;
}

static int fevm_get_power_mode(u64 *mode)
{
	return fevm_wmi_call(FEVM_GET_POWER_MODE, NULL, 0, mode);
}

static int fevm_set_fan(u8 fan, u8 duty)
{
	u8 in[2] = { fan, duty };
	u64 status;
	int ret;

	ret = fevm_wmi_call(FEVM_SET_FAN_CONTROL, in, sizeof(in), &status);
	if (ret)
		return ret;
	return status ? -EIO : 0;
}

/* GetFanControl returns packed u32: bits[31:16]=fan2 rpm, [15:0]=fan1 rpm */
static int fevm_get_fan_rpm(u32 *fan1, u32 *fan2)
{
	u8 in = 1;
	u64 val;
	int ret;

	ret = fevm_wmi_call(FEVM_GET_FAN_CONTROL, &in, 1, &val);
	if (ret)
		return ret;
	*fan1 = val & 0xffff;
	*fan2 = (val >> 16) & 0xffff;
	return 0;
}

static int fevm_get_hw_temp(u8 type, u32 *temp)
{
	u64 val;
	int ret;

	ret = fevm_wmi_call(FEVM_GET_HW_TEMP, &type, 1, &val);
	if (ret)
		return ret;
	*temp = val & 0xff;
	return 0;
}

static int fevm_get_feature(u8 selector, u32 *value)
{
	u8 in[4] = { selector, 0, 0, 0 };
	u64 val;
	int ret;

	ret = fevm_wmi_call(FEVM_GET_FEATURE, in, sizeof(in), &val);
	if (ret)
		return ret;
	*value = (u32)val;
	return 0;
}

/* ---------- sysfs attributes ---------- */

static ssize_t power_mode_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	static const char * const names[] = {
		[FEVM_MODE_BALANCED]	= "balanced",
		[FEVM_MODE_PERFORMANCE]	= "performance",
		[FEVM_MODE_QUIET]	= "quiet",
		[FEVM_MODE_SUPER]	= "super",
	};
	u64 mode;
	int ret;

	ret = fevm_get_power_mode(&mode);
	if (ret)
		return ret;
	if (mode < ARRAY_SIZE(names) && names[mode])
		return sysfs_emit(buf, "%s\n", names[mode]);
	return sysfs_emit(buf, "unknown-%llu\n", mode);
}

static ssize_t power_mode_store(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t count)
{
	u8 mode;

	if (sysfs_streq(buf, "balanced"))
		mode = FEVM_MODE_BALANCED;
	else if (sysfs_streq(buf, "performance"))
		mode = FEVM_MODE_PERFORMANCE;
	else if (sysfs_streq(buf, "quiet"))
		mode = FEVM_MODE_QUIET;
	else if (sysfs_streq(buf, "super"))
		mode = FEVM_MODE_SUPER;
	else if (!kstrtou8(buf, 0, &mode) && mode <= FEVM_MODE_SUPER)
		;
	else
		return -EINVAL;

	if (fevm_set_power_mode(mode))
		return -EIO;
	return count;
}
static DEVICE_ATTR_RW(power_mode);

static ssize_t fan_rpm_show(struct device *dev, char *buf, int fan)
{
	u32 f1, f2;
	int ret;

	ret = fevm_get_fan_rpm(&f1, &f2);
	if (ret)
		return ret;
	return sysfs_emit(buf, "%u\n", fan == 1 ? f1 : f2);
}

static ssize_t fan1_rpm_show(struct device *dev,
			     struct device_attribute *attr, char *buf)
{
	return fan_rpm_show(dev, buf, 1);
}
static DEVICE_ATTR_RO(fan1_rpm);

static ssize_t fan2_rpm_show(struct device *dev,
			     struct device_attribute *attr, char *buf)
{
	return fan_rpm_show(dev, buf, 2);
}
static DEVICE_ATTR_RO(fan2_rpm);

static ssize_t fan_duty_store(struct device *dev, const char *buf,
			      size_t count, int fan)
{
	u8 duty;
	int ret;

	ret = kstrtou8(buf, 0, &duty);
	if (ret)
		return ret;
	if (duty > FEVM_FAN_AUTO)
		return -EINVAL;
	ret = fevm_set_fan(fan, duty);
	if (ret)
		return ret;
	return count;
}

static ssize_t fan1_duty_store(struct device *dev,
			       struct device_attribute *attr,
			       const char *buf, size_t count)
{
	return fan_duty_store(dev, buf, count, 1);
}
static DEVICE_ATTR_WO(fan1_duty);

static ssize_t fan2_duty_store(struct device *dev,
			       struct device_attribute *attr,
			       const char *buf, size_t count)
{
	return fan_duty_store(dev, buf, count, 2);
}
static DEVICE_ATTR_WO(fan2_duty);

/*
 * fan_mode: auto -> duty 101, max -> duty 100, manual -> caller then
 * writes fanN_duty.  We keep the last-written mode for the show side.
 */
static ssize_t fan_mode_store(struct device *dev,
			      struct device_attribute *attr,
			      const char *buf, size_t count)
{
	u8 duty;
	int ret;

	if (sysfs_streq(buf, "auto"))
		duty = FEVM_FAN_AUTO;
	else if (sysfs_streq(buf, "max"))
		duty = FEVM_FAN_MAX;
	else
		return -EINVAL;

	ret = fevm_set_fan(1, duty);
	if (ret)
		return ret;
	ret = fevm_set_fan(2, duty);
	if (ret)
		return ret;
	return count;
}
static DEVICE_ATTR_WO(fan_mode);

static ssize_t cpu_temp_show(struct device *dev,
			     struct device_attribute *attr, char *buf)
{
	u32 temp;
	int ret;

	ret = fevm_get_hw_temp(1, &temp);
	if (ret)
		return ret;
	return sysfs_emit(buf, "%u\n", temp);
}
static DEVICE_ATTR_RO(cpu_temp);

static ssize_t feature_mask_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	u32 val;
	int ret;

	ret = fevm_get_feature(1, &val);
	if (ret)
		return ret;
	return sysfs_emit(buf, "0x%02x\n", val);
}
static DEVICE_ATTR_RO(feature_mask);

static ssize_t mode_count_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	u32 val;
	int ret;

	ret = fevm_get_feature(2, &val);
	if (ret)
		return ret;
	return sysfs_emit(buf, "%u\n", val);
}
static DEVICE_ATTR_RO(mode_count);

static ssize_t fan_count_show(struct device *dev,
			      struct device_attribute *attr, char *buf)
{
	u32 val;
	int ret;

	ret = fevm_get_feature(3, &val);
	if (ret)
		return ret;
	return sysfs_emit(buf, "%u\n", val);
}
static DEVICE_ATTR_RO(fan_count);

static ssize_t gpu_present_show(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	u32 val;
	int ret;

	ret = fevm_get_feature(4, &val);
	if (ret)
		return ret;
	return sysfs_emit(buf, "%u\n", val);
}
static DEVICE_ATTR_RO(gpu_present);

static struct attribute *fevm_wmi_attrs[] = {
	&dev_attr_power_mode.attr,
	&dev_attr_fan_mode.attr,
	&dev_attr_fan1_duty.attr,
	&dev_attr_fan2_duty.attr,
	&dev_attr_fan1_rpm.attr,
	&dev_attr_fan2_rpm.attr,
	&dev_attr_cpu_temp.attr,
	&dev_attr_feature_mask.attr,
	&dev_attr_mode_count.attr,
	&dev_attr_fan_count.attr,
	&dev_attr_gpu_present.attr,
	NULL,
};

static const struct attribute_group fevm_wmi_group = {
	.attrs = fevm_wmi_attrs,
};

/* ---------- hwmon (so `sensors` shows the WMI fans/temp too) ---------- */

static umode_t fevm_hwmon_is_visible(const void *data,
				   enum hwmon_sensor_types type,
				   u32 attr, int channel)
{
	return 0444;
}

static int fevm_hwmon_read(struct device *dev, enum hwmon_sensor_types type,
			 u32 attr, int channel, long *val)
{
	u32 f1, f2, temp;
	int ret;

	switch (type) {
	case hwmon_fan:
		ret = fevm_get_fan_rpm(&f1, &f2);
		if (ret)
			return ret;
		*val = channel ? f2 : f1;
		return 0;
	case hwmon_temp:
		ret = fevm_get_hw_temp(1, &temp);
		if (ret)
			return ret;
		*val = temp * 1000;
		return 0;
	default:
		return -EOPNOTSUPP;
	}
}

static int fevm_hwmon_read_label(struct device *dev,
				 enum hwmon_sensor_types type,
				 u32 attr, int channel, const char **str)
{
	if (type == hwmon_temp)
		*str = "EC temp";
	else
		*str = channel ? "fan2" : "fan1";
	return 0;
}

static const struct hwmon_ops fevm_hwmon_ops = {
	.is_visible = fevm_hwmon_is_visible,
	.read = fevm_hwmon_read,
	.read_string = fevm_hwmon_read_label,
};

static const struct hwmon_channel_info * const fevm_hwmon_info[] = {
	HWMON_CHANNEL_INFO(temp, HWMON_T_INPUT | HWMON_T_LABEL),
	HWMON_CHANNEL_INFO(fan, HWMON_F_INPUT | HWMON_F_LABEL,
			   HWMON_F_INPUT | HWMON_F_LABEL),
	NULL,
};

static const struct hwmon_chip_info fevm_hwmon_chip_info = {
	.ops = &fevm_hwmon_ops,
	.info = fevm_hwmon_info,
};

/* ---------- WMI events (front-panel mode selector) ---------- */

static void fevm_wmi_notify(struct wmi_device *wdev,
			  const struct wmi_buffer *data)
{
	/*
	 * Selector events carry an 8-byte payload; byte 1 holds the level:
	 * 0x11 quiet, 0x12 balanced, 0x13 max/performance, 0x14 super.
	 * Other payloads (e.g. the periodic 0x0a) are ignored so dmesg
	 * stays clean.
	 */
	if (data && data->length >= 2) {
		const u8 *b = data->data;

		if (b[0] == 0x01 && b[1] >= 0x11 && b[1] <= 0x14) {
			dev_info(&wdev->dev, "mode selector event: 0x%02x\n", b[1]);
			if (fevm.mdev)
				sysfs_notify(&fevm.mdev->dev.kobj, NULL, "power_mode");
		} else {
			dev_dbg(&wdev->dev, "event family=%u detail=0x%02x\n",
				b[0], b[1]);
		}
	}
}

/* ---------- driver registration ---------- */

static int fevm_wmi_probe(struct wmi_device *wdev, const void *context)
{
	int ret;

	/* context == 0 marks the method device; the event device only
	 * delivers notifications and gets no sysfs interface. */
	if (context != (void *)0)
		return 0;

	fevm.mdev = wdev;

	ret = sysfs_create_group(&wdev->dev.kobj, &fevm_wmi_group);
	if (ret)
		return ret;

	fevm.hwmon = devm_hwmon_device_register_with_info(&wdev->dev,
					"fevm_wmi", &fevm,
					&fevm_hwmon_chip_info, NULL);
	if (IS_ERR(fevm.hwmon))
		dev_warn(&wdev->dev, "hwmon registration failed\n");

	dev_info(&wdev->dev, "FEVM FA880 WMI control bound\n");
	return 0;
}

static void fevm_wmi_remove(struct wmi_device *wdev)
{
	if (wdev == fevm.mdev) {
		sysfs_remove_group(&wdev->dev.kobj, &fevm_wmi_group);
		fevm.mdev = NULL;
	}
}

static const struct wmi_device_id fevm_wmi_id_table[] = {
	{ FEVM_WMI_METHOD_GUID, (void *)0 },
	{ FEVM_WMI_EVENT_GUID, (void *)1 },
	{ }
};

static struct wmi_driver fevm_wmi_driver = {
	.driver = {
		.name = "fevm-wmi",
	},
	.id_table = fevm_wmi_id_table,
	.no_singleton = true,
	.probe = fevm_wmi_probe,
	.remove = fevm_wmi_remove,
	.notify_new = fevm_wmi_notify,
};

module_wmi_driver(fevm_wmi_driver);

MODULE_DEVICE_TABLE(wmi, fevm_wmi_id_table);
MODULE_AUTHOR("Devin");
MODULE_DESCRIPTION("FEVM FA880 PRO (B39-880) WMI control driver");
MODULE_LICENSE("GPL");
