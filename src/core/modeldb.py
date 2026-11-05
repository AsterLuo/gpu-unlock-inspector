# -*- coding: utf-8 -*-
"""型号库：加载 / 匹配 / 字段查询。

设计要点：
- 型号库是外部 JSON（gpu_db.json），随 exe 一起发布，用户可直接编辑补充新型号。
- 匹配用「名字包含 + 显存容量提示」双条件，解决同型号多显存版本（3060 8G/12G、
  4060 Ti 8G/16G、3080 10G/12G、5060 Ti 8G/16G）的歧义。
- 匹配失败不猜：返回 None，由上层显示「型号未收录」并允许手动选择。
"""
from __future__ import annotations

import json
import os
import re
import sys


def resource_root():
    """打包后资源在 sys._MEIPASS；源码运行时在项目根。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def app_dir():
    """exe 所在目录（配置与记录写这里）。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def db_path():
    """优先用 exe 同目录的 gpu_db.json（用户可编辑），否则用内置副本。"""
    external = os.path.join(app_dir(), "gpu_db.json")
    if os.path.isfile(external):
        return external
    bundled = os.path.join(resource_root(), "gpu_db.json")
    if os.path.isfile(bundled):
        return bundled
    return os.path.join(resource_root(), "assets", "gpu_db.json")


class GpuDatabase:
    def __init__(self, path=None):
        self.path = path or db_path()
        self.data = {}
        self.gpus = []
        self.algorithms = {}
        self.lhr_types = {}
        self.error = None
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8-sig") as fh:
                self.data = json.load(fh)
        except FileNotFoundError:
            self.error = "gpu_db_missing"
            self.data = {"gpus": [], "algorithms": {}, "lhr_types": {}}
            return
        except (json.JSONDecodeError, OSError) as exc:
            self.error = f"gpu_db_invalid: {exc}"
            self.data = {"gpus": [], "algorithms": {}, "lhr_types": {}}
            return
        self.gpus = self.data.get("gpus") or []
        self.algorithms = self.data.get("algorithms") or {}
        self.lhr_types = self.data.get("lhr_types") or {}

    # ------------------------------------------------------------ 匹配
    @staticmethod
    def _norm(text):
        t = (text or "").upper()
        t = t.replace("NVIDIA", " ").replace("GEFORCE", " ")
        t = re.sub(r"\bSUPER\b", "SUPER", t)
        t = re.sub(r"[^A-Z0-9]+", " ", t)
        return " ".join(t.split())

    def find(self, gpu_name, mem_total_bytes=None):
        """返回 (entry, how)。how ∈ {name, name+mem, None}。"""
        if not gpu_name:
            return None, None
        target = self._norm(gpu_name)
        mem_gb = None
        if mem_total_bytes:
            mem_gb = round(mem_total_bytes / (1024 ** 3))

        best = None
        best_score = -1
        for entry in self.gpus:
            names = [self._norm(n) for n in (entry.get("match", {}).get("names") or [entry.get("name", "")])]
            for nm in names:
                if not nm:
                    continue
                if nm == target:
                    score = 100
                elif target.startswith(nm + " ") or nm in target.split(" ") or nm in target:
                    score = 60 + min(len(nm), 20)
                else:
                    continue
                hint = entry.get("match", {}).get("mem_gb_hint")
                if hint and mem_gb:
                    if int(hint) == int(mem_gb):
                        score += 40
                    else:
                        score -= 35
                if score > best_score:
                    best_score = score
                    best = entry
        if best is None or best_score < 50:
            return None, None
        hint = best.get("match", {}).get("mem_gb_hint")
        how = "name+mem" if hint else "name"
        return best, how

    def by_id(self, gpu_id):
        for entry in self.gpus:
            if entry.get("id") == gpu_id:
                return entry
        return None

    def choices(self):
        """供下拉选择：[(显示名, id)]"""
        out = []
        for e in self.gpus:
            label = e.get("name", "?")
            hint = e.get("match", {}).get("mem_gb_hint")
            if hint:
                label = f"{label} {hint}GB"
            out.append((label, e.get("id")))
        return out

    def write_external_copy(self):
        """把内置库写到 exe 同目录，便于用户编辑。返回是否写入。"""
        target = os.path.join(app_dir(), "gpu_db.json")
        if os.path.abspath(target) == os.path.abspath(self.path):
            return True
        try:
            with open(self.path, "r", encoding="utf-8-sig") as fh:
                content = fh.read()
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            return True
        except OSError:
            return False
