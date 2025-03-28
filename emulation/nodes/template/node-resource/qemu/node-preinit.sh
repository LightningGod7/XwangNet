#!/bin/bash
### BEGIN INIT INFO
# Provides:          node-preinit
# Required-Start:    $remote_fs $syslog
# Required-Stop:     $remote_fs $syslog
# Default-Start:     2 3 4 5
# Default-Stop:      0 1 6
# Short-Description: Node Preinit Script
# Description:       Initializes the node environment on boot.
### END INIT INFO

LOG_FILE="/var/log/execv.log"

# Mount points
mount --bind /proc /root/rootfs/proc
mount --bind /sys /root/rootfs/sys
mount --bind /dev /root/rootfs/dev

log_format() {
    while read -r line; do
        # Check if the line contains an execve call
        if [[ "$line" =~ execve\(\"([^\"]+)\"\,\ \[([^\]]+) ]]; then
            command_path="${BASH_REMATCH[1]}"
            command_args="${BASH_REMATCH[2]}"

            # Remove the first argument (which is usually the program name)
            command_args=$(echo "$command_args" | awk -F ', ' '{for (i=2; i<=NF; i++) printf "%s ", $i; print ""}')

            # Trim trailing whitespace
            command_args=$(echo "$command_args" | xargs)

            # Construct the final command
            if [[ -n "$command_args" ]]; then
                full_command="$command_path $command_args"
            else
                full_command="$command_path"
            fi

            # Extract PID from the line
            pid=$(echo "$line" | awk '{print $2}' | tr -d '[]')

            # Get process and parent details
            if [[ -n "$pid" ]]; then
                process_info=$(ps -o pid,ppid,comm --no-headers -p "$pid" 2>/dev/null)                if [[ -n "$process_info" ]]; then
                    pid=$(awk '{print $1}' <<< "$process_info")
                    ppid=$(awk '{print $2}' <<< "$process_info")
                    pname=$(awk '{print $3}' <<< "$process_info")
                    parent_name=$(ps -o comm= --no-headers -p "$ppid" 2>/dev/null || echo "N/A")
                else
                    pname="Unknown"
                    ppid="Unknown"
                    parent_name="Unknown"
                fi
            fi

            # Log the formatted output
            {
                echo "$line"
                echo "execve: $full_command"
                echo "PID: $pid ($pname)"
                echo "PPID: $ppid ($parent_name)"
                echo ""
            } | tee -a "$LOG_FILE"
        fi
    done
}


# Send execve log to QEMU serial monitor
tail -f "$LOG_FILE" > /dev/ttyS1 &

# Attach strace to PID 1 and follow all processes
for pid in $(pgrep .); do
      pname=$(ps -o comm= -p "$pid" 2>/dev/null)  # Get process name
      if [[ "$pname" != "tail" ]]; then
          echo "Attaching strace to PID: $pid (Process: $pname)" # Debug output
          strace -ff -e execve -s 65535 -p "$pid" 2>&1 | log_format &
          #strace -ff -e execve -s 65535 -p "$pid" 2>&1 | tee -a /var/log/execv.log &
      fi
done

#send execve log to qemu serial monitor

#Start node init
chroot /root/rootfs /node-init.sh &