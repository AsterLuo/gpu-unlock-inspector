# -*- coding: utf-8 -*-
"""配置与测量记录的持久化。

约束（来自需求）：配置与测试记录写在 exe 同目录，不污染系统。
所以不碰注册表、不写 %APPDATA% —— 全部落在 _app_dir()。

Windows 上如果 exe 被放在 Program Files 之类只读目录，写入会失败；
这时降级为「只在内存里保存」，界面给一条提示，绝不崩溃。
"""
from __future__ import annotations

import json
import os
import time

from core.modeldb import app_dir

CONFIG_NAME = "gpu_inspector_config.json"
RECORDS_NAME = "gpu_inspector_records.json"

DEFAULT_CONFIG = {
    "lang": "zh",
    "theme": "light",
    "bench_duration": 30,
    "bench_algo": "proxy_v1",
    "bench_intensity": "mid",
    "manual_gpu_id": None,
    "window": {"w": 1320, "h": 880},
}

MAX_RECORDS = 200


def _path(name):
    return os.path.join(app_dir(), name)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return dict(default)


def _write_json(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


class Store:
    def __init__(self):
        self.config = _read_json(_path(CONFIG_NAME), DEFAULT_CONFIG)
        # 补齐旧配置缺的键
        for k, v in DEFAULT_CONFIG.items():
            self.config.setdefault(k, v)
        self.readonly = False
        self.records = self._load_records()

    # ------------------------------------------------------------ 配置
    def save_config(self):
        ok = _write_json(_path(CONFIG_NAME), self.config)
        if not ok:
            self.readonly = True
        return ok

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value, save=True):
        self.config[key] = value
        if save:
            self.save_config()

    # ------------------------------------------------------------ 记录
    def _load_records(self):
        data = _read_json(_path(RECORDS_NAME), {"records": []})
        recs = data.get("records")
        return recs if isinstance(recs, list) else []

    def save_records(self):
        ok = _write_json(_path(RECORDS_NAME), {"records": self.records[-MAX_RECORDS:]})
        if not ok:
            self.readonly = True
        return ok

    def add_record(self, rec):
        rec = dict(rec)
        rec.setdefault("ts", time.time())
        rec.setdefault("iso", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec["ts"])))
        self.records.append(rec)
        if len(self.records) > MAX_RECORDS:
            self.records = self.records[-MAX_RECORDS:]
        self.save_records()
        return rec

    def delete_record(self, iso):
        self.records = [r for r in self.records if r.get("iso") != iso]
        self.save_records()

    def clear_records(self):
        self.records = []
        self.save_records()

    def latest_pair(self):
        """返回 (before, after)：各自取最近一条。"""
        before = None
        after = None
        for r in reversed(self.records):
            if r.get("kind") == "before" and before is None:
                before = r
            elif r.get("kind") == "after" and after is None:
                after = r
            if before and after:
                break
        return before, after