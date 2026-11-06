# GPUUnlockInspector 交接文档

> 面向接手人的单页说明：这工具是什么、判定怎么做、哪里容易踩坑、出问题先看什么。

---

## 1. 一句话定位

单文件便携 Windows 工具，用来回答一个问题：**这台机器上的 NVIDIA 显卡，此刻算力有没有被解锁？**

判定不靠猜：**启动就实测张量算力（TFLOPS），与该型号「解锁后参考值」比对**，远低于参考即判未解锁。

---

## 2. 判定逻辑（最重要，先读这段）

```
启动
  ↓
扫描本机有没有带 torch+CUDA 的 Python（找不到就引导安装 / 自举便携 Python）
  ↓
自动跑算力实测（6 项 FP16/BF16/FP32 累加矩阵乘）
  ↓
按本机显卡型号取出「解锁后参考值」
  ↓
实测 < 参考值 × 0.75  →  未解锁
实测 ≥ 参考值 × 0.75  →  已解锁
拿不到参考值 / 实测     →  无法判定
```

### 两种判据，优先用参考值

| 判据 | 条件 | 来源 |
| --- | --- | --- |
| `vs_reference`（首选） | 型号库里有该卡的解锁后参考值 | `gpu_db.json` → `tflops_reference.by_model[id]` |
| `self_ratio`（兜底） | 没有参考值时，用 FP16+FP32acc ÷ FP16+FP16acc | 全速对照路径从未被锁 |

`self_ratio` 阈值：`<0.65` 锁定、`>0.85` 解锁、中间无法判定。

### 三层链与「判定依据」对话框

`verdict_now()` 的优先级是硬性的，UI 上点「查看判定依据」看到的就是这条链：

| 层 | 证据 | 权重 | 界面标注 |
| --- | --- | --- | --- |
| ① | 实测算力（`judge_by_compute`） | **硬否决**：不达标一律判未解锁 | 生效 / 未参与 |
| ② | 本次开机 `loader.log` | 佐证；与实测冲突时被推翻 | 生效 / **被推翻** / 未参与 |
| ③ | 驱动版本推断（`evaluate().rules`） | 仅在前两层都缺失时兜底 | 未参与 |

> ⚠️ **第 ③ 层的那批 Rule（LHR 移除版本 / LHR 设备 ID / 显存频率 / 功耗余量）
> 已经不是主判据**。历史版本把这张表直接铺在对话框里，导致四条全是「跳过」、
> 真正决定结论的「实测 / 参考值」反而不显示。现在对话框只画 ①②③ 三层并标状态，
> `verdict.rules` 仅保留在导出报告与日志里（那是原始记录，属数据完整性）。

### 参考值怎么来的（别乱改）

公式来自 nv-uefi-unlocker 的 `docs/theoretical-uplift.md §6`：

```
FP16acc 张量峰值(TFLOPS) = SM数 × 256 × 2 × boostGHz ÷ 1000
```

该公式由项目用 3090（71.5 vs 白皮书 71）、4090（165 vs 165）、2757（178 vs 实测 177.4）三点验证。

`gpu_db.json` 里每个型号的区间：

- `unlocked` = 理论峰值 × 0.94~1.04
- `locked` = 解锁后 × 0.40~0.55
- **RTX 3070 Ti 是唯一有本机真实实测的**：锁定 31~42、解锁 77~84（与参考图一致）

---

## 3. 日志时效（第二重要）

U 盘上的 `loader.log` 记录的是**上一次引导**的结果，不能证明此刻还生效。

```python
log_is_current_boot(log_mtime, boot_time, tolerance_s=120)
```

- 只有 `mtime >= 本次开机时刻 - 120s` 的日志才算「本次开机」
- 过期日志写入 `notes: ["log_stale"]`，**不参与判定**
- 算力实测永远优先于日志（`relocked` = 日志说解锁、实测说锁）

> 真实案例：日志写于昨天 09:04 引导，今天开机后实测仍锁定 —— 寄存器值在 always-on 岛，
> FLR 后存活、断电即失，驱动 GC6 唤醒/绑定前也会重新推导回锁。

---

## 4. 数据源优先级

| 用途 | 顺序 |
| --- | --- |
| 实时参数采集 | NVML → nvidia-smi → WMI |
| 实时刷新 | HWiNFO → NVML → nvidia-smi |
| 算力实测 | cuBLAS(torch) → 内置 OpenCL（绝对值不可比，只读比例） |

**为何 HWiNFO 排第一**：NVML 功耗/温度被驱动压在约 1 Hz，界面数字「跟不上」。
HWiNFO 持续采样，还能给热点温度 / 显存结温 / 风扇 RPM。

---

## 5. 出问题先看什么

### 5.1 日志

`gpu_inspector.log` 与 exe 同目录。关键时刻长这样：

```
22:32:42.952 [INFO ] [envscan]      done: 1 candidates
22:32:43.308 [INFO ] [onboard]      ensure_environment: torch_env=True scanning=False
22:32:43.708 [INFO ] [verify]       auto_verify: start with ...\python.exe
22:32:49.283 [INFO ] [tflops]       verdict=locked source=vs_reference worst_ratio=0.375
22:32:49.362 [INFO ] [verdict]      final=locked (from compute)
```

设 `GPUINV_LOG=DEBUG` 打开详细。

### 5.2 症状对照

| 症状 | 先查 | 根因 |
| --- | --- | --- |
| 双击没反应 | `gpu_inspector_crash.log` | `--noconsole` 下 `sys.stderr` 是 None |
| 无限弹窗 | 日志有无反复 `run_tflops` | 曾把 exe 自身当 Python 解释器执行 |
| 点击无反馈 | `[tflops]` 是否出现 | 曾在点击时重扫环境，阻塞 3 秒 |
| 弹窗看不见 | 主窗口是否阻塞 | 无边框窗被主窗口挡住 |
| 总是 unlocked | `[verdict] final=` 的 source | 若为 `log_current_boot` 说明算力没跑起来 |

---

## 6. 代码结构

```
src/
├─ core/
│  ├─ nvml.py          NVML ctypes 绑定（零依赖，第 1 层数据源）
│  ├─ smi.py           nvidia-smi XML（第 2 层）
│  ├─ wmi.py           WMI（第 3 层）
│  ├─ collector.py     三层编排 + 实时采样器
│  ├─ hwinfo.py        HWiNFO 共享内存（高刷实时值）
│  ├─ modeldb.py       型号库加载/匹配
│  ├─ unlock.py        ★ 判定引擎（judge_by_compute / verdict_now / log_is_current_boot）
│  ├─ tflops.py        ★ Tensor 算力实测（cuBLAS 桥 + OpenCL 回落）
│  ├─ bench_ocl.py     OpenCL proxy_v1 与 GEMM 内核
│  ├─ runner.py        proxy_v1 后台跑分线程
│  ├─ envscan.py       ★ Python 环境发现（排除自身、扫第三方目录）
│  ├─ deps.py          torch 安装（流式进度）
│  ├─ python_boot.py   便携 Python 自举（python-build-standalone）
│  ├─ usb_boot.py      ★ U 盘识别 + loader.log 解析
│  ├─ usb_maker.py     U 盘制作（格式化 + 写盘）
│  ├─ gpu_family.py    PCI Device ID → 资产家族路由
│  ├─ assets_fetch.py  从参考项目拉资产
│  ├─ archive.py       ★ 跨重启存档（before/ after/ 双文件夹，一份文件含两个测试）
│  ├─ clipimg.py       ★ 纯 ctypes 抓屏 → CF_DIB 剪贴板（不依赖 Pillow）
│  ├─ theme.py         ★ 设计令牌（颜色/字号/间距/动效/缓动/音效/断点）+ cubic_bezier
│  ├─ log.py           ★ 统一日志
│  └─ storage.py       配置/记录持久化
├─ i18n/strings.py     中英全量文案（UI 零硬编码）
└─ ui/
   ├─ app.py           主窗口 + 全部事件
   ├─ panels.py        各业务卡片（HeroPanel / OverviewCard / DetailCard / TflopsCard /
   │                   BenchCard / UsbCard / CrossBootCard / HistoryCard）
   ├─ base.py          ★ 组件（Ctx 字体换算 / Card / Button 四态 / Badge / Sparkline）
   ├─ busy.py          ★ 统一忙碌反馈（Spinner / StatusLine）
   ├─ anim.py          ★ 动效引擎（cubic-bezier 补间 / 可全局关闭 / 减动效）
   ├─ sound.py         ★ 交互音效（现场合成 WAV → Win32 PlaySound，零依赖）
   ├─ dialogs.py       ★ 弹窗（居中 + 分情况进度条 + 首启动引导 + 判定依据三层链）
   └─ scroll.py        整页滚动
```

带 ★ 的是判定与反馈核心，改动前先读对应注释。

### 6.1 UI 层三条铁律（改界面前必读）

1. **字体只能通过 `ctx.font(kind, bold=False)` 取。**
   `theme.FONTS` 的值是**磅**，`Ctx.font()` 负责 `px = pt × 96/72 × DPI × 放大档`
   并转成负值交给 Tk。直接写 `tkfont.Font(size=11)` 会得到 11 **磅**（≈15px 的另一套口径），
   与设计阶梯对不上。
2. **改绘制上下文只能走 `App._make_ctx()`。**
   主题 / 语言 / 字体加粗 / 字号档 四项设置都在这里读取。
   历史上 `set_lang` 与 `set_theme` 各自 `Ctx(...)`，新增字体参数后必然漏掉一处；
   自检 `ctx built through one helper` 会拦住直接构造。
3. **一个容器只能用一种几何管理器。**
   `pack` 与 `grid` 混用会抛 `TclError: already has slaves managed by grid`。
   表格类布局统一用 `grid` + `columnconfigure(weight=)`；`Label(width=N)` 的 N 是
   **字符数**不是像素，字号一放大就会把最后一列挤出卡片。

### 6.2 字体与配色改动后的验收动作

| 动作 | 命令 / 位置 |
| --- | --- |
| 对比度全量复核 | `python tests\test_selfcheck.py`（`test_a11y_and_tokens`，两主题 18 组） |
| 字号换算复核 | 同上（`pt to px conversion present` / `font uses negative size`） |
| 三档字号肉眼验收 | 顶栏「标准 / 大 / 特大」逐档切换，看顶栏是否挤、表格判定列是否被裁 |
| 加粗回退 | 顶栏「常规」切一次，确认无排版塌陷 |
| 减动效 | Windows 设置 → 辅助功能 → 视觉效果 → 动画效果关闭，确认过渡全部即时 |

---

## 7. 构建

```bat
build.bat
```

要求 Python 3.10+（带 tkinter 的官方发行版）。产物 `dist/GPUUnlockInspector.exe`（约 19.0 MB）。

自检：

```bat
python tests\test_selfcheck.py
```

317 项，覆盖 i18n 键集、型号库、判定四态、日志时效、HWiNFO 解析、跨重启存档、
before/after 双文件夹存档、剪贴板 DIB 编码、WCAG AA 对比度、动效缓动单调性、
字体 pt→px 换算、音效降级、便携 Python 自举、反馈组件状态机、自执行防护。
**无 GPU 也能跑**；字体与配色部分不依赖窗口也能验。

---

## 8. 已知限制

1. **参考值大多是估算**。只有 RTX 3070 Ti 是本机真实实测，其余按公式推算。
   要更准，就在目标机上跑一次解锁态、把实测值填进 `gpu_db.json` 的 `unlocked`。
2. **cuBLAS 与 mma_bench 口径不同**。3070 Ti 的 77~84 是项目 mma_bench 口径，
   本机 torch/cuBLAS 实测只有 62~77（全速对照）。判阈值已按各自口径取。
3. **内置 OpenCL 内核打不满 Tensor Core**（约 cuBLAS 的 1/8），只能读比例。UI 会标明引擎。
4. **未收录型号**判「无法判定」，可手动选型号看参考数据（置信度封顶「中」）。
5. **U 盘制作会格式化目标盘**，需两次确认（第一次列清单、第二次手打盘符）。
6. **HWiNFO 是可选增强**，未安装时刷新率受 NVML 限制（功耗约 1 Hz）。
7. **字体默认全量加粗**。细体中文在 1080p/125% 缩放下笔画会糊，所以 `BOLD_BY_DEFAULT = True`；
   不在乎可读性、想回到轻量观感时切顶栏「常规」即可（不重建窗口，只是重绘）。

---

## 9. 这批改动里踩过的坑（别再踩）

| 坑 | 现象 | 根因 |
| --- | --- | --- |
| `overrideredirect(True)` | 弹窗被主窗口挡住，以为没反应 | 无边框窗不进任务栏也不置顶 |
| `sys.executable` 当解释器 | 无限弹窗 | 打包后它就是本 exe，`-c` 会再启一个自己 |
| 点击时调 `find_torch_python()` | 点击后 3 秒无响应 | 每次重扫全机 |
| 给 `self._w` 赋值 | `TypeError: int + str` | `_w` 是 tkinter 内部控件路径名 |
| OpenCL global size 传一维 | 算出全 0、耗时近 0 | 必须二维 `(n, n)` |
| GEMM 输入用常量矩阵 | 512³ 只要 14µs、虚报 88 TFLOPS | 被驱动常量折叠，必须随机数据 |
| `--noconsole` 下写 stderr | 双击即崩 | `sys.stderr` 是 None |
| 后台线程直接操作 Tk | 回调不执行、状态卡住 | Tk 非线程安全，用队列 + `after()` |
| 扫描未完成就判断缺环境 | 弹引导窗卡死 | 要等 `_env_scanning` 变 False |
| 确认弹窗关掉就后台跑 | 用户以为没执行 | 下载/生成/写盘都必须有进度弹窗接上 |
| `subprocess.run` 阻塞读 maker | 进度条一直不动 | 改 `Popen` + 逐行 readline 流式进 events |
| 写盘同步跑在主线程 | 窗口假死、点不动 | 后台线程 + `queue` + `after` 轮询 |
| 剪贴板只写 CF_BITMAP | 部分程序粘贴空白 | 写 CF_DIB(8)，24 位自底向上、行 4 字节对齐 |
| `SetClipboardData` 后再 GlobalFree | 偶发崩溃 | 成功后所有权归系统，不能再释放 |
| `tkfont.Font(size=11)` 当像素用 | 全套字号小 24%，小字糊成一片 | Tk 的 size 正数是**磅**、负数才是像素，须 `size=-px` |
| `Label(width=26)` 当像素宽用 | 字号放大后最后一列被裁 | width 单位是**字符数**，表格改 `grid` + `columnconfigure(weight=)` |
| 同一容器混用 pack 和 grid | `TclError: already has slaves managed by grid` | 一个容器只能选一种几何管理器 |
| 顶栏 7 个控件单行 `pack(side="right")` | 字号放大后互相挤压溢出 | 拆两排：显示类设置 / 行为类设置 |
| `minsize` 等于断点值 | 单栏布局永远不可达 | 断点 1080 时 minsize 必须 < 1080（现 880） |
| 切主题/切语言各自 `Ctx(...)` | 新增字体设置后部分路径漏读 | 统一走 `_make_ctx()`，自检断言禁止直接构造 |

---

## 10. 如果要继续做

优先级建议：

1. **补齐更多型号的真实实测值**（解锁态跑一次填 `gpu_db.json`），这是精度最大短板。
2. **支持 50 系**：目前按参考项目明确拒绝（项目未适配）。
3. **基准测试也接入判定**：现在 proxy_v1 只做前后对比，不参与判定。
4. **多卡**：目前只判第一张卡。

参考项目：https://github.com/physicsdolphin/nv-uefi-unlocker（判定依据与口径来源）
