#!/bin/sh

# Set variables
ASLR=2
LEGACY_LAYOUT=""
MOUNT_DEV_TREE=0

# Mount points
mount -t proc proc /proc
mount -t sysfs sys /sys

# Check if MOUNT_DEV_TREE is set to 1 and then mount
if [ "$MOUNT_DEV_TREE" -eq 1 ]; then
    mount -t devtmpfs devtmpfs /dev
fi

# ASLR Options
echo $ASLR > /proc/sys/kernel/randomize_va_space

# Set legacy VA layout if needed
if [ -n "$LEGACY_LAYOUT" ]; then
    echo $LEGACY_LAYOUT > /proc/sys/vm/legacy_va_layout
fi

# Various fixes & init tasks
/etc/rc.d/rcS


# Wait then patch ip, init script replaces qemu ip
sleep 3
/sbin/ifconfig eth0:0 10.0.2.15 netmask 255.255.255.0 up

#Shell
/bin/sh
