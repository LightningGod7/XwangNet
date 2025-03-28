#!/bin/bash

if [[ "$SSH_ORIGINAL_COMMAND" == "nochroot" ]]; then
    exec /bin/bash -i
else
    exec chroot /root/rootfs /bin/sh   
fi
