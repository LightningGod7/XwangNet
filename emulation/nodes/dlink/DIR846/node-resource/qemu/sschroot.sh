#!/bin/bash

if [[ "$SSH_ORIGINAL_COMMAND" == "chroot" ]]; then
    exec chroot /root/rootfs /bin/sh
else
    exec /bin/bash -i  
fi
