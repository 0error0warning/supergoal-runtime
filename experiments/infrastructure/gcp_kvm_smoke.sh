#!/usr/bin/env bash
# Boots an actual nested Linux guest with KVM; software emulation is disallowed.
set -euo pipefail
umask 027
probe=$(mktemp -d /var/lib/supergoal-lab/probes/kvm-XXXXXXXX)
mkdir -p "$probe/initrd"/{bin,dev,proc,sys}
cp /bin/busybox "$probe/initrd/bin/busybox"
mknod "$probe/initrd/dev/console" c 5 1
cat > "$probe/initrd/init" <<'INIT'
#!/bin/busybox sh
/bin/busybox mount -t proc proc /proc
/bin/busybox mount -t sysfs sysfs /sys
echo SUPERGOAL_NESTED_KVM_GUEST_BOOTED
/bin/busybox uname -a
/bin/busybox poweroff -f
INIT
chmod 0755 "$probe/initrd/init"
(cd "$probe/initrd" && find . -print0 | cpio --null -o --format=newc | gzip -1) > "$probe/initrd.gz"
timeout 45 qemu-system-x86_64 -accel kvm -cpu host -smp 1 -m 256M \
    -nodefaults -no-reboot -display none -serial stdio \
    -kernel "/boot/vmlinuz-$(uname -r)" -initrd "$probe/initrd.gz" \
    -append 'console=ttyS0 rdinit=/init panic=-1' < /dev/null > "$probe/console.log" 2>&1
grep -q '^SUPERGOAL_NESTED_KVM_GUEST_BOOTED' "$probe/console.log"
printf 'KVM_GUEST_BOOT_PASS %s\n' "$probe"
tail -n 8 "$probe/console.log"
