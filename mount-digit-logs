#!/usr/bin/env bash
set -euo pipefail

serial=${1:?usage: mount-digit-logs.sh digit-v4-evt-xxxx}
conf=/tmp/teleport-ssh.conf
mount=/tmp/$serial-logs
host="root@$serial.agilityrobotics.teleport.sh"

# 1. Make sure we have a live Teleport session (no-op if already logged in).
tsh status >/dev/null 2>&1 || tsh login

# 2. (Re)generate the ssh config sshfs/ssh will use for the ProxyCommand.
tsh config > "$conf"

# 3. Clear any stale or dead mount left behind by a previous interrupted run.
#    A dead FUSE mount ("Transport endpoint is not connected") makes a fresh
#    sshfs fail or hang, so always start from a clean mountpoint.
if mountpoint -q "$mount" 2>/dev/null || ! ls "$mount" >/dev/null 2>&1; then
    fusermount3 -u "$mount" 2>/dev/null || umount -l "$mount" 2>/dev/null || true
fi
mkdir -p "$mount"

# 4. Warm up the SSH path IN THE FOREGROUND first. This is the key fix: the
#    `tsh proxy ssh` ProxyCommand may need to prompt (re-login / MFA tap). If
#    sshfs daemonizes (the old behavior) that prompt is detached from the
#    terminal and the call hangs forever with no output. Doing a foreground
#    `ssh ... true` forces any auth to happen in the open and fails loudly.
echo "Verifying SSH path to $serial ..."
ssh -F "$conf" "$host" true

# 5. Mount. Run sshfs in the foreground (-f) as a managed background job rather
#    than letting it self-daemonize, then poll for the mount with a timeout so
#    problems surface instead of hanging. reconnect + keepalives keep the mount
#    alive across brief drops.
sshfs -F "$conf" "$host:/var/log/ar-control/" "$mount" \
    -f -o reconnect -o ServerAliveInterval=15 -o ServerAliveCountMax=3 &
sshfs_pid=$!

for _ in $(seq 1 30); do
    mountpoint -q "$mount" && break
    if ! kill -0 "$sshfs_pid" 2>/dev/null; then
        echo "sshfs exited before mounting" >&2
        wait "$sshfs_pid"
        exit 1
    fi
    sleep 1
done

if mountpoint -q "$mount"; then
    echo "Mounted $serial logs at $mount (sshfs pid $sshfs_pid)"
    echo "Unmount with: fusermount3 -u $mount"
else
    echo "Timed out waiting for mount; killing sshfs (pid $sshfs_pid)" >&2
    kill "$sshfs_pid" 2>/dev/null || true
    exit 1
fi
