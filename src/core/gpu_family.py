# -*- coding: utf-8 -*-
"""按本机显卡的 PCI Device ID 决定该写哪个家族的引导资产。

来源：nv-uefi-unlocker 的 docs/compatibility.md「路由与证据等级」。
同族设备共用 Booter/payload 资产，所以只需要认族，不需要认具体型号。

    Turing        0x1e02..=0x1fff、0x2182..=0x21d1   -> tu10x
    Ampere GA10x  0x2200..=0x22ff、0x2400..=0x25ff   -> ga102
    Ada           0x2600..=0x28ff                    -> ad102
    GA100/Hopper/Blackwell(50系) 不在支持路径中

注意：这张表是**区间**，不是逐卡列表。区间内未单列的 ID 继承家族资产，
这正是我们要的粒度 —— 不需要为每张卡维护一份清单。
"""
from __future__ import annotations

# (name, [(lo, hi), ...])  —— 区间闭区间，按 device id 比较
FAMILY_RANGES = (
    ("tu10x", ((0x1E02, 0x1FFF), (0x2182, 0x21D1))),
    ("ga102", ((0x2200, 0x22FF), (0x2400, 0x25FF))),
    ("ad102", ((0x2600, 0x28FF),)),
)

# 明确不支持的范围（给出准确原因，而不是笼统说「不支持」）
# 参考项目 docs/compatibility.md 明确：Turing 之前 / GA100 / RTX 50 不支持。
# 用户也确认：50 系既然项目没写适配，就不考虑。
UNSUPPORTED = (
    ("ga100", ((0x20B0, 0x20C2), (0x20F1, 0x20F1)), "ga100_datacenter"),
    ("blackwell", ((0x2900, 0x2FFF),), "blackwell_50"),
    ("pascal", ((0x1B00, 0x1BFF), (0x1C00, 0x1CFF), (0x1D00, 0x1DFF),
                (0x1F00, 0x1FFF)), "pascal_or_older_skip"),
)


def parse_device_id(text):
    """'10DE:2482' / '2482' / 0x2482 -> 0x2482。失败返回 None。"""
    if text is None:
        return None
    if isinstance(text, int):
        return text & 0xFFFF
    s = str(text).strip().upper()
    if ":" in s:
        s = s.split(":")[-1]
    s = s.replace("0X", "")
    try:
        return int(s, 16) & 0xFFFF
    except ValueError:
        return None


def family_for_device_id(device_id):
    """返回 (family, reason)。family 为 None 时 reason 说明为什么。"""
    dev = parse_device_id(device_id)
    if dev is None:
        return None, "no_device_id"
    for name, ranges, reason in UNSUPPORTED:
        for lo, hi in ranges:
            if lo <= dev <= hi:
                return None, reason
    for name, ranges in FAMILY_RANGES:
        for lo, hi in ranges:
            if lo <= dev <= hi:
                return name, "ok"
    return None, "unknown_device_id"


def family_for_snapshot(snapshot):
    """从采集结果里取 device id 并判族。返回 (family, reason, device_id)。"""
    gpu = None
    if snapshot is not None:
        gpu = snapshot.primary if hasattr(snapshot, "primary") else (
            snapshot[0] if isinstance(snapshot, list) and snapshot else snapshot)
    if not gpu:
        return None, "no_gpu", None
    dev = gpu.get("pci_device_id")
    family, reason = family_for_device_id(dev)
    return family, reason, dev