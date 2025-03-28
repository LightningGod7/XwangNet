#!/bin/sh

# Set variables
ASLR=2
LEGACY_LAYOUT=""
MOUNT_DEV_TREE=1
# Mount points
mount -t proc proc /proc
mount -t sysfs sys /sys

# Check if MOUNT_DEV_TREE is set to 1 and then mount
if [ "$MOUNT_DEV_TREE" -eq 1 ]; then
    mount -t devtmpfs devtmpfs /dev
fi

# Apply ASLR setting
if [ -f /proc/sys/kernel/randomize_va_space ]; then
    echo $ASLR > /proc/sys/kernel/randomize_va_space
fi

# Set legacy VA layout if needed
if [ -n "$LEGACY_LAYOUT" ] && [ -f /proc/sys/vm/legacy_va_layout ]; then
    echo $LEGACY_LAYOUT > /proc/sys/vm/legacy_va_layout
fi

# Various fixes & init tasks
mkdir -p /var/run/ubusd

# Start required services if they exist
[ -x /sbin/ubusd ] && /sbin/ubusd &
[ -x /sbin/init ] && /sbin/init &

[ -x /etc/rc.d/S50lighttpd ] && /etc/rc.d/S50lighttpd start &
[ -x /etc/rc.d/S50php7-fastcgi ] && /etc/rc.d/S50php7-fastcgi start &

# Wait for background processes
#wait

# Start an interactive shell
exec /bin/sh
