# -*- coding: utf-8 -*-
"""解锁状态判定引擎。

结论只有四种：unlocked / locked / na / unknown。

判定不搞黑盒。每条规则都产出可复核记录：依据字段、实测值、阈值、来源、权重。
UI 会把这张表原样画出来，方便逐条核对。

═══════════════════════════════════════════════════════════════════
判定所依据的事实（可自行查证）
═══════════════════════════════════════════════════════════════════
1. LHR（Lite Hash Rate）是 NVIDIA 在 2021 年 5 月随 RTX 30 系改版引入的
   低算力限制器，通过在驱动层识别 Ethash 的内存访问特征来降速。
2. 2021 年 2 月发布的 RTX 3060 用 LHR v1，被 470.05 beta 驱动绕过；
   2021 年 5 月起的改版卡换用 LHR v2（设备 ID 后缀 2504/2505 等），
   对内存访问模式做更细的指纹识别，当时无公开软解。
3. **2022 年 5 月，NVIDIA 在 R515 驱动分支起移除了 LHR 限制器**，
   从 516.94（Windows WHQL）/ 515.65.01（Linux）起 LHR 卡不再被降速。
   参考：NVIDIA Data Center GPU Driver Release Notes 515 v2.0。
   这是本工具最重要的判定依据：驱动版本 >= 516.94 的 LHR 卡，实际就是已解锁。
4. LHR v2 的「软件解禁」需要**同时**刷改过的 VBIOS 和专用非公开驱动
   （参考 LHRv2Unlocker 项目）。因此「已解锁」在驱动维度等价于
   「正在使用非公开驱动」——公开渠道拿不到，可以硬判为未解锁。
5. 40 系（Ada）与 50 系（Blackwell）出厂即不含 LHR，结论为「不适用」。

因此判定优先级是：
   型号库说没限制器      → 不适用（na）
   驱动 >= 516.94        → 已解锁（unlocked），强依据
   驱动在 [since, 516.94) → 未解锁（locked），强依据，除非有基准证据反驳
   其余                  → 看频率/算力/功耗等弱证据投票，证据不足则无法判定
═══════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import re

STATE_UNLOCKED = "unlocked"
STATE_LOCKED = "locked"
STATE_NA = "na"
STATE_UNKNOWN = "unknown"

CONF_HIGH = "high"
CONF_MEDIUM = "medium"
CONF_LOW = "low"

# NVIDIA 在 R515 分支移除 LHR；516.94 是 Windows 上第一个确认完全无限制的 WHQL 版本
LHR_REMOVED_MAJOR = 516
LHR_REMOVED_MINOR = 94
LHR_REMOVED_TEXT = "516.94"


class Rule:
    """一条判定规则的可复核记录。"""

    __slots__ = ("key", "field", "actual", "threshold", "verdict", "weight", "source", "detail")

    def __init__(self, key, field=None, actual=None, threshold=None, verdict="skip",
                 weight=0.0, source="db", detail=None):
        self.key = key
        self.field = field
        self.actual = actual
        self.threshold = threshold
        self.verdict = verdict     # hit / miss / skip
        self.weight = weight       # >0 支持解锁，<0 支持锁定，0 只做记录
        self.source = source       # db / nvml / nvidia-smi / wmi / bench
        self.detail = detail

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


class Verdict:
    def __init__(self, state, confidence, rules, reason_key, entry=None, matched_by=None):
        self.state = state
        self.confidence = confidence
        self.rules = rules
        self.reason_key = reason_key
        self.entry = entry
        self.matched_by = matched_by

    @property
    def is_unknown(self):
        return self.state == STATE_UNKNOWN

    def score(self):
        return sum(r.weight for r in self.rules if r.verdict != "skip")


def parse_driver(text):
    """'617.14' → (617, 14)；'32.0.16.1714'（WMI 内部号）→ 近似换算；失败返回 None。"""
    if not text:
        return None
    s = str(text).strip()
    m = re.match(r"^(\d+)\.(\d+)", s)
    if not m:
        return None
    major, minor = int(m.group(1)), int(m.group(2))
    if major == 32 and minor == 0:
        m2 = re.match(r"^32\.0\.(\d+)\.(\d+)", s)
        if not m2:
            return None
        a, b = int(m2.group(1)), int(m2.group(2))
        # 32.0.15.xxxx ≈ 5xx.xx ；32.0.16.xxxx ≈ 6xx.xx
        pub_major = 400 + (a - 13) * 100 + b // 100
        return (pub_major, b % 100)
    return (major, minor)


def _ver(a, b):
    return a >= b if a and b else None


def evaluate(snapshot, db, manual_gpu_id=None, bench=None, deep=None):
    """返回 Verdict。绝不抛异常，数据不足时返回 unknown。

    deep: 可选的 core.ssdiag.DeepProbe 结果。传入时会在同一套规则表里
          追加「深度诊断」证据，并把最终结论从「LHR 维度」扩展到同时覆盖
          「Tensor/PLM 维度」——两者都解锁才算 unlocked。
    """
    rules = []

    if snapshot is None or not snapshot.gpus:
        return Verdict(STATE_UNKNOWN, CONF_LOW, rules, "unlock.reason.no_gpu")

    gpu = snapshot.primary

    # ---------------------------------------------------------------- 型号匹配
    entry = None
    matched_by = None
    if manual_gpu_id:
        entry = db.by_id(manual_gpu_id)
        matched_by = "manual" if entry else None
    if entry is None:
        entry, matched_by = db.find(gpu.get("name"), gpu.get("mem_total_bytes"))

    if entry is None:
        return Verdict(STATE_UNKNOWN, CONF_LOW, rules, "unlock.not_in_db.hint")

    lhr = entry.get("lhr") or {}
    lhr_type = (lhr.get("type") or "unknown").lower()

    has_realtime = bool(snapshot.nvml or snapshot.smi_available) or bool(gpu.get("clock_sm_mhz"))
    if not has_realtime:
        rules.append(Rule("rule.lhr_absent", "source", gpu.get("source"), "nvml|nvidia-smi",
                          "skip", 0.0, gpu.get("source") or "wmi"))
        return Verdict(STATE_UNKNOWN, CONF_LOW, rules, "unlock.reason.nvml_fail", entry, matched_by)

    # ---------------------------------------------------------------- 硬否决 1：无限制器
    # 注意：手动选择的型号不是本机真实身份，所以这条硬结论的置信度也要封顶。
    if lhr_type == "none":
        rules.append(Rule("rule.lhr_absent", "lhr.type", "none", "none", "hit", 0.0, "db"))
        conf = CONF_MEDIUM if matched_by == "manual" else CONF_HIGH
        return Verdict(STATE_NA, conf, rules, "unlock.reason.lhr_none", entry, matched_by)

    if lhr_type not in ("lhr_v1", "lhr_v2"):
        rules.append(Rule("rule.lhr_absent", "lhr.type", lhr_type, "lhr_v1|lhr_v2",
                          "skip", 0.0, "db"))
        return Verdict(STATE_UNKNOWN, CONF_LOW, rules, "unlock.reason.unknown_type",
                       entry, matched_by)

    # ---------------------------------------------------------------- 规则 1：驱动是否已移除 LHR
    drv_raw = gpu.get("driver_version")
    drv = parse_driver(drv_raw)
    removed = (LHR_REMOVED_MAJOR, LHR_REMOVED_MINOR)
    if drv is not None:
        if drv >= removed:
            rules.append(Rule("rule.driver_unlocked", "driver_version", drv_raw,
                              ">= " + LHR_REMOVED_TEXT, "hit", +5.0, "wmi"))
        else:
            rules.append(Rule("rule.driver_unlocked", "driver_version", drv_raw,
                              ">= " + LHR_REMOVED_TEXT, "miss", -5.0, "wmi"))
    else:
        rules.append(Rule("rule.driver_unlocked", "driver_version", drv_raw,
                          ">= " + LHR_REMOVED_TEXT, "skip", 0.0, "wmi"))

    # ---------------------------------------------------------------- 规则 2：上锁区间
    since = (lhr.get("since_driver") or "").strip()
    since_v = parse_driver(since) if since else None
    if drv is not None and since_v is not None:
        if drv < removed:
            if drv >= since_v:
                rules.append(Rule("rule.driver_stock", "driver_version", drv_raw,
                                  "[%s, %s)" % (since, LHR_REMOVED_TEXT), "hit", -3.0, "wmi"))
            else:
                rules.append(Rule("rule.driver_stock", "driver_version", drv_raw,
                                  "[%s, %s)" % (since, LHR_REMOVED_TEXT), "miss", +1.0, "wmi"))
        else:
            rules.append(Rule("rule.driver_stock", "driver_version", drv_raw,
                              "[%s, %s)" % (since, LHR_REMOVED_TEXT), "miss", +1.0, "wmi"))
    else:
        rules.append(Rule("rule.driver_stock", "driver_version", drv_raw, since or "n/a",
                          "skip", 0.0, "wmi"))

    # ---------------------------------------------------------------- 规则 3：PCI Device ID 佐证
    # 已知 LHR 版本的核心会用不同的 Device ID 后缀（见 gpu_db.json 的 lhr_ids）。
    # 这条只做「佐证」，权重为 0：它能确认「这是 LHR 卡」，但确认不了「有没有解锁」。
    dev_id = (gpu.get("pci_device_id") or "").upper()
    id_map = (db.data.get("lhr_ids") or {}).get("ids") or {}
    if dev_id:
        suffix = dev_id.split(":")[-1]
        if suffix in id_map:
            rules.append(Rule("rule.pci_lhr_id", "pci_device_id", dev_id,
                              "known LHR device id", "hit", 0.0, "nvml",
                              detail=id_map[suffix]))
        else:
            rules.append(Rule("rule.pci_lhr_id", "pci_device_id", dev_id,
                              "known LHR device id", "skip", 0.0, "nvml"))
    else:
        rules.append(Rule("rule.pci_lhr_id", "pci_device_id", None,
                          "known LHR device id", "skip", 0.0, "nvml"))

    # ---------------------------------------------------------------- 规则 4：显存频率核验
    # 型号库存的是原始时钟（如 GDDR6X 1188 MHz），NVML 报的是等效速率（MT/s），
    # GDDR6/6X/7 都是 ×8，所以两边换算后再比。
    ref_mem = (entry.get("clocks") or {}).get("mem_mhz")
    cur_mem = gpu.get("mem_clock_mhz")
    if ref_mem and cur_mem:
        ref_mts = ref_mem * 8.0
        ratio = cur_mem / ref_mts
        # 一致 = 这条证据干净（没超频/没降频污染），可以给解锁投 +1
        verdict = "hit" if 0.94 <= ratio <= 1.06 else "miss"
        rules.append(Rule("rule.mem_clock", "mem_clock_mhz", cur_mem,
                          "%d MT/s (±6%%)" % ref_mts, verdict,
                          +1.0 if verdict == "hit" else -1.0, "nvml",
                          detail="ratio=%.3f" % ratio))
    else:
        rules.append(Rule("rule.mem_clock", "mem_clock_mhz", cur_mem, ref_mem, "skip", 0.0, "nvml"))

    # ---------------------------------------------------------------- 规则 5：算力达成比
    # 只有「同一张卡、同一算法」的参考值才可比。型号库的 Ethash MH/s 与本项目
    # proxy_v1 的 MSol/s 量纲不同，不能直接相除，所以必须由基准自己带 reference_sol。
    bench_ref = (bench or {}).get("reference_sol")
    if bench and bench.get("avg") and bench_ref:
        pct = float(bench["avg"]) / float(bench_ref)
        verdict = "hit" if pct >= 0.88 else "miss"
        rules.append(Rule("rule.hash_pct", "bench.avg", round(bench["avg"], 2),
                          ">= 88%% of %.2f MSol/s (reference)" % bench_ref,
                          verdict, +4.0 if verdict == "hit" else -4.0, "bench",
                          detail="pct=%.1f%%" % (pct * 100)))
    else:
        rules.append(Rule("rule.hash_pct", "bench.avg",
                          round(bench["avg"], 2) if bench and bench.get("avg") else None,
                          "needs a same-algorithm reference run", "skip", 0.0, "bench"))

    # ---------------------------------------------------------------- 规则 6：功耗余量
    # proxy_v1 是内存延迟型负载，功耗天然跑不满 TDP，所以没有同机参考时这条只记录不投票。
    tdp = entry.get("tdp_w")
    ref_power = (bench or {}).get("reference_power")
    if bench and bench.get("power_avg") and ref_power:
        ratio = bench["power_avg"] / float(ref_power)
        verdict = "hit" if ratio >= 0.90 else "miss"
        rules.append(Rule("rule.power_headroom", "power_draw_w", round(bench["power_avg"], 1),
                          ">= 90%% of %.1f W (reference)" % ref_power,
                          verdict, +1.5 if verdict == "hit" else -1.5, "bench",
                          detail="ratio=%.2f" % ratio))
    else:
        rules.append(Rule("rule.power_headroom", "power_draw_w",
                          round(bench["power_avg"], 1) if bench and bench.get("power_avg") else None,
                          "%s W TDP" % (tdp or "?"), "skip", 0.0, "bench"))

    # ---------------------------------------------------------------- 规则 7：深度诊断（可选）
    # 只有在用户显式跑了 ssdiag 才有值。它读的是 FEAT SS0/SS1 寄存器，
    # 判定 Tensor/PLM 维度的解锁状态，与 LHR 是两个独立维度。
    if deep is not None and deep.ran:
        if deep.state == "unlocked":
            rules.append(Rule("rule.deep_feat", "FEAT.SS0/SS1",
                              "0x%08X/0x%X" % (deep.ss0 or 0, deep.ss1 or 0),
                              "0x88888888/0x8", "hit", +5.0, "ssdiag"))
        elif deep.state == "locked":
            rules.append(Rule("rule.deep_feat", "FEAT.SS0/SS1",
                              "0x%08X/0x%X" % (deep.ss0 or 0, deep.ss1 or 0),
                              "0x88888888/0x8", "miss", -5.0, "ssdiag"))
        else:
            rules.append(Rule("rule.deep_feat", "FEAT.SS0/SS1", "unparsed",
                              "0x88888888/0x8", "skip", 0.0, "ssdiag"))
    elif deep is not None:
        rules.append(Rule("rule.deep_feat", "FEAT.SS0/SS1",
                          deep.error or "not run", "0x88888888/0x8",
                          "skip", 0.0, "ssdiag"))

    # ---------------------------------------------------------------- 投票
    active = [r for r in rules if r.verdict != "skip"]
    if not active:
        return Verdict(STATE_UNKNOWN, CONF_LOW, rules, "unlock.reason.nvml_fail",
                       entry, matched_by)

    score = sum(r.weight for r in active)
    pos_strong = [r for r in active if r.weight >= 3.0]
    neg_strong = [r for r in active if r.weight <= -3.0]

    if pos_strong and not neg_strong and score >= 3.0:
        state = STATE_UNLOCKED
    elif neg_strong and not pos_strong and score <= -3.0:
        state = STATE_LOCKED
    elif pos_strong and neg_strong:
        # 强证据对撞：驱动说已解锁、算力却说没到 —— 不下结论，交给用户看依据
        state = STATE_UNKNOWN
    else:
        state = STATE_UNKNOWN

    # 置信度只看「独立证据条数 + 证据来源是否互相独立」，不因为规则多就涨。
    # 注意：驱动两条规则同源于 WMI 的同一个字段，算一条证据。
    independent = set()
    for r in active:
        if r.key in ("rule.driver_unlocked", "rule.driver_stock"):
            independent.add("driver")
        elif r.key == "rule.mem_clock":
            independent.add("mem")
        elif r.key == "rule.pci_lhr_id":
            independent.add("pci")
        elif r.key == "rule.hash_pct":
            independent.add("bench")
        elif r.key == "rule.power_headroom":
            independent.add("power")
        elif r.key == "rule.deep_feat":
            independent.add("feat")

    has_bench = bool(bench and bench.get("avg") and bench_ref)
    if state == STATE_UNKNOWN:
        confidence = CONF_LOW
    elif len(independent) >= 4 and has_bench and matched_by != "manual":
        confidence = CONF_HIGH
    elif len(independent) >= 2 and matched_by != "manual":
        confidence = CONF_MEDIUM
    else:
        confidence = CONF_LOW

    # 手动选的型号不是这台卡的真实身份，所以置信度必须封顶在「中」。
    # 原来的写法只在 == HIGH 时降级，但下面分支可能直接给出 MEDIUM，
    # 一旦某天分支条件变化就会漏降，所以这里按档位硬封顶。
    if matched_by == "manual":
        if confidence == CONF_HIGH:
            confidence = CONF_MEDIUM
        elif confidence == CONF_MEDIUM:
            # 保持 MEDIUM（已经是封顶值），不为 manual 再降
            pass

    return Verdict(state, confidence, rules, "unlock.reason.ok", entry, matched_by)


def gain_percent(entry, algo="ethash"):
    """型号库里默认/解锁的算力提升百分比。"""
    if not entry:
        return None
    hr = (entry.get("hashrate") or {}).get(algo) or {}
    locked, unlocked = hr.get("locked"), hr.get("unlocked")
    if locked is None or not unlocked:
        return None
    return (unlocked - locked) / float(unlocked) * 100.0


def reference_delta(entry, algo="ethash"):
    """返回 (locked, unlocked, delta, pct) 或 None。"""
    if not entry:
        return None
    hr = (entry.get("hashrate") or {}).get(algo) or {}
    locked, unlocked = hr.get("locked"), hr.get("unlocked")
    if unlocked is None:
        return None
    delta = (unlocked - locked) if locked is not None else None
    pct = gain_percent(entry, algo)
    return locked, unlocked, delta, pct

# ══════════════════════════════════════════════════════════════════
# Tensor 算力判定（TFLOPS）
# ══════════════════════════════════════════════════════════════════
def analyze_tflops(tsummary, entry, db):
    """把一次 TFLOPS 实测转成「锁定 / 解锁 / 无法判定」。

    两条独立判据，优先用自比较（不依赖任何区间数据）：

      A. **自比较比例**（最可靠）
         同一台机、同一驱动下，FP16+FP16acc 这条路径从未被锁，是天然的「全速标尺」。
         锁定卡的特征：FP16+FP32acc / FP16+FP16acc ≈ 0.35~0.45（半速再打折）；
         解锁后该比例回到 ≈ 1.0。
         实测依据（RTX 3070 Ti，驱动 617.14）：
           FP16+FP32acc = 27.4 TFLOPS，FP16+FP16acc = 76.9 TFLOPS → 比例 0.356，锁定态。

      B. **型号库区间比对**（有数据时更直观）
         型号库里记录了该卡锁定态 / 解锁态的实测区间，命中哪个就判哪个。

    两条都拿不到时返回 unknown，绝不猜。
    """
    out = {"verdict": "unknown", "source": "none", "ratio": None,
           "rows": [], "note_key": None}
    if not tsummary:
        return out

    by_key = {r["key"]: r for r in tsummary}

    def val(k):
        r = by_key.get(k) or {}
        v = r.get("tflops")
        return float(v) if v else None

    lock_vals = [val(k) for k in LOCK_SENSITIVE_T if val(k)]
    control = val("fp16_fp16acc_4096")

    # ---- A. 自比较
    ratio = None
    if lock_vals and control and control > 0:
        # 用最接近 4096 的那项做比较（同尺寸更可比）
        ratio = (val("fp16_fp32acc_4096") or lock_vals[0]) / control
        out["ratio"] = ratio
        fb = ((db.data.get("tflops_reference") or {}).get("fallback") or {})
        lo = fb.get("ratio_locked_max", 0.65)
        hi = fb.get("ratio_unlocked_min", 0.85)
        if ratio <= lo:
            out["verdict"], out["source"] = "locked", "self_ratio"
            out["note_key"] = "tf.note.self_locked"
        elif ratio >= hi:
            out["verdict"], out["source"] = "unlocked", "self_ratio"
            out["note_key"] = "tf.note.self_unlocked"
        else:
            out["note_key"] = "tf.note.self_ambiguous"

    # ---- B. 型号库区间
    ref = ((db.data.get("tflops_reference") or {}).get("by_model") or {})
    model_ref = ref.get((entry or {}).get("id")) if entry else None
    if model_ref:
        locked_hits = 0
        unlocked_hits = 0
        compared = 0
        for key, rng in model_ref.items():
            v = val(key)
            if v is None or not isinstance(rng, dict):
                continue
            for band, counter in (("locked", "l"), ("unlocked", "u")):
                span = rng.get(band)
                if not span or len(span) != 2:
                    continue
                compared += 1
                if span[0] <= v <= span[1]:
                    if band == "locked":
                        locked_hits += 1
                    else:
                        unlocked_hits += 1
                    break
        if compared and locked_hits + unlocked_hits:
            band_verdict = "locked" if locked_hits > unlocked_hits else (
                "unlocked" if unlocked_hits > locked_hits else "unknown")
            # 区间判据只在和自比较一致、或自比较拿不到时才采纳
            if out["verdict"] == "unknown" and band_verdict != "unknown":
                out["verdict"], out["source"] = band_verdict, "range"
                out["note_key"] = ("tf.note.range_locked" if band_verdict == "locked"
                                   else "tf.note.range_unlocked")
            elif out["verdict"] != band_verdict and band_verdict != "unknown":
                out["note_key"] = "tf.note.conflict"
        out["locked_hits"] = locked_hits
        out["unlocked_hits"] = unlocked_hits

    out["rows"] = tsummary
    return out


LOCK_SENSITIVE_T = ("fp16_fp32acc_4096", "bf16_fp32acc_4096",
                    "fp16_fp32acc_8192", "bf16_fp32acc_8192")

def analyze_log_vs_measure(log_verdict, log_age_h, tf_verdict, boot_age_h):
    """仲裁三路证据，定出**唯一**的最终结论。

    优先级（硬性，不可调换）：

      1. **实测算力**（tf_verdict）—— 唯一能反映「此刻真实状态」的证据。
         这是硬否决：只要实测不达标，无论日志怎么写、驱动多新，一律判「未解锁」。
         依据：钳制精确到「张量路径 × 累加模式」，FP16+FP16acc 这条路径从未被锁，
         它和 FP16+FP32acc 的比值直接反映当前是否被钳制（见 analyze_tflops）。
      2. **U 盘 loader.log** —— 只证明「引导那一刻成功了」，不是此刻的状态。
         值在 always-on 岛，FLR 后存活、断电即失；驱动 GC6 唤醒/bind 前还会重新推导回锁。
      3. 驱动版本推断（由 evaluate() 产出）—— 仅在没有前两者时兜底。

    真实场景（本机就踩到了）：
      昨天 09:04 引导解锁成功（日志 HANDOFF-OUT 全绿），Windows 09:04:39 起来；
      今天 10:47 实测 FP16+FP32acc 仍只有 27.4 TFLOPS、比例 0.356 —— 锁定态。

    返回 (verdict, note_key)。
    """
    # ---- 硬否决：实测优先
    if tf_verdict and tf_verdict != "unknown":
        if tf_verdict == "locked":
            if log_verdict == "unlocked":
                # 日志说解锁、实测说锁 —— 解锁没存活到当前会话
                return "locked", "xf.note.relocked"
            return "locked", "xf.note.measured_locked"
        if tf_verdict == "unlocked":
            if log_verdict == "unlocked":
                return "unlocked", "xf.note.confirmed"
            return "unlocked", "xf.note.measured_unlocked"

    # ---- 没有实测：只能看日志
    if log_verdict in ("unlocked", "locked"):
        if log_age_h is not None and boot_age_h is not None:
            # 日志早于本次开机超过 1 分钟，说明它描述的是上一次引导
            if log_age_h - boot_age_h > 1 / 60.0:
                return log_verdict, "xf.note.stale_log"
        return log_verdict, "xf.note.log_only"
    return "unknown", "xf.note.no_evidence"

def final_verdict(tf_verdict, log_verdict, log_age_h, boot_age_h, evaluated_state):
    """四路证据合成的最终结论。hero 与徽章都只认这一个。

    顺序（前面的一票否决后面）：
        实测 > 日志 > 驱动推断
    其中「实测不达标」是硬否决：- 无论日志/驱动怎么说，一律未解锁。
    """
    state, note = analyze_log_vs_measure(log_verdict, log_age_h, tf_verdict, boot_age_h)
    if note in ("xf.note.no_evidence",):
        # 没有实测也没有日志，退回驱动推断
        if evaluated_state and evaluated_state != "unknown":
            return evaluated_state, "xf.note.driver_only"
    return state, note

# ══════════════════════════════════════════════════════════════════
# 统一判定：算力为准，日志佐证
# ══════════════════════════════════════════════════════════════════
def boot_time():
    """本次开机时刻（epoch 秒）。用 GetTickCount64 反推，最可靠。"""
    try:
        import ctypes
        ms = ctypes.windll.kernel32.GetTickCount64()
        import time as _t
        return _t.time() - ms / 1000.0
    except Exception:
        return None


def log_is_current_boot(log_mtime, boot_ts, tolerance_s=120):
    """日志是否是本次开机之后写的。

    为什么必须校验：U 盘上的 loader.log 是**上一次**引导留下的，
    它只能证明「上次引导成功过」，不能证明「此刻解锁仍然生效」。
    实测踩过：日志写于昨天 09:04，今天开机后实测仍是锁定态。
    容差 120 秒用于吸收时钟偏差与写盘延迟。
    """
    if log_mtime is None or boot_ts is None:
        return None
    return log_mtime >= (boot_ts - tolerance_s)


def unlocked_reference(entry, db):
    """该卡的「解锁后算力」参考值。优先型号库的 tflops_reference。"""
    if not entry:
        return None
    ref = ((db.data.get("tflops_reference") or {}).get("by_model") or {})
    model = ref.get(entry.get("id"))
    if not model:
        return None
    out = {}
    for key, band in model.items():
        if isinstance(band, dict) and band.get("unlocked"):
            out[key] = band["unlocked"]
    return out or None


def judge_by_compute(tsummary, entry, db, threshold=0.75):
    """核心判定：算力远低于该卡的解锁后参考值 → 未解锁。

    threshold=0.75 的含义：实测值低于解锁后参考值的 75% 就判锁定。
    这个口子比「自比较比例」更宽松，因为不同散热/功耗墙会让绝对值浮动，
    但只要差出四分之一，就不可能是「已解锁」。

    两种判据，谁有数据用谁：
      A. 型号库区间：实测 < 解锁后下界 * threshold → 锁定
      B. 自比较比例：FP16+FP32acc / FP16+FP16acc，<0.65 锁定 / >0.85 解锁
    """
    out = {"verdict": "unknown", "source": "none", "detail": {}, 
           "ratio": None, "shortfall": None}
    if not tsummary:
        return out

    vals = {r["key"]: r.get("tflops") for r in tsummary if r.get("tflops")}
    if not vals:
        return out

    # ---- A. 与型号库的解锁后参考值比
    ref = unlocked_reference(entry, db)
    compared = 0
    short = 0
    ratios = []
    if ref:
        for key, band in ref.items():
            v = vals.get(key)
            if v is None or not band:
                continue
            lo = band[0] if isinstance(band, (list, tuple)) else band
            if not lo:
                continue
            compared += 1
            r = v / float(lo)
            ratios.append((key, r))
            if r < threshold:
                short += 1
        if compared:
            worst = min(ratios, key=lambda x: x[1])
            out["detail"] = {"compared": compared, "short": short,
                             "worst_key": worst[0], "worst_ratio": round(worst[1], 3)}
            out["shortfall"] = round(1.0 - worst[1], 3)
            if short > 0:
                out["verdict"], out["source"] = "locked", "vs_reference"
                return out
            out["verdict"], out["source"] = "unlocked", "vs_reference"
            return out

    # ---- B. 自比较比例（型号库没有参考值时）
    a = analyze_tflops(tsummary, entry, db)
    out["ratio"] = a.get("ratio")
    if a.get("verdict") != "unknown":
        out["verdict"] = a["verdict"]
        out["source"] = "self_ratio"
        out["detail"] = {"note_key": a.get("note_key")}
    return out


def verdict_now(compute, log_info, boot_ts, entry, db):
    """最终结论。算力优先；日志只在「本次开机」且算力缺失时作参考。

    优先级（硬性）：
      1. 本次实测的算力 —— 唯一能反映「此刻真实状态」的证据
      2. 本次开机后生成的 loader.log —— 说明这次引导确实解锁了
      3. 驱动推断（仅在前两者都没有时）
    """
    state = "unknown"
    source = "none"
    notes = []

    c = judge_by_compute(compute, entry, db) if compute else None
    c_state = (c or {}).get("verdict", "unknown")

    log_state = None
    log_fresh = None
    if log_info:
        log_fresh = log_is_current_boot(log_info.get("mtime"), boot_ts)
        if log_fresh:
            log_state = log_info.get("verdict")
        elif log_fresh is False:
            notes.append("log_stale")

    if c_state != "unknown":
        state, source = c_state, (c or {}).get("source") or "compute"
        if log_state == "unlocked" and c_state == "locked":
            notes.append("relocked")
        elif log_state == "unlocked" and c_state == "unlocked":
            notes.append("confirmed")
    elif log_state in ("unlocked", "locked"):
        state, source = log_state, "log_current_boot"
        notes.append("log_only")

    return {"state": state, "source": source, "notes": notes,
            "compute": c, "log_state": log_state, "log_fresh": log_fresh}
