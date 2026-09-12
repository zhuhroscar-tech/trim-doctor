"""trim-doctor: verify the full SSD TRIM/discard passthrough chain on Linux
(device support -> LVM -> LUKS -> mount options), instead of manually
checking each layer."""

__version__ = "0.1.2"
