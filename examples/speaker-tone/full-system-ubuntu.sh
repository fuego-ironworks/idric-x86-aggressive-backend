#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)

UBUNTU_SUITE=${UBUNTU_SUITE:-noble}
UBUNTU_MIRROR=${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu}
QEMU=${QEMU:-qemu-system-x86_64}
ICK=${ICK:?set ICK to the qualified native ICK compiler}

work=${WORK_DIR:-"$repo_root/build/full-system-x86-speaker"}
rootfs="$work/rootfs"
disk="$work/ubuntu-x86-64.raw"
serial="$work/serial.log"
audio="$work/tone.wav"
program="$work/speaker-tone-alsa"
kernel="$work/vmlinuz"
initrd="$work/initrd.img"

rm -rf "$work"
mkdir -p "$work"

for command in debootstrap mkfs.ext4 python3 "$QEMU" "$ICK"; do
    command -v "$command" >/dev/null 2>&1 || {
        printf 'FAIL: required command not found: %s\n' "$command" >&2
        exit 1
    }
done

"$ICK" -fno-link-libatomic -std=c11 -D_POSIX_C_SOURCE=200809L -Wall -Wextra -Werror -O2 \
    "$script_dir/speaker-tone-alsa.c" -lasound -o "$program"

sudo debootstrap \
    --variant=minbase \
    --components=main,universe \
    --include=linux-image-generic,kmod,busybox-static,initramfs-tools,libasound2t64 \
    "$UBUNTU_SUITE" "$rootfs" "$UBUNTU_MIRROR"

sudo install -m 0755 "$program" "$rootfs/usr/local/bin/speaker-tone-alsa"

sudo tee "$rootfs/usr/local/sbin/device-action-init" >/dev/null <<'GUEST_INIT'
#!/bin/busybox sh

exec </dev/console >/dev/console 2>&1

/bin/busybox mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
/bin/busybox mount -t proc proc /proc 2>/dev/null || true
/bin/busybox mount -t sysfs sysfs /sys 2>/dev/null || true

/sbin/modprobe snd-intel8x0 2>/dev/null || /sbin/modprobe snd_intel8x0 2>/dev/null || true

i=0
while [ ! -e /dev/snd/pcmC0D0p ] && [ "$i" -lt 20 ]; do
    /bin/busybox sleep 1
    i=$((i + 1))
done

if [ ! -e /dev/snd/pcmC0D0p ]; then
    echo 'ALSA_PCM_PRESENT=0'
    echo 'PROGRAM_STATUS=125'
    cat /proc/asound/cards 2>/dev/null || true
    find /dev/snd -maxdepth 1 -type c -print 2>/dev/null || true
    /bin/busybox poweroff -f
    /bin/busybox sleep 5
    exit 125
fi

echo 'ALSA_PCM_PRESENT=1'
echo 'SPEAKER_TONE_RUNNING=1'
/usr/local/bin/speaker-tone-alsa plughw:0,0
status=$?
echo "PROGRAM_STATUS=$status"
cat /proc/asound/cards 2>/dev/null || true
/bin/busybox sleep 1
/bin/busybox sync
/bin/busybox poweroff -f
/bin/busybox sleep 5
exit "$status"
GUEST_INIT
sudo chmod 0755 "$rootfs/usr/local/sbin/device-action-init"

kernel_source=$(find "$rootfs/boot" -maxdepth 1 -type f -name 'vmlinuz-*' | sort | tail -n 1)
initrd_source=$(find "$rootfs/boot" -maxdepth 1 -type f -name 'initrd.img-*' | sort | tail -n 1)

[ -n "$kernel_source" ] && [ -n "$initrd_source" ] || {
    printf '%s\n' 'FAIL: Ubuntu rootfs did not install a kernel and initrd' >&2
    exit 1
}

sudo cp "$kernel_source" "$kernel"
sudo cp "$initrd_source" "$initrd"
sudo chown "$(id -u):$(id -g)" "$kernel" "$initrd"

truncate -s 2G "$disk"
sudo mkfs.ext4 -q -d "$rootfs" "$disk"
sudo chown "$(id -u):$(id -g)" "$disk"

rm -f "$serial" "$audio"

"$QEMU" \
    -machine pc,accel=tcg \
    -cpu qemu64 \
    -m 512M \
    -kernel "$kernel" \
    -initrd "$initrd" \
    -append 'console=ttyS0,115200 root=/dev/vda rw init=/usr/local/sbin/device-action-init panic=-1' \
    -drive "file=$disk,format=raw,if=virtio" \
    -display none \
    -serial "file:$serial" \
    -audiodev "wav,id=audio0,path=$audio" \
    -device AC97,audiodev=audio0 \
    -no-reboot &
qemu_pid=$!

cleanup_qemu() {
    if kill -0 "$qemu_pid" 2>/dev/null; then
        kill "$qemu_pid" 2>/dev/null || true
        wait "$qemu_pid" 2>/dev/null || true
    fi
}
trap cleanup_qemu EXIT HUP INT TERM

i=0
while [ "$i" -lt 150 ]; do
    if [ -f "$serial" ] && grep -q 'PROGRAM_STATUS=' "$serial"; then
        break
    fi
    if ! kill -0 "$qemu_pid" 2>/dev/null; then
        break
    fi
    sleep 1
    i=$((i + 1))
done

if ! [ -f "$serial" ] || ! grep -q 'SPEAKER_TONE_RUNNING=1' "$serial"; then
    cat "$serial" 2>/dev/null || true
    printf '%s\n' 'FAIL: guest never reached speaker-tone execution' >&2
    exit 1
fi

i=0
while kill -0 "$qemu_pid" 2>/dev/null && [ "$i" -lt 30 ]; do
    sleep 1
    i=$((i + 1))
done
cleanup_qemu
trap - EXIT HUP INT TERM

cat "$serial"
grep -q 'ALSA_PCM_PRESENT=1' "$serial"
grep -q 'PROGRAM_STATUS=0' "$serial"

python3 - "$audio" <<'PY'
import array
import sys
import wave

path = sys.argv[1]
with wave.open(path, "rb") as wav:
    channels = wav.getnchannels()
    width = wav.getsampwidth()
    rate = wav.getframerate()
    frames = wav.readframes(wav.getnframes())

if width != 2:
    raise SystemExit(f"FAIL: expected QEMU WAV s16 samples, got width={width}")
if channels < 1:
    raise SystemExit("FAIL: WAV has no channels")

samples = array.array("h")
samples.frombytes(frames)
if sys.byteorder != "little":
    samples.byteswap()
mono = samples[0::channels]
if not mono:
    raise SystemExit("FAIL: WAV contains no samples")

peak = max(abs(value) for value in mono)
if peak < 2000:
    raise SystemExit(f"FAIL: captured audio peak is too small: {peak}")
threshold = max(1000, peak // 8)
active_indices = [i for i, value in enumerate(mono) if abs(value) >= threshold]
if not active_indices:
    raise SystemExit("FAIL: no active captured audio")

max_gap = max(1, rate // 50)
clusters = []
start = previous = active_indices[0]
active_count = 1
for index in active_indices[1:]:
    if index - previous > max_gap:
        clusters.append((active_count, start, previous + 1))
        start = index
        active_count = 1
    else:
        active_count += 1
    previous = index
clusters.append((active_count, start, previous + 1))
active_count, start, stop = max(clusters)
active = mono[start:stop]
duration = len(active) / rate
if not 0.18 <= duration <= 0.40:
    raise SystemExit(f"FAIL: dominant tone duration {duration:.6f}s is outside expected range")

mean = sum(active) / len(active)
signs = [1 if value >= mean else -1 for value in active]
crossings = sum(a != b for a, b in zip(signs, signs[1:]))
frequency = crossings * rate / (2 * len(active))
print(f"captured_rate={rate} channels={channels} peak={peak}")
print(f"dominant_active_samples={active_count}")
print(f"dominant_duration_seconds={duration:.6f}")
print(f"estimated_frequency_hz={frequency:.3f}")
if not 350 <= frequency <= 450:
    raise SystemExit("FAIL: captured tone frequency is outside the 400 Hz fixture window")
PY

printf '%s\n' \
    'PASS: native x86-64 ALSA program executed through an Ubuntu guest audio device' \
    'PASS: guest exposed the AC97 playback PCM through the supported ALSA stack' \
    'PASS: QEMU WAV capture contains the expected bounded ~400 Hz tone'
