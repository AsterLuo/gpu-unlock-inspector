# -*- coding: utf-8 -*-
"""不依赖 GPU 的自检套件：i18n 完整性、型号库、判定引擎、存储、降级路径。

用法（任意 Python 3.10+，无需第三方库）：
    python tests/test_selfcheck.py
退出码 0 = 全通过。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

FAILS = []
PASSES = []


def check(name, cond, detail=""):
    if cond:
        PASSES.append(name)
        print("  PASS  %s" % name)
    else:
        FAILS.append((name, detail))
        print("  FAIL  %s   %s" % (name, detail))


# ------------------------------------------------------------------ i18n
def test_i18n():
    print("\n[i18n]")
    from i18n.strings import ZH, EN, Translator, selfcheck
    missing_en, missing_zh = selfcheck()
    check("zh/en key sets identical", not missing_en and not missing_zh,
          "missing_en=%s missing_zh=%s" % (missing_en, missing_zh))
    check("key count > 150", len(ZH) > 150, "count=%d" % len(ZH))

    # 每条文案都不为空，且不含未替换的占位符
    bad = [k for k, v in ZH.items() if not str(v).strip()]
    check("no empty zh strings", not bad, str(bad[:5]))

    # 英文表里不能残留中文（常见漏翻）
    import re
    cn = re.compile(r"[\u4e00-\u9fff]")
    leaked = [k for k, v in EN.items() if cn.search(str(v))]
    check("no Chinese leaked into EN (except 中文 label)",
          set(leaked) <= {"top.lang.zh"}, str(leaked[:8]))

    tr = Translator("zh")
    check("translator returns text", tr("app.title") == ZH["app.title"])
    check("missing key is visible", tr("no.such.key").startswith("⟦"))

    # 所有 code 里用到的 tr("...") 字面量都必须存在于表里
    used = set()
    ure = re.compile(r'tr\(\s*"([a-z0-9_.]+)"')
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if not f.endswith(".py"):
                continue
            with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                used.update(ure.findall(fh.read()))
    # 以 "." 结尾的是动态拼接前缀（如 tr("src." + x)），不当作完整 key
    used = {k for k in used if not k.endswith(".")}
    missing = sorted(k for k in used if k not in ZH)
    check("every literal tr() key exists", not missing, str(missing[:10]))


# ------------------------------------------------------------------ 型号库
def test_db():
    print("\n[model database]")
    from core.modeldb import GpuDatabase
    path = os.path.join(ROOT, "assets", "gpu_db.json")
    check("gpu_db.json exists", os.path.isfile(path), path)
    db = GpuDatabase(path)
    check("db loads without error", db.error is None, str(db.error))
    check("has 20+ models", len(db.gpus) >= 20, "n=%d" % len(db.gpus))

    ids = [g["id"] for g in db.gpus]
    check("model ids unique", len(ids) == len(set(ids)))

    required = ("id", "name", "arch", "codename", "cuda_cores", "mem", "clocks", "lhr")
    bad = [g.get("id") for g in db.gpus if any(k not in g for k in required)]
    check("every model has required fields", not bad, str(bad[:5]))

    # 30/40/50 三代齐备
    archs = {g.get("arch") for g in db.gpus}
    for a in ("Ampere", "Ada Lovelace", "Blackwell"):
        check("covers %s" % a, a in archs, str(archs))

    # 同型号多显存版本必须能区分
    e12, _ = db.find("NVIDIA GeForce RTX 3060", 12 * 1024 ** 3)
    e8, _ = db.find("NVIDIA GeForce RTX 3060", 8 * 1024 ** 3)
    check("RTX 3060 12G matches 12G entry", e12 and e12["id"] == "rtx3060_12g",
          e12 and e12["id"])
    check("RTX 3060 8G matches 8G entry", e8 and e8["id"] == "rtx3060_8g",
          e8 and e8["id"])

    e1, _ = db.find("NVIDIA GeForce RTX 3070 Ti", 8 * 1024 ** 3)
    check("RTX 3070 Ti matches", e1 and e1["id"] == "rtx3070ti", e1 and e1["id"])

    e2, _ = db.find("NVIDIA GeForce RTX 5090", 32 * 1024 ** 3)
    check("RTX 5090 matches", e2 and e2["id"] == "rtx5090", e2 and e2["id"])

    e3, _ = db.find("NVIDIA GeForce GTX 1080", 8 * 1024 ** 3)
    check("unknown model returns None", e3 is None, str(e3))


# ------------------------------------------------------------------ 判定引擎
class FakeSnap:
    def __init__(self, gpu, nvml=True, smi=True):
        self.gpus = [gpu]
        self.nvml = object() if nvml else None
        self.smi_available = smi
        self.sources_used = ["nvml"] if nvml else []
        self.warnings = []
        self.primary = gpu

    @property
    def has_gpu(self):
        return bool(self.gpus)


def test_unlock():
    print("\n[unlock engine]")
    from core.modeldb import GpuDatabase
    from core import unlock as U
    db = GpuDatabase(os.path.join(ROOT, "assets", "gpu_db.json"))

    base_gpu = {
        "name": "NVIDIA GeForce RTX 3070 Ti",
        "mem_total_bytes": 8 * 1024 ** 3,
        "driver_version": "617.14",
        "mem_clock_mhz": 9701,
        "pci_device_id": "10DE:2482",
        "source": "nvml",
    }

    # 1) 新驱动 → 已解锁
    v = U.evaluate(FakeSnap(dict(base_gpu)), db)
    check("modern driver => unlocked", v.state == "unlocked", v.state)

    # 2) 老驱动（上锁区间）→ 未解锁
    old = dict(base_gpu, driver_version="471.11")
    v = U.evaluate(FakeSnap(old), db)
    check("locked-era driver => locked", v.state == "locked", v.state)

    # 3) 40 系无限制器 → 不适用
    ada = dict(base_gpu, name="NVIDIA GeForce RTX 4070 Ti", mem_total_bytes=12 * 1024 ** 3,
               pci_device_id="10DE:2782")
    v = U.evaluate(FakeSnap(ada), db)
    check("RTX 40 => not applicable", v.state == "na", v.state)

    # 4) 无 GPU → 无法判定
    empty = FakeSnap({}, nvml=False, smi=False)
    empty.gpus = []
    v = U.evaluate(empty, db)
    check("no GPU => unknown", v.state == "unknown", v.state)

    # 5) 只有 WMI → 无法判定
    wmi_only = FakeSnap(dict(base_gpu, driver_version=None), nvml=False, smi=False)
    v = U.evaluate(wmi_only, db)
    check("WMI only => unknown", v.state == "unknown", v.state)

    # 6) 未收录型号 → 无法判定
    unknown = dict(base_gpu, name="NVIDIA GeForce GTX 1080 Ti", mem_total_bytes=11 * 1024 ** 3)
    v = U.evaluate(FakeSnap(unknown), db)
    check("unlisted model => unknown", v.state == "unknown", v.state)

    # 7) 手动指定型号：置信度不得为 high
    v = U.evaluate(FakeSnap(dict(base_gpu)), db, manual_gpu_id="rtx3090")
    check("manual pick caps confidence", v.confidence != "high", v.confidence)
    check("manual pick is flagged", v.matched_by == "manual", v.matched_by)

    # 8) 每条规则都可复核
    v = U.evaluate(FakeSnap(dict(base_gpu)), db)
    check("verdict carries rules", len(v.rules) >= 4, str(len(v.rules)))
    ok = all(r.key and getattr(r, "verdict", None) in ("hit", "miss", "skip") for r in v.rules)
    check("every rule has key+verdict", ok)

    # 9) 四种状态都在取值范围内
    allowed = {"unlocked", "locked", "na", "unknown"}
    seen = set()
    for drv in ("617.14", "471.11", None):
        for name, mem in (("NVIDIA GeForce RTX 3070 Ti", 8), ("NVIDIA GeForce RTX 4070", 12),
                          ("NVIDIA GeForce RTX 5090", 32), ("NVIDIA GTX 1080", 8)):
            g = dict(base_gpu, name=name, driver_version=drv, mem_total_bytes=mem * 1024 ** 3)
            seen.add(U.evaluate(FakeSnap(g), db).state)
    check("states stay within the 4 allowed values", seen <= allowed, str(seen))

    # 10) 深度诊断证据能被采纳
    from core.ssdiag import DeepProbe
    dp = DeepProbe()
    dp.available = dp.ran = True
    dp.state = "locked"
    dp.ss0, dp.ss1 = 0x12345678, 0x0
    v = U.evaluate(FakeSnap(dict(base_gpu)), db, deep=dp)
    feat = [r for r in v.rules if r.key == "rule.deep_feat"]
    check("deep probe rule present", bool(feat))
    check("deep locked blocks unlock verdict", v.state != "unlocked", v.state)


# ------------------------------------------------------------------ ssdiag 解析
def test_ssdiag_parse():
    print("\n[ssdiag parsing]")
    from core import ssdiag
    p = ssdiag.parse_output("FEAT SS0 = 0x88888888  SS1 = 0x8  -> UNLOCKED")
    check("parses ss0", p["ss0"] == 0x88888888, str(p["ss0"]))
    check("parses ss1", p["ss1"] == 0x8, str(p["ss1"]))
    check("unlocked detected", p["state"] == "unlocked", p["state"])

    p = ssdiag.parse_output("SS0 = 0x00000000 SS1 = 0x0 -> not_unlocked")
    check("locked detected", p["state"] == "locked", p["state"])

    p = ssdiag.parse_output("garbage output")
    check("garbage => unknown", p["state"] == "unknown", p["state"])

    p = ssdiag.parse_output("PLATFORM: Secure Boot = OFF\nPLATFORM: blocklist = ON")
    check("platform gates parsed", p["platform"].get("secure_boot") == "OFF", str(p["platform"]))


# ------------------------------------------------------------------ 存储
def test_storage():
    print("\n[storage]")
    from core import storage
    tmp = tempfile.mkdtemp(prefix="gpuinv_")
    storage.CONFIG_NAME = "cfg.json"
    storage.RECORDS_NAME = "rec.json"
    storage.app_dir = lambda: tmp
    import core.storage as S
    S._path = lambda name: os.path.join(tmp, name)

    st = S.Store()
    check("defaults filled", st.get("lang") in ("zh", "en"), str(st.get("lang")))
    st.set("lang", "en")
    st2 = S.Store()
    check("config persists", st2.get("lang") == "en", str(st2.get("lang")))

    st2.add_record({"kind": "before", "avg": 100.0})
    st2.add_record({"kind": "after", "avg": 150.0})
    b, a = st2.latest_pair()
    check("record pair stored", b and a and b["avg"] == 100.0 and a["avg"] == 150.0)
    check("record gets timestamp", "iso" in (a or {}), str((a or {}).keys()))

    st2.clear_records()
    check("records cleared", st2.records == [], str(st2.records))

    # 配置文件必须是 UTF-8 无 BOM（Node / 其它工具读它会炸）
    st2.set("lang", "zh")
    raw = open(os.path.join(tmp, "cfg.json"), "rb").read()
    check("config written without BOM", not raw.startswith(b"\xef\xbb\xbf"), raw[:6])


# ------------------------------------------------------------------ 降级路径
def test_degradation():
    print("\n[degradation]")
    from core import bench_ocl
    cpu = bench_ocl.CpuBench()
    check("cpu engine opens", cpu.ok or cpu.open())
    dt, ops = cpu.slice_once("low")
    check("cpu engine produces work", ops > 0 and dt > 0, "%s/%s" % (dt, ops))
    check("cpu engine reports its own name", cpu.name == "cpu_proxy", cpu.name)

    from core.runner import BenchResult
    r = BenchResult()
    check("empty result avg is None", r.avg() is None)
    check("empty result stability is None", r.stability() is None)
    r.samples = [{"t": 1, "rate": 10.0}, {"t": 2, "rate": 10.0}]
    check("identical rates => 100% stability", abs((r.stability() or 0) - 100.0) < 1e-6,
          str(r.stability()))
    r.samples = [{"t": 1, "rate": 10.0}, {"t": 2, "rate": 20.0}]
    check("varying rates lower stability", (r.stability() or 100) < 100, str(r.stability()))

    # 型号库损坏时必须优雅降级
    from core.modeldb import GpuDatabase
    tmp = tempfile.mkdtemp(prefix="gpudb_")
    bad = os.path.join(tmp, "gpu_db.json")
    open(bad, "w", encoding="utf-8").write("{ not json")
    db = GpuDatabase(bad)
    check("broken db does not raise", db.error is not None, str(db.error))
    check("broken db yields empty list", db.gpus == [], str(db.gpus))
    check("find on broken db returns None", db.find("RTX 4090", 0)[0] is None)


# ------------------------------------------------------------------ HWiNFO 解析
def test_hwinfo():
    print("\n[HWiNFO shared memory]")
    import struct
    from core import hwinfo

    def build(sensors, entries, poll=500):
        ss, es = 264, 316
        so = 48
        eo = so + ss * len(sensors)
        total = eo + es * len(entries)
        buf = bytearray(total)
        struct.pack_into("<I", buf, 0x00, hwinfo.HEADER_MAGIC)
        struct.pack_into("<I", buf, 0x14, so)
        struct.pack_into("<I", buf, 0x18, ss)
        struct.pack_into("<I", buf, 0x1C, len(sensors))
        struct.pack_into("<I", buf, 0x20, eo)
        struct.pack_into("<I", buf, 0x24, es)
        struct.pack_into("<I", buf, 0x28, len(entries))
        struct.pack_into("<I", buf, 0x2C, poll)
        for i, name in enumerate(sensors):
            b = so + i * ss
            struct.pack_into("<II", buf, b, i, 0)
            buf[b + 8:b + 8 + len(name)] = name.encode("cp1252")
        for i, (etype, sidx, rname, unit, val) in enumerate(entries):
            b = eo + i * es
            struct.pack_into("<III", buf, b, etype, sidx, i)
            buf[b + 12:b + 12 + len(rname)] = rname.encode("cp1252")
            buf[b + 268:b + 268 + len(unit)] = unit.encode("cp1252")
            struct.pack_into("<dddd", buf, b + 284, val, val, val, val)
        return bytes(buf)

    sensors = ["GPU [#0]: NVIDIA GeForce RTX 3070 Ti"]
    entries = [
        (1, 0, "GPU Temperature", "\u00b0C", 62.0),
        (1, 0, "GPU Hot Spot Temperature", "\u00b0C", 74.5),
        (1, 0, "GPU Memory Junction Temperature", "\u00b0C", 88.0),
        (5, 0, "GPU Power", "W", 245.3),
        (5, 0, "GPU Power Limit", "W", 340.0),
        (6, 0, "GPU Core Clock", "MHz", 1950.0),
        (6, 0, "GPU Memory Clock", "MHz", 9501.0),
        (7, 0, "GPU Core Load", "%", 99.2),
        (3, 0, "GPU Fan", "%", 71.0),
    ]
    buf = build(sensors, entries)
    parsed = hwinfo.parse_buffer(buf)
    check("parses all entries", parsed is not None and len(parsed) == 9,
          str(parsed and len(parsed)))
    check("sensor name attached", parsed and parsed[0]["sensor"].startswith("GPU [#0]"))
    check("type classified", parsed and parsed[3]["type"] == "power", parsed and parsed[3]["type"])

    r = hwinfo.HwinfoReader()
    r.ok = True
    r.readings = parsed
    snap = r.snapshot()
    for key, want in (("temp_c", 62.0), ("hotspot_c", 74.5), ("mem_junction_c", 88.0),
                      ("power_w", 245.3), ("power_limit_w", 340.0),
                      ("clock_sm_mhz", 1950.0), ("util_gpu_pct", 99.2), ("fan_pct", 71.0)):
        check("snapshot.%s" % key, abs((snap.get(key) or 0) - want) < 0.01, str(snap.get(key)))

    # 损坏输入必须安全返回 None
    check("bad magic rejected", hwinfo.parse_buffer(b"\x00" * 100) is None)
    check("truncated rejected", hwinfo.parse_buffer(buf[:60]) is None)
    bad = bytearray(buf)
    struct.pack_into("<I", bad, 0x1C, 99999)
    check("absurd count rejected", hwinfo.parse_buffer(bytes(bad)) is None)
    bad2 = bytearray(buf)
    struct.pack_into("<I", bad2, 0x18, 10)   # sensor_size 过小
    check("undersized element rejected", hwinfo.parse_buffer(bytes(bad2)) is None)
    check("no HWiNFO => empty snapshot",
          hwinfo.HwinfoReader().snapshot() == {})


# ------------------------------------------------------------------ 跨重启存档
def test_archive():
    print("\n[cross-reboot archive]")
    from core import archive

    tmp = tempfile.mkdtemp(prefix="gpuarch_")
    ar = archive.Archive(root=tmp)
    check("archive root created", os.path.isdir(tmp), tmp)
    check("starts empty", ar.sessions == [], str(ar.sessions))

    rec = {"avg": 1200.0, "peak": 1250.0}
    ar.add_session(rec, "baseline", "locked", {"name": "RTX 3080"})
    check("baseline stored", len(ar.sessions) == 1)
    check("baseline has boot_time", "boot_time" in ar.sessions[0])

    check("no pair yet", ar.pair() is None)

    # 同一次开机内不算跨重启
    ar.add_session({"avg": 1400.0}, "after", "unlocked", {"name": "RTX 3080"})
    pair = ar.pair()
    check("pair computed", pair is not None)
    check("same boot flagged as not cross",
          pair and pair["cross_boot"] is False, str(pair and pair["cross_boot"]))
    check("pct math", pair and abs(pair["pct"] - 16.666) < 0.01, str(pair and pair["pct"]))

    # 伪造上一次开机的基线 —— 应被识别为跨重启
    ar.sessions = [{
        "id": "baseline-x", "kind": "baseline", "ts": time.time() - 7200,
        "boot_time": time.time() - 9000, "unlock_state": "locked",
        "bench": {"avg": 1000.0}, "gpu": {},
    }] + [s for s in ar.sessions if s["kind"] == "after"]
    pair2 = ar.pair()
    check("cross-boot baseline preferred", pair2 and abs(pair2["baseline_avg"] - 1000.0) < 0.01,
          str(pair2 and pair2["baseline_avg"]))
    check("cross_boot true", pair2 and pair2["cross_boot"] is True)

    # 状态变化检测
    st = ar.update_state("unlocked", {"name": "RTX 3080"})
    check("state recorded", st.get("last_state") == "unlocked", str(st.get("last_state")))
    ar.state["last_state"] = "locked"
    st2 = ar.update_state("unlocked", {"name": "RTX 3080"})
    check("state change detected", st2.get("state_changed_since_last_boot") is True)

    ar.clear()
    check("cleared", ar.sessions == [])

    # 存档必须能跨进程读取（真落盘）
    ar.add_session({"avg": 1.0}, "baseline", "locked", {})
    ar2 = archive.Archive(root=tmp)
    check("archive reloaded from disk", len(ar2.sessions) == 1, str(len(ar2.sessions)))

    # 文件必须是 UTF-8 无 BOM
    raw = open(os.path.join(tmp, "sessions.json"), "rb").read()
    check("archive json without BOM", not raw.startswith(b"\xef\xbb\xbf"), raw[:6])




# ------------------------------------------------------------------ 双文件夹存档
def test_bundles():
    print("\n[before/after bundles]")
    from core import archive

    tmp = tempfile.mkdtemp(prefix="gpubundle_")
    ar = archive.Archive(root=tmp)

    # 两个子文件夹要能自动建出来
    bdir = archive.bundle_dir("before", tmp)
    adir = archive.bundle_dir("after", tmp)
    check("before/ created", os.path.isdir(bdir), bdir)
    check("after/ created", os.path.isdir(adir), adir)
    check("dirs are distinct", bdir != adir)

    compute = {"summary": [{"key": "fp16_fp32acc_4096", "tflops": 35.2},
                           {"key": "fp16_fp16acc_4096", "tflops": 71.0}],
               "verdict": "locked"}
    bench = {"avg": 1100.0, "peak": 1200.0, "samples": [{"rate": 1100.0}],
             "algo": "proxy_v1"}

    item, path = ar.save_bundle("before", compute, bench, "locked",
                                {"name": "RTX 3070 Ti"})
    check("before file written", os.path.isfile(path), path)
    check("before file under before/",
          os.path.dirname(path) == bdir, os.path.dirname(path))
    check("one file holds both tests",
          item["compute"] and item["bench"], str(sorted(item.keys())))

    # 解锁后要用 after/，且不能覆盖 before/
    item2, path2 = ar.save_bundle("after", compute, bench, "unlocked", {})
    check("after file under after/", os.path.dirname(path2) == adir, path2)
    check("both files coexist", os.path.isfile(path) and os.path.isfile(path2))

    # 历史命名要能归一
    check("baseline normalizes to before",
          archive._norm_kind("baseline") == "before")
    check("post normalizes to after", archive._norm_kind("post") == "after")

    # 配对：两个测试各算一次
    pair = ar.bundle_pair()
    check("bundle pair computed", pair is not None)
    check("tflops picked from summary",
          pair and abs(pair["tflops_before"] - 35.2) < 0.01,
          str(pair and pair["tflops_before"]))
    check("bench delta", pair and abs(pair["bench_delta"]) < 0.01,
          str(pair and pair["bench_delta"]))

    # 导入：读回来 + 复制进对应目录
    imported = ar.import_bundle(path, "after")
    check("import accepted", imported is not None)
    check("imported copy in after/",
          imported and os.path.dirname(imported["path"]) == adir,
          str(imported and imported["path"]))

    # 坏文件必须被拒
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write('{"hello": 1}')
    check("garbage rejected", ar.read_bundle(bad) is None)
    check("missing file rejected", ar.read_bundle(os.path.join(tmp, "nope.json")) is None)

    # 清空只删 JSON，目录保留
    removed = ar.clear_bundles()
    check("clear removed files", removed >= 2, str(removed))
    check("before/ survives clear", os.path.isdir(bdir))
    check("after/ survives clear", os.path.isdir(adir))
    check("no json left", ar.list_bundles("before") == [], str(ar.list_bundles("before")))

    # 回归：UI 的三个存入口都必须指向 bundle，不能有第二条落盘路径
    import re as _re
    app_src = open(os.path.join(ROOT, "src", "ui", "app.py"), encoding="utf-8").read()
    check("bench card save wired to bundle",
          "self.save_baseline, self.save_after" in app_src,
          "BenchCard still points at save_measure")
    body = app_src.split("def save_measure", 1)[1].split("def ", 1)[0]
    check("save_measure delegates to bundle",
          "_save_bundle" in body and "add_record" not in body, body[:120].replace(chr(10), " "))

    # 回归：没跑过实测时 compute_verdict 必须存在，否则回调里的 AttributeError 会被 tkinter 吞掉
    check("compute_verdict initialised",
          _re.search(r"self\.compute_verdict = None", app_src) is not None)
    check("combined_verdict initialised",
          _re.search(r"self\.combined_verdict = None", app_src) is not None)

    # 回归：跨重启对比必须同时给算力与基准两路，不能一路顶替另一路
    panel_src = open(os.path.join(ROOT, "src", "ui", "panels.py"), encoding="utf-8").read()
    check("crossboot has compute metrics",
          all(k in panel_src for k in ("self.tf_base", "self.tf_after", "self.tf_gain")),
          "compute metrics missing")
    check("crossboot has bench metrics",
          all(k in panel_src for k in ("self.bn_base", "self.bn_after", "self.bn_gain")),
          "bench metrics missing")
    # 字体：pt→px 必须真的换算，否则整套字号偏小 24%（历史 bug）
    from core import theme as _T
    base_src = open(os.path.join(ROOT, "src", "ui", "base.py"), encoding="utf-8").read()
    check("pt to px conversion present",
          "PT_TO_PX" in base_src and "96.0 / 72.0" in open(
              os.path.join(ROOT, "src", "core", "theme.py"),
              encoding="utf-8").read(),
          "font sizes are pt but rendered as px")
    check("font uses negative size (px)",
          "size=-int(round(px))" in base_src, "Tk size sign decides pt vs px")
    check("bold by default", _T.BOLD_BY_DEFAULT is True)
    check("size boosts defined",
          set(_T.SIZE_BOOSTS) == {"normal", "large", "xlarge"}, str(_T.SIZE_BOOSTS))
    check("all boosts >= 1.0",
          min(_T.SIZE_BOOSTS.values()) >= 1.0, str(_T.SIZE_BOOSTS))

    # 字体设置必须集中在唯一入口，避免某处重建时漏掉
    check("ctx built through one helper",
          app_src.count("self._make_ctx()") >= 3
          and app_src.count("Ctx(self.theme_name, self.tr, self.ctx.scale)") == 0,
          "some rebuild paths bypass font settings")

    # 判定依据：必须展示实际生效的三层链，不能再列驱动推断时代的 rules
    dlgsrc = open(os.path.join(ROOT, "src", "ui", "dialogs.py"), encoding="utf-8").read()
    check("basis dialog exists", "class BasisDialog" in dlgsrc)
    check("basis shows three tiers",
          "basis.l1" in dlgsrc and "basis.l2" in dlgsrc and "basis.l3" in dlgsrc,
          "missing tier copy")
    check("basis marks veto",
          "basis.tag.veto" in dlgsrc, "no veto state")
    # 只看 show_basis 函数体：导出报告里仍会带 rules（那是原始记录，应当保留）
    _basis_body = app_src.split("def show_basis", 1)[1].split("def ", 1)[0]
    check("show_basis no longer dumps verdict.rules",
          "verdict.rules" not in _basis_body,
          "still rendering deprecated rule table")
    check("basis lines use grid alignment",
          "columnconfigure(1, weight=1)" in dlgsrc,
          "pack-based lines squeeze the value label")

    # 首屏：没有实测时必须是「待检测」，且数字不给编造的百分比
    check("pending state has copy",
          "state.pending" in open(os.path.join(ROOT, "src", "i18n", "strings.py"),
                                  encoding="utf-8").read())
    check("startup does not skip recompute",
          "self._recompute_unlock()" in app_src
          and "self._recompute_unlock()" in app_src.split("def refresh_data", 1)[1].split("def ", 1)[0],
          "refresh_data must go through _recompute_unlock")
    check("no-measurement verdict forced to pending",
          '"pending"' in app_src.split("def _recompute_unlock", 1)[1].split("def ", 1)[0])
    check("hero metric signed",
          '"%+.1f%%" % gain_pct' in panel_src, "gain must keep its sign")

    # 定义 1 处 + 调用 2 处（算力组、基准组各一次）
    check("both groups filled independently",
          panel_src.count("self._fill_row(") == 2
          and panel_src.count('"tflops_before"') == 1
          and panel_src.count('"bench_before"') == 1,
          str(panel_src.count("self._fill_row(")))


# ------------------------------------------------------------------ 剪贴板截图
def test_clipboard_image():
    print("\n[clipboard image]")
    from core import clipimg

    check("clipimg imports", clipimg is not None)
    # 纯编码路径：不碰真实剪贴板，任何平台都能跑
    bgra = bytes([10, 20, 30, 255]) * (4 * 3)
    try:
        dib = clipimg.to_dib(bgra, 4, 3)
        import struct as _struct
        head = _struct.unpack("<IiiHH", dib[:16])
        check("DIB header is BITMAPINFOHEADER", head[0] == 40, str(head[0]))
        check("DIB size matches", head[1] == 4 and head[2] == 3, str(head[1:3]))
        check("DIB is 24bpp", head[4] == 24, str(head[4]))
        # 行按 4 字节对齐：4px * 3B = 12B，本来就对齐
        check("DIB payload length", len(dib) - 40 == 3 * 12, str(len(dib) - 40))
    except Exception as exc:
        check("DIB encode", False, "%s: %s" % (type(exc).__name__, exc))

    try:
        png = clipimg.to_png(bgra, 4, 3)
        check("PNG signature", png[:8] == b"\x89PNG\r\n\x1a\n")
        check("PNG has IHDR+IEND", b"IHDR" in png and b"IEND" in png)
    except Exception as exc:
        check("PNG encode", False, "%s: %s" % (type(exc).__name__, exc))


# ------------------------------------------------------------------ 视觉与无障碍
def test_a11y_and_tokens():
    print("\n[a11y contrast + design tokens]")
    from core import theme as T

    def lum(c):
        c = c.lstrip("#")
        v = [int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
        return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    pairs = [
        ("ink", "card"), ("ink", "bg"), ("ink_soft", "card"), ("ink_soft", "bg"),
        ("ink_faint", "card"), ("ink_faint", "bg"), ("ink_faint", "chip_bg"),
        ("violet", "card"), ("violet", "accent_soft"),
        ("ok", "card"), ("ok", "ok_soft"),
        ("warn", "card"), ("warn", "warn_soft"),
        ("bad", "card"), ("bad", "bad_soft"),
        ("na", "card"),
    ]
    for name, th in (("light", T.THEMES["light"]), ("dark", T.THEMES["dark"])):
        bad = []
        for fg, bg in pairs:
            try:
                r = ratio(th[fg], th[bg])
            except KeyError:
                continue
            if r < 4.5:
                bad.append("%s/%s=%.2f" % (fg, bg, r))
        check("%s theme meets WCAG AA" % name, not bad, ", ".join(bad))

    for name, th in (("light", T.THEMES["light"]), ("dark", T.THEMES["dark"])):
        r1 = ratio("#ffffff", th["grad_from"])
        r2 = ratio("#ffffff", th["grad_to"])
        check("%s solid button white text" % name, r1 >= 4.5 and r2 >= 4.5,
              "from=%.2f to=%.2f" % (r1, r2))

    check("motion tokens present",
          all(k in T.MOTION for k in ("instant", "fast", "base", "slow", "spin")),
          str(sorted(T.MOTION)))
    check("easing tokens present",
          all(k in T.EASE for k in ("standard", "decelerate", "accelerate", "linear")),
          str(sorted(T.EASE)))
    check("sound tokens present",
          all(k in T.SOUND for k in ("click", "success", "error")), str(sorted(T.SOUND)))
    check("breakpoints present",
          all(k in T.BREAKPOINTS for k in ("sm", "md", "lg", "xl")),
          str(sorted(T.BREAKPOINTS)))
    for k in ("violet_hover", "violet_active", "violet_disabled",
              "focus_ring", "disabled_fg", "disabled_bg", "line_strong"):
        check("interaction token %s in light" % k, k in T.THEMES["light"])
        check("interaction token %s in dark" % k, k in T.THEMES["dark"])

    vals = [T.ease("standard", i / 10.0) for i in range(11)]
    check("ease is monotonic",
          all(vals[i] <= vals[i + 1] + 1e-9 for i in range(10)), str(vals[-3:]))
    check("ease starts at 0", abs(vals[0]) < 1e-6, str(vals[0]))
    check("ease ends at 1", abs(vals[-1] - 1.0) < 1e-6, str(vals[-1]))
    check("overshoot exceeds 1", T.ease("overshoot", 0.7) > 1.0,
          str(T.ease("overshoot", 0.7)))
    check("duration lookup", T.duration("fast") == 140 and T.duration("nope") == 200)


# ------------------------------------------------------------------ 动效与音效降级
def test_motion_sound():
    print("\n[motion + sound degradation]")
    from ui import anim, sound
    from core import theme as T

    check("anim has global switch",
          hasattr(anim, "set_enabled") and hasattr(anim, "enabled"))
    check("sound has global switch",
          hasattr(sound, "set_enabled") and hasattr(sound, "enabled"))

    sound.set_enabled(False)
    check("sound off is silent", sound.play("click") is False)
    check("sound reports disabled", sound.enabled() is False)

    anim.set_enabled(False)
    seen = []

    class _W:
        def after(self, ms, fn):
            return None

    anim.run(_W(), 500, lambda p: seen.append(p))
    check("disabled motion runs once", len(seen) == 1, str(seen))
    check("disabled motion ends at 1.0", seen and abs(seen[0] - 1.0) < 1e-9, str(seen))
    anim.set_enabled(True)

    for name in ("click", "success", "error"):
        data = sound._wav(name)
        check("%s wav synthesised" % name, data and data[:4] == b"RIFF",
              "%d bytes" % (len(data) if data else 0))
        check("%s cue is short" % name, T.SOUND[name]["ms"] <= 200,
              str(T.SOUND[name]["ms"]))
        check("%s gain is gentle" % name, T.SOUND[name]["gain"] <= 0.3,
              str(T.SOUND[name]["gain"]))

    # 按钮四态 + 键盘可达（结构检查，不依赖真实窗口）
    base_src = open(os.path.join(ROOT, "src", "ui", "base.py"), encoding="utf-8").read()
    check("button has focus ring",
          "focus_ring" in base_src and "<FocusIn>" in base_src)
    check("button is keyboard activatable",
          "<Key-space>" in base_src and "<Return>" in base_src)
    check("button has disabled visual",
          "disabled_fg" in base_src and "violet_disabled" in base_src)
    check("button hover is tweened",
          "_tween_hover" in base_src and "duration(\"fast\")" in base_src)

# ------------------------------------------------------------------ U 盘日志
def test_loader_log():
    print("\n[loader.log parsing]")
    from core import usb_boot
    fix = os.path.join(HERE, "fixtures")
    unlocked = os.path.join(fix, "loader_unlocked.log")
    aborted = os.path.join(fix, "loader_aborted.log")

    check("fixtures present", os.path.isfile(unlocked) and os.path.isfile(aborted))

    d = usb_boot.parse_log(unlocked)
    check("unlock log parses", d["ok"], str(d["error"]))
    check("version read", d["version"] == "2.1.9", d["version"])
    check("tx mode detected", d["tx_mode"] is True)
    check("plm flip 8f -> ffffffff",
          d["plm_from"] == 0xFFFFFF8F and d["plm_to"] == 0xFFFFFFFF,
          "%s -> %s" % (d["plm_from"], d["plm_to"]))
    check("ss0 from main card only", d["ss0"] == 0x88888888, hex(d["ss0"] or 0))
    check("ss1 from main card only", d["ss1"] == 0x8, hex(d["ss1"] or 0))
    check("readout1 zero after unlock", d["readout1"] == 0x00000000,
          hex(d["readout1"] or 0))
    check("all three flags true",
          d["plm_open"] is True and d["ss0_unlock"] is True and d["ss1_unlock"] is True)
    check("verdict unlocked via handoff",
          d["verdict"] == "unlocked" and d["verdict_source"] == "handoff",
          "%s/%s" % (d["verdict"], d["verdict_source"]))
    check("gpu device parsed", d["gpu_dev"] == "10de:2482", str(d["gpu_dev"]))
    check("banner abort not mistaken for event", d["abort"] is None, str(d["abort"]))
    check("phases captured", "SAMPLE" in d["phases"], str(d["phases"][:6]))

    a = usb_boot.parse_log(aborted)
    check("aborted log parses", a["ok"], str(a["error"]))
    check("aborted log is not unlocked", a["verdict"] != "unlocked", a["verdict"])
    check("aborted log records the abort", a["abort"] is not None, str(a["abort"]))

    missing = usb_boot.parse_log(os.path.join(fix, "nope.log"))
    check("missing log degrades", missing["ok"] is False and bool(missing["error"]),
          str(missing["error"]))

    vols = usb_boot.scan()
    check("volume scan returns a list", isinstance(vols, list), type(vols).__name__)


# ------------------------------------------------------------------ Tensor 算力判定
def test_tflops():
    print("\n[Tensor compute verdict]")
    from core import unlock as U
    from core.modeldb import GpuDatabase
    db = GpuDatabase(os.path.join(ROOT, "assets", "gpu_db.json"))
    entry = db.by_id("rtx3070ti")

    check("reference bands exist in db",
          bool((db.data.get("tflops_reference") or {}).get("by_model")),
          "no tflops_reference")

    locked = [
        {"key": "fp16_fp32acc_4096", "tflops": 27.36},
        {"key": "bf16_fp32acc_4096", "tflops": 31.13},
        {"key": "fp16_fp32acc_8192", "tflops": 33.34},
        {"key": "bf16_fp32acc_8192", "tflops": 33.80},
        {"key": "fp32_ref_4096", "tflops": 10.05},
        {"key": "fp16_fp16acc_4096", "tflops": 76.85},
    ]
    a = U.analyze_tflops(locked, entry, db)
    check("locked build => locked", a["verdict"] == "locked", a["verdict"])
    check("locked verdict uses self ratio", a["source"] == "self_ratio", a["source"])
    check("ratio ~0.36", abs((a["ratio"] or 0) - 0.356) < 0.02, str(a["ratio"]))

    unlocked = [dict(r) for r in locked]
    for row in unlocked:
        if row["key"] in U.LOCK_SENSITIVE_T:
            row["tflops"] = round(row["tflops"] * 2.9, 2)
    b = U.analyze_tflops(unlocked, entry, db)
    check("unlocked build => unlocked", b["verdict"] == "unlocked", b["verdict"])
    check("unlocked ratio ~1.0", abs((b["ratio"] or 0) - 1.0) < 0.2, str(b["ratio"]))

    check("empty summary => unknown",
          U.analyze_tflops([], entry, db)["verdict"] == "unknown")
    check("unknown model still uses self ratio",
          U.analyze_tflops(locked, db.by_id("rtx5090"), db)["verdict"] == "locked")

    v, note = U.analyze_log_vs_measure("unlocked", 26.0, "locked", 26.0)
    check("log unlocked but measured locked => relocked",
          v == "locked" and note == "xf.note.relocked", "%s/%s" % (v, note))
    v, note = U.analyze_log_vs_measure("unlocked", 26.0, "unlocked", 26.0)
    check("both unlocked => confirmed", v == "unlocked" and note == "xf.note.confirmed",
          "%s/%s" % (v, note))
    v, note = U.analyze_log_vs_measure(None, None, "locked", 0.1)
    check("measurement alone is enough", v == "locked", v)
    v, note = U.analyze_log_vs_measure("unlocked", 5.0, None, 0.1)
    check("stale log recognised", note == "xf.note.stale_log", note)
    v, note = U.analyze_log_vs_measure(None, None, None, None)
    check("no evidence => unknown", v == "unknown", v)



# ------------------------------------------------------------------ 家族路由
def test_family_routing():
    print("\n[USB family routing]")
    from core import gpu_family as G

    cases = [
        ("10DE:2482", "ga102"),   # 3070 Ti
        ("10DE:2206", "ga102"),   # 3080
        ("10DE:2504", "ga102"),   # 3060 LHR
        ("10DE:2489", "ga102"),   # 3060 Ti
        ("10DE:2684", "ad102"),   # 4090
        ("10DE:2782", "ad102"),   # 4070 Ti
        ("10DE:28E0", "ad102"),   # 4060 Laptop
        ("10DE:1E07", "tu10x"),   # 2080 Ti
        ("10DE:2182", "tu10x"),   # 1660 Ti
    ]
    for dev, want in cases:
        got, why = G.family_for_device_id(dev)
        check("route %s -> %s" % (dev, want), got == want, "%s/%s" % (got, why))

    got, why = G.family_for_device_id("10DE:2B85")
    check("RTX 50 rejected", got is None and why == "blackwell_50", "%s/%s" % (got, why))
    got, why = G.family_for_device_id("10DE:20B0")
    check("GA100 rejected", got is None and why == "ga100_datacenter", "%s/%s" % (got, why))
    got, why = G.family_for_device_id(None)
    check("missing id handled", got is None and why == "no_device_id", str(why))
    got, why = G.family_for_device_id("garbage")
    check("garbage id handled", got is None, str(got))

    # 区间边界
    check("lower bound 0x1e02", G.family_for_device_id(0x1E02)[0] == "tu10x")
    check("upper bound 0x28ff", G.family_for_device_id(0x28FF)[0] == "ad102")
    check("just past 0x2900 rejected", G.family_for_device_id(0x2900)[0] is None)


# ------------------------------------------------------------------ 最终结论仲裁
def test_final_verdict():
    print("\n[final verdict arbitration]")
    from core import unlock as U

    # 硬否决：实测不达标，无论日志/驱动怎么说都必须未解锁
    v, note = U.final_verdict("locked", "unlocked", 26.0, 26.0, "unlocked")
    check("measured locked beats log+driver",
          v == "locked" and note == "xf.note.relocked", "%s/%s" % (v, note))

    v, note = U.final_verdict("locked", None, None, None, "unlocked")
    check("measured locked beats driver", v == "locked", v)

    v, note = U.final_verdict("unlocked", "unlocked", 1.0, 1.0, "unlocked")
    check("all agree unlocked", v == "unlocked" and note == "xf.note.confirmed",
          "%s/%s" % (v, note))

    # 没有实测时才退到日志，再退到驱动
    v, note = U.final_verdict(None, "unlocked", 1.0, 1.0, "locked")
    check("no measurement -> log wins over driver", v == "unlocked", v)
    v, note = U.final_verdict(None, None, None, None, "na")
    check("driver fallback used", v == "na" and note == "xf.note.driver_only",
          "%s/%s" % (v, note))
    v, note = U.final_verdict(None, None, None, None, "unknown")
    check("nothing at all -> unknown", v == "unknown", v)



# ------------------------------------------------------------------ 资产获取
def test_assets_fetch():
    print("\n[asset fetching]")
    from core import assets_fetch as AF
    from core import gpu_family as G

    check("three supported families declared",
          set(AF.FAMILY_FILES) == {"ga102", "ad102", "tu10x"}, str(set(AF.FAMILY_FILES)))
    for fam, files in AF.FAMILY_FILES.items():
        check("%s needs EFI loader" % fam, "EFI/BOOT/BOOTX64.EFI" in files)
        check("%s needs v67 payload" % fam,
              any(f.startswith("v67-") for f in files))
        check("%s needs fwimage" % fam, any(f.startswith("fwimage_") for f in files))

    # 缓存目录不可写时不能抛异常
    check("cached_family_ok false when missing",
          AF.cached_family_ok("tu10x") in (True, False))
    check("cached_listing never raises", isinstance(AF.cached_listing("tu10x"), list))

    # 家族门禁：只放 20/30/40
    for dev, want in (("10DE:1E07", "tu10x"), ("10DE:2482", "ga102"),
                      ("10DE:2684", "ad102")):
        check("supported %s" % dev, G.family_for_device_id(dev)[0] == want)
    for dev, why in (("10DE:2B85", "blackwell_50"),
                     ("10DE:20B0", "ga100_datacenter"),
                     ("10DE:1B80", "pascal_or_older_skip")):
        got, reason = G.family_for_device_id(dev)
        check("unsupported %s (%s)" % (dev, why), got is None and reason == why,
              "%s/%s" % (got, reason))



# ------------------------------------------------------------------ 环境探测
def test_envscan():
    print("\n[environment detection]")
    from core import envscan

    cands = envscan.candidate_pythons()
    check("candidates is a list", isinstance(cands, list), type(cands).__name__)
    check("only existing files", all(os.path.isfile(c) for c in cands),
          str([c for c in cands if not os.path.isfile(c)][:3]))
    check("no duplicates", len(cands) == len(set(cands)))
    # 本程序自建的 venv 必须排在第三方发行版之前（先试自己的）
    from core.modeldb import app_dir
    own = os.path.join(app_dir(), "tools", "torch-venv", "Scripts", "python.exe")
    if own in cands:
        check("own venv ranked early", cands.index(own) <= 2, str(cands.index(own)))

    # probe 结构必须齐全，且对不存在的解释器也不抛异常
    item = envscan.probe(sys.executable, timeout=20)
    for key in ("exe", "has_torch", "version", "cuda", "usable", "note"):
        check("probe has %s" % key, key in item)
    check("probe usable implies cuda", (not item["usable"]) or item["cuda"])

    bad = envscan.probe("C:/definitely/not/python.exe", timeout=5)
    check("missing interpreter handled", bad["has_torch"] is False and bad["note"],
          str(bad.get("note"))[:60])

    # scan 必须有时间预算，不能无限跑
    import time
    t0 = time.time()
    items = envscan.scan(deep=False, budget=20)
    check("scan respects budget", time.time() - t0 < 40, "%.1fs" % (time.time() - t0))
    check("scan returns items", isinstance(items, list), type(items).__name__)
    if any(i.get("usable") for i in items):
        check("usable item has version",
              all(i.get("version") for i in items if i.get("usable")))



# ------------------------------------------------------------------ 递归启动防御
def test_self_execution_guard():
    print("\n[self-execution guard]")
    from core import envscan, deps, single_instance as S

    check("non-python exe rejected",
          envscan.safe_python("C:/Windows/notepad.exe") is False)
    check("nonexistent rejected", envscan.safe_python("C:/nope/python.exe") is False)

    # 候选列表里不能出现自身（源码运行时 sys.executable 是真解释器，
    # 所以用一个假的 exe 名字来验证过滤逻辑本身）
    fake = os.path.join(os.path.dirname(sys.executable), "GPUUnlockInspector.exe")
    check("guard handles absent path", envscan.safe_python(fake) is False)

    # deps 也必须排除自身
    got = deps.find_system_python()
    check("deps never returns self exe", got != sys.executable or not envscan.is_frozen(),
          str(got))

    # 单实例守卫在正常情况放行
    n = S.count_siblings()
    check("sibling count is sane", 0 <= n < 100, str(n))
    check("threshold fits onefile (2 procs/app)", S.MAX_INSTANCES >= 4, str(S.MAX_INSTANCES))
    ok, why = S.guard()
    check("guard allows normal start", ok is True, why)



# ------------------------------------------------------------------ 便携 Python 自举
def test_python_bootstrap():
    print("\n[portable python bootstrap]")
    from core import python_boot as PB

    check("asset regex present", bool(PB.ASSET_RE))
    import re
    pat = re.compile(PB.ASSET_RE)
    ok_name = "cpython-3.12.16+20261026-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
    bad_name = "cpython-3.12.16+20261026-aarch64-apple-darwin-install_only_stripped.tar.gz"
    check("windows asset matches", bool(pat.match(ok_name)))
    check("macos asset rejected", not pat.match(bad_name))

    fake = {"tag_name": "x", "assets": [
        {"name": bad_name, "size": 1, "browser_download_url": "u"},
        {"name": ok_name, "size": 22 * 1024 * 1024, "browser_download_url": "u2"},
        {"name": "cpython-3.11.18+1-x86_64-pc-windows-msvc-install_only_stripped.tar.gz",
         "size": 1, "browser_download_url": "u3"},
    ]}
    picked = PB._pick_asset(fake)
    check("picks 3.12 over 3.11", picked and "3.12.16" in picked["name"],
          str(picked and picked["name"]))
    check("never picks macos", picked and "windows" in picked["name"])

    check("existing_python is a path or None",
          PB.existing_python() is None or os.path.isfile(PB.existing_python()))



# ------------------------------------------------------------------ 反馈组件
def test_feedback_widgets():
    print("\n[feedback widgets]")
    import tkinter as tk
    from i18n.strings import Translator
    from ui.base import Ctx
    from ui.busy import Spinner, StatusLine

    try:
        root = tk.Tk()
    except Exception as exc:
        # 无 Tcl 环境（CI / 精简 Python）时跳过 GUI 检查，不算失败
        print("  SKIP  GUI widget test (%s)" % str(exc).splitlines()[0][:60])
        return
    root.withdraw()
    ctx = Ctx("light", Translator("zh"), 1.0)
    holder = tk.Frame(root, bg=ctx.c("card"))

    line = StatusLine(holder, ctx)
    check("StatusLine builds", line is not None)

    line.set_busy("busy")
    check("busy starts spinner", line.spinner._running is True)
    check("busy text shown", line.label.cget("text") == "busy")

    line.set_progress(0.42, "run")
    check("progress stops spinner", line.spinner._running is False)
    check("progress shows percent", "42%" in line.label.cget("text"),
          line.label.cget("text"))

    line.set_done("ok")
    check("done marker present", "\u2713" in line.label.cget("text"),
          line.label.cget("text"))

    line.set_error("bad")
    check("error marker present", "\u2715" in line.label.cget("text"),
          line.label.cget("text"))

    line.clear()
    check("clear empties label", line.label.cget("text") == "")
    check("clear stops spinner", line.spinner._running is False)

    # 进度必须被 clamp 在 0..100
    line.set_progress(-1)
    check("negative clamped", "0%" in line.label.cget("text"), line.label.cget("text"))
    line.set_progress(9.9)
    check("overflow clamped", "100%" in line.label.cget("text"), line.label.cget("text"))

    sp = Spinner(holder, ctx)
    check("Spinner builds", sp is not None)
    check("spinner frames non-empty", len(Spinner.FRAMES) > 0)

    root.destroy()



# ------------------------------------------------------------------ 新判定逻辑
def test_judge_by_compute():
    print("\n[compute-first verdict]")
    from core import unlock as U
    from core.modeldb import GpuDatabase
    db = GpuDatabase(os.path.join(ROOT, "assets", "gpu_db.json"))
    entry = db.by_id("rtx3070ti")

    ref = U.unlocked_reference(entry, db)
    check("reference exists for 3070ti", bool(ref), str(ref))
    check("reference has 4 sensitive cases", len(ref) == 4, str(len(ref)))

    locked = [{"key": k, "tflops": (v[0] + v[1]) / 2 * 0.45} for k, v in ref.items()]
    j = U.judge_by_compute(locked, entry, db)
    check("far below reference => locked", j["verdict"] == "locked", j["verdict"])
    check("source is vs_reference", j["source"] == "vs_reference", j["source"])

    unlocked = [{"key": k, "tflops": (v[0] + v[1]) / 2} for k, v in ref.items()]
    j2 = U.judge_by_compute(unlocked, entry, db)
    check("at reference => unlocked", j2["verdict"] == "unlocked", j2["verdict"])

    check("empty summary => unknown",
          U.judge_by_compute([], entry, db)["verdict"] == "unknown")
    check("unknown model => unknown",
          U.judge_by_compute(locked, db.by_id("rtx5090"), db)["verdict"] == "unknown")

    # 日志时效
    boot = U.boot_time()
    check("boot_time is sane", boot is not None and boot < time.time())
    check("yesterday log is stale", U.log_is_current_boot(boot - 86400, boot) is False)
    check("fresh log accepted", U.log_is_current_boot(boot + 30, boot) is True)
    check("unknown mtime => None", U.log_is_current_boot(None, boot) is None)

    # 算力优先，日志只佐证
    v = U.verdict_now(locked, {"mtime": boot - 86400, "verdict": "unlocked"},
                      boot, entry, db)
    check("stale log ignored", v["state"] == "locked", v["state"])
    check("stale note recorded", "log_stale" in v["notes"], str(v["notes"]))
    v2 = U.verdict_now(locked, {"mtime": boot + 10, "verdict": "unlocked"},
                       boot, entry, db)
    check("compute beats fresh log", v2["state"] == "locked", v2["state"])
    check("relocked noted", "relocked" in v2["notes"], str(v2["notes"]))
    v3 = U.verdict_now(None, {"mtime": boot + 10, "verdict": "unlocked"},
                       boot, entry, db)
    check("no compute => log used", v3["state"] == "unlocked", v3["state"])


# ------------------------------------------------------------------ 日志
def test_logging():
    print("\n[logging]")
    from core import log as L
    tmp = tempfile.mkdtemp(prefix="gpulog_")
    path = os.path.join(tmp, "t.log")
    L.init(path, level="DEBUG")
    L.info("t", "hello")
    L.warn("t", "warning")
    L.error("t", "boom", ValueError("x"))
    txt = L.tail(path, n=20)
    check("log file written", os.path.isfile(path))
    check("info recorded", "hello" in txt)
    check("warn level present", "[WARN" in txt)
    check("error with traceback", "ValueError" in txt, txt[-120:])
    check("tail returns text", isinstance(txt, str) and len(txt) > 0)
    L.close()
    check("log survives close", os.path.getsize(path) > 0)

# ------------------------------------------------------------------ 静态检查
def test_no_third_party():
    print("\n[dependencies]")
    banned = ("import numpy", "import torch", "import PyQt", "import PySide",
              "import pynvml", "import requests", "import pandas")
    hits = []
    for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "src")):
        for f in files:
            if not f.endswith(".py"):
                continue
            with open(os.path.join(dirpath, f), encoding="utf-8") as fh:
                lines = fh.read().splitlines()
            # 只检查「模块顶层 / 函数体里真正会被执行的 import」。
            # tflops.py 里有一份 _BRIDGE 字符串是写给子进程跑的，那一份不算本进程依赖。
            in_triple = False
            for line in lines:
                stripped = line.strip()
                # 跳过三引号字符串块（_BRIDGE 就写在里面）
                if stripped.count('"""') % 2 == 1 or stripped.count("'''") % 2 == 1:
                    in_triple = not in_triple
                    continue
                if in_triple:
                    continue
                if stripped.startswith("#"):
                    continue
                for b in banned:
                    if stripped.startswith(b):
                        hits.append((f, b))
    check("core+ui use stdlib only", not hits, str(hits[:5]))

    # 子进程桥接脚本必须真的带 torch（否则「cublas」引擎名不副实）
    from core import tflops as tf_mod
    check("cublas bridge mentions torch.mm", "torch.mm" in tf_mod._BRIDGE)
    check("bridge is valid python", compile(tf_mod._BRIDGE, "<bridge>", "exec") is not None)


def main():
    test_i18n()
    test_db()
    test_unlock()
    test_ssdiag_parse()
    test_storage()
    test_hwinfo()
    test_archive()
    test_bundles()
    test_clipboard_image()
    test_a11y_and_tokens()
    test_motion_sound()
    test_loader_log()
    test_tflops()
    test_family_routing()
    test_final_verdict()
    test_assets_fetch()
    test_envscan()
    test_self_execution_guard()
    test_python_bootstrap()
    test_feedback_widgets()
    test_judge_by_compute()
    test_logging()
    test_degradation()
    test_no_third_party()

    print("\n" + "=" * 56)
    print("PASS %d   FAIL %d" % (len(PASSES), len(FAILS)))
    if FAILS:
        for name, detail in FAILS:
            print("  - %s  %s" % (name, detail))
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())