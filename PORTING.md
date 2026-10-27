# 红米 K40 Pro (haydn / SM8350) 的 UniXNU 移植说明

> 本文件记录把 [UniXNU](https://github.com/UniXNU/UniXNU)（文档 <https://unixnu.com>）的通用移植栈落到红米 K40 Pro（代号 **haydn**，SoC **SM8350 / Lahaina**）上的结果。
> 配套仓库：设备仓库 `UniXNU-haydn/`，UEFI 固件树 `uefi-haydn/`（由 venus 改造，同一颗 SM8350）。
> 硬件事实来源：本工作区 `haydn/FACTS.md`、`haydn/M1-M3.md`（均为**实机**测量），以及 stock HyperOS 镜像解出的 `haydn/dtb/haydn-merged.dtb`。

## 0. 设备与状态摘要

| 项 | 值 |
|---|---|
| 代号 / SoC | `haydn` / Qualcomm SM8350（Lahaina v2.1） |
| CPU | 8 核 = 1×Cortex-X1 + 3×A78 + 4×A55，MPIDR Affinity `0x000..0x700` |
| 内存 | 板上 6/8/12 GB；本机实测 8 GB：低段 `0x80000000`、高段起点 `0x1_80000000` |
| 显示 | 1080×2400 AMOLED，DSC cmd mode，120 Hz；启动 framebuffer = splash `0xdfc00000 + 0x2300000` |
| 触摸 | **Focaltech FTS，GENI SPI `0xa8c000`**，IRQ SPI 356，reset GPIO22 / irq GPIO23，坐标 8640×19200 |
| 存储 | UFS 3.1，控制器 `0x1d84000`（+ ICE `0x1d88000` / `0x1d90000`） |
| 中断 | GICv3：GICD `0x17a00000`，GICR `0x17a60000`（stride 0x20000，8 帧）；虚拟定时器 INTID **27** |
| 定时器 | CNTFRQ 19.2 MHz；**物理定时器 CNTP_\* 被 QHEE hypervisor 拦成 UNDEF**（只能用 CNTV） |
| 重启原因 | IMEM `0x146bf000`，偏移 +0x65c |
| PMIC | SPMI `0xc440000`；pmk8350@0 / **pm8350@1** / pm8350c@2 / pm8350b@3 / pmr735b@5 |

**移植进度（两条路线，都要写清楚，避免和早期英文草稿混淆）：**

- **SWEET 路线（本工作区已在真机跑通）**：iOS 16.7.16 的 **iPhone X 用户态 (d22ap)** + 16K 页 HFS+ 根。里程碑 **M3 已达成**——XNU 在 haydn 上挂上 iOS 根、`launchd` 运行（见 `haydn/M3.md` 2026-10-20 10:20 条目）。该路线的独立 git 仓库在工作区 `ios-haydn/`（venus 端口重定向，README 记录到锁屏；其 `docs/lockscreen.jpg` 截图文件当前缺失，锁屏一项以截图回归后为准）。
- **UniXNU 路线（本次交付的仓库）**：走 UniXNU 官方栈——**UEFI + DarwinBoot + 4K 页 xnu-10063（iOS 17）**。这条线**不能**跑 iOS 16 用户态，原因见 §2。本次交付是**可构建、可校验**的 UniXNU 设备仓库 + UEFI 方案，固件卷、payload、boot.img 已在本机端到端产出并通过字节级校验；内核编译与 userdata 打包需 macOS（环境边界，见 §7）。

---

## 1. 为什么是两条路线（关键约束）

UniXNU 的内核来自 Apple 开源 `xnu-10063.141.1`（**iOS 17**），并且用 `kits/NativePrelink` 把自研内核**预链接**到目标 IPSW 里那份原始 kernelcache。这意味着**用户态镜像必须也是 iOS 17**。

- iPhone X（d22ap / iPhone10,3）**最高只到 iOS 16.7.16**，没有 iOS 17。
- 因此 UniXNU 路线不能用 iPhone X 的 IPSW；它沿用 sheng 的做法：目标镜像取 **iPad7,1 iPadOS 17.7.11**（`21H461`，与 sheng 完全相同的那份），在 ADT 里写出 iPad 身份（`--ipad-identity`）。
- 若坚持 iOS 16 / iPhone X 用户态，走 **SWEET 路线**（`repo/` + `haydn/`），那条线已经在真机上把 iOS 16 跑起来了。

本仓库 `board.toml` 的 `[ipsw]` 已按 iPad7,1 17.7.11 配置，并把成员 sha256 钉死（与 sheng 同一份镜像）。

---

## 2. 硬件事实（haydn 独有，均来自实机/stock DT）

这些是移植要落地、且和 sweet/sheng/venus 不同的点：

```
启动 framebuffer (splash) : 0xdfc00000 + 0x2300000   # cont_splash_region
虚拟定时器 INTID          : 27   (PPI 11；sweet 是 19)
物理定时器                : EL1 写 CNTP_* 直接 UNDEF（QHEE），XNU 必须只用 CNTV
GICD / GICR               : 0x17a00000 +0x10000 / 0x17a60000 +0x100000, stride 0x20000
arm-io 窗口               : 0x17000000（GIC 子节点 reg 用窗口内偏移）
UFS 控制器 (ufs_mem)      : 0x1d84000 +0x3000（UFSHCI VER=0x300 即 3.0，HS-G4）
UFS ICE / ICE_HWKM        : 0x1d88000 +0x8000 / 0x1d90000 +0x9000
UFS VCC 供电              : PM8350 L7 = RPMh "ldob7" 2504..2952 mV（ABL 可能未投票 → 需先投票）
触摸                      : Focaltech FTS on GENI SPI 0xa8c000（不是 I2C！sweet 才是 Goodix I2C）
按键 / PON                : PM8350 PON HLOS 0x1300 SID1；vol_up = PM8350 SID1 GPIO6
SPMI arbiter              : 0xc440000
IMEM restart_reason       : 0x146bf000 + 0x65c
内存高段起点              : 0x1_80000000（XNU 内核内存需落在 4 GiB 以下才 DMA 可达，见 §5）
```

GCC 里与 UFS 相关的时钟分支（取自 mainline `gcc-sm8350.c`，NativeUfs 若自管时钟要用）：

```
gcc_ufs_phy_axi_clk          enable 0x77010  (hwcg bit1)
gcc_ufs_phy_ahb_clk          enable 0x77018  (hwcg bit1)
gcc_ufs_phy_tx_symbol_0_clk  enable 0x7701c
gcc_ufs_phy_rx_symbol_0_clk  enable 0x77020
gcc_ufs_phy_rx_symbol_1_clk  enable 0x770b8
gcc_ufs_phy_unipro_core_clk  enable 0x7705c  (hwcg bit1)
gcc_ufs_phy_ice_core_clk     enable 0x77064  (hwcg bit1)
gcc_ufs_phy_phy_aux_clk      enable 0x7709c  (hwcg bit1)
gcc_aggre_ufs_phy_axi_clk    enable 0x770cc  (hwcg bit1)
ufs_phy_gdsc                 0x177004
```

---

## 3. 本次交付的设备仓库（`UniXNU-haydn/`）

```
UniXNU-haydn/
  board.toml                      板描述（[ipsw]/[kernel]/[firmware]/[payload]/[userdata]/[bootimg]）
  configs/haydn_defconfig         内核开关（首个 bring-up：4K XNU 到控制台 + 原生 UFS 根）
  scripts/make-haydn-adt.py        haydn ADT 生成器（§2 地址；--ipad-identity 写 iPad7,1 身份）
  scripts/check-adt-haydn.py       离线 ADT 校验（Linux 可跑，33 项）
  scripts/check-haydn.py           UEFI 内存图预检（对真实 haydn DT 校验，仿 sheng）
  scripts/fetch-haydn-dtb.py       从 HyperOS 镜像重建合并 DT（薄封装 haydn/extract.py）
  scripts/verify-bootimg.py        boot.img 字节级拆解校验
  package/unixnu.toml              固件卷内的 boot manager 兜底配置
  PORTING.md                       本文件
```

`[userdata]` 用的是实机 shim 读到的分区（`haydn/M3.md` 2026-10-19 20:05）：`first_lba=0x2a8000`, `last_lba=0x3afafba`, `lba_bytes=4096`。
`[payload.adt]` 的 `chip_id = "207"`（十进制 = A10X `0xCF`），`cpu_regs` 是 haydn 的 8 个 MPIDR，`psci_method = smc`（M1 实测 PSCI 1.1 / SMCCC 1.1 走 SMC）。

**关于 splash dtb 节点**：sheng 因为用 mainline DT 需要 `[[bootimg.dtb_node]]` 补 `splash_region`；而本移植**追加的是 stock 合并 DT**，它本来就带 `/reserved-memory/splash_region {label="cont_splash_region"; reg=<0 0xdfc00000 0 0x2300000>}`，再加会 `FDT_ERR_EXISTS`。所以 board.toml **不设该节点**，只在注释里说明（已用 fdtget + M1 的 `PAR fb` 落实）。

---

## 4. UEFI 侧移植（`uefi-haydn/` = venus→haydn）

> 完整方案与逐文件清单见 **`uefi-haydn/HAYDN-PORT.md`**（含本机编译踩坑记录：BaseTools 子模块、
> edk2-pytool 代理、mu_nasm 的 Linux 处理、`CLANG_BIN` 尾斜杠等）。本节只列要点。

haydn 与小米 11 **venus 同 SoC（SM8350 / Lahaina）**，故从 venus 端口派生最省事：

- `Platforms/Xiaomi/haydnPkg/`（由 venusPkg 复制改名）：`haydn.dsc`（新 PLATFORM_GUID、`PcdSmbiosSystemModel="Redmi K40 Pro"`、RetailSku `M2012K11AC`、framebuffer 改回 **1080×2400**）、`haydn.fdf`（新 FvNameGuid）、`Include/{APRIORI,DXE,RAW}.inc` 指向 `Binaries/haydn`。
- `Resources/Configs/haydn.toml`：`uefi_fd` base `0x9FD00000` size `0x200000`；`header_version = 1`；`append_dtb = true`；`requires_kernel_header = true`（与 venus 一致）。
- `Binaries/haydn → Binaries/venus` 软链：复用 Project-Silicium/Device-Binaries 里 venus 的 QcomPkg 闭源驱动（同 SoC）。**注意 venus 与 haydn 面板不同**，RawFiles 里的 Panel xml 需在实机上按“xiaomi 38 08 0a” 1080×2400 面板重选/替换。
- `Silicium-ACPI/Platforms/Xiaomi/haydn`：从 venus 复制（`AcpiTables.inf` 引 `Common/SSDT.aml` + `Lahaina/APIC,FACP,GTDT.aml`）。
- `haydnPkg/FdtBlob_compat/haydn.dtb` + `Resources/DTBs/haydn.dtb/.dts`：合并 stock DT。
- **内存图** `haydnPkg/Library/MemoryMapLib/MemoryMapLib.c`：以 venus 为骨架，按 haydn 预留区重排——DXE Heap 因 stock `modem_region` 占到 `0x9F000000` 而从 `0x9B800000` 上移到 `0xA0000000`；Splash `0xDFC00000`；QTEE/TZApps 落在 stock `removed_region 0xD8800000`。

**预检结果**（在 `uefi-haydn/` 下跑 `check-haydn.py`）：

```
21 DTS reservations checked against 54 descriptors
0 static errors; 0 vendor inputs still missing; hardware unverified
```

UEFI 全量编译**在本机 Linux 成功**（`build_uefi.py -d haydn -r DEBUG` → `PROGRESS - Success`）：

```
Build/haydnPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd   2097152 bytes (0x200000)
sha256 dfe5d5000856d457... ; Mu-haydn.img 1957888 bytes
```

### 编译踩到的环境坑（都已在本机解决，供复现）

1. **BaseTools BrotliCompress 编译失败**：brotli 子模块为空。→ `git submodule update --init BaseTools/Source/C/BrotliCompress/brotli` 后 `make -C Mu_Basecore/BaseTools/Source/C` 通过。
2. **MdePkg/SecurityPkg .dec “File/directory not found”**：`MipiSysTLib/mipisyst`、`BaseFdtLib/libfdt`、`BrotliCustomDecompressLib/brotli`、`oniguruma`、`SecurityPkg/.../libspdm`、几个 UnitTestFramework 子模块缺失 → 逐 `submodule update --init --depth 1`。
3. **stuart 外部依赖**：edk2toolext 用 urllib 下载（不认 SOCKS）。→ 用 **http 混合代理** `http_proxy=http://127.0.0.1:10808`；`mu_nasm` 是 Azure Artifacts 私源包，官方 flatcontainer 404，改从 `.../nuget/v3/flat2/mu_nasm/20016.1.1/mu_nasm.20016.1.1.nupkg` 取，塞进 `~/.nuget/packages/`；nuget(mono) 的代理写在 `~/.config/NuGet/NuGet.Config`。
4. **mu_nasm 无 Linux 二进制**（只有 Windows）→ 把系统 `/usr/bin/nasm` 拷进 `mu_nasm_extdep/Linux-x86/`。
5. **`CLANG_BIN=/usr/bin` 少了尾斜杠** → EDK2 拼出 `/usr/binclang`（Error 127）。必须是 `CLANG_BIN=/usr/bin/`。
6. **打包阶段 “DTB Missing in Resources/DTBs”** → 把 `haydn.dtb` 放进 `Resources/DTBs/`。

---

## 5. ADT 生成与离线校验

生成（dram size 用本机 8 GB 观测 `0x180000000`；注意内核内存要压到 4 GiB 以下，见下）：

```sh
python3 UniXNU-haydn/scripts/make-haydn-adt.py --output /tmp/haydn.adt \
  --dram-size 0x180000000 --owned-memory-size 0x100000000 --ipad-identity \
  --unique-chip-id 5357454554000001 --chip-id 8011 \
  --root-match-volume-uuid 04769d26-2ca0-4825-b443-e2e5f88300f2 \
  --packed-data-ramdisk --trust-cache
# haydn ADT: 13548 bytes, dram 0x180000000, timebase 19200000
```

离线校验（Linux，无需 macOS/Xcode）：

```sh
python3 UniXNU-haydn/scripts/check-adt-haydn.py /tmp/haydn.adt --dram-size 0x180000000
# 33/33 checks passed
```

校验项覆盖：ADT 节点编码整块消费、根 compatible 含 `qcom,sm8350`+`xiaomi,haydn`、`/chosen`（64B random-seed、dram base/size、sha2-384、CHRP NVRAM 代理的 header 校验和 + Adler-32、memory-map 占位）、`/cpus` 8 核且 MPIDR 恰为 `0x000..0x700`、cpu0 running、每核 19.2 MHz、`/psci smc`、`/arm-io` ranges 是 `0x17000000`、`intc` reg 对得上 live DT 的 GIC 地址与 stride 0x20000、`ufshc` 类节点存在。

**内存与 DMA 约束（两条路线不同，别混为一谈）**：

- **SWEET 路线（iOS16，已在真机跑通）**实测过：SM8350 UFS 主控访问不到 4 GiB 以上，`sweet_ufs.c` 自管 bounce buffer，若内核内存落在高段（`0x1_80000000`）就报 `SWEET UFS: no contiguous DMA memory` / READ(10) 超时；解法是把 ADT 的 `dram-base/size` 改成 `0xC0000000 + 3 GiB` 这类 4 GiB 以下窗口（`haydn/M3.md` 修复链 #4）。这是 **sweet_ufs 实现**带来的约束。
- **UniXNU 路线（iOS17，本移植）**由 DarwinBoot 在 UEFI 内存图里分配一块连续窗（`darwinboot-memory-size` = `owned_memory_size`），内核落点由 UEFI 分配器决定。`NativeUfs` 是否同样受 32-bit DMA 限制，取决于它的队列/DMA 描述符编程，本移植**未在真机验证**——bring-up 时要么确认 NativeUfs 用了 64-bit DMA/SMMU，要么把窗请求留在低段。因此 `board.toml` 取 `owned_memory_size = 0x100000000`（4 GiB，保守值，远小于 8 GB 板从 `0x80000000` 起的 6 GiB 可用 DRAM），先保证一块连续大块能分到，跑通后再按需调。`dram_size`（ADT 里交给 XNU 的可用范围）仍是 `0x180000000`。

---

## 6. payload 与 boot.img：端到端已在本机产出并校验

### payload（用现成的 SWEET arm64 内核工件走通 UniXNU payload 打包路径）

```sh
cd unixnu
./unixnu build payload -D ../UniXNU-haydn --out /tmp/haydn-out \
  --kernel ../haydn/out/xnu-venus/kernel.development.sweet \
  --unique-chip-id 5357454554000001
```

产物 `out/haydn/payload/payload.img`（192 MiB FAT 卷，标签 `HAYDNESP`）：内含 `EFI/UniXNU/unixnu.toml`（entry type=xnu）+ `Payloads/kernel.macho` + `Payloads/kernel.macho.adt`。`payload.json` 记录 `kernel_sha256 1ce359d4…`（确定性输入）。注意：ADT 的 `random-seed` 每次 `os.urandom(64)`，所以 `adt_sha256` 与 `payload_sha256` **每次构建都不同**——这是 UniXNU 上游的既定行为（docs/guide/overview.md：“The payload contains a random seed in the ADT, so two payload builds differ”），校验器比的是结构与内容规则，不是这两个哈希。

> `--kernel` 用 SWEET 工件只是**验证打包/校验链路**（本机没有 macOS/Xcode，跑不了 §7 的 kernel 步骤）。真机上 payload 里的 `kernel.macho` 必须是 UniXNU 自己 `build kernel` 的 iOS17 4K 内核——SWEET 内核是 16K 页、且是 iOS16，跑不起 iPad7,1 17.7.11 用户态。

### boot.img（真实固件卷 + stock DT + payload）

```sh
./unixnu bootimg -D ../UniXNU-haydn --out /tmp/haydn-out \
  --uefi ../uefi-haydn \
  --fd ../uefi-haydn/Build/haydnPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd \
  --dtb ../haydn/dtb/haydn-merged.dtb          # 不指定 --ramdisk 时自动取上面的 payload.img
# bootimg: /tmp/haydn-out/haydn/boot/boot.img (203284480 bytes)
```

字节级拆解校验：

```sh
python3 UniXNU-haydn/scripts/verify-bootimg.py
# PASS 全部 12 项：magic / header v1 / page 4096 / 载入地址 /
#   单一 gzip 成员 / 追加 DTB 与源树一致(5c61abd4…) /
#   FD 与编译产物一致(dfe5d500…) / FD 大小=config /
#   shim 带 arm64 镜像头且头描述 shim+FD @ base 0x9fd00000 /
#   ramdisk == payload FAT 卷 / 无越界尾料
```

校验分两类：**确定性**比较（FD、DTB、shim 头、镜像几何）跨构建稳定，上面给出了它们的哈希前缀；**seed 相关**比较（ramdisk/payload/boot.img 整体哈希）每次构建都变，脚本按“boot.img 内嵌的 ramdisk 与**本次**payload.img 逐字节相等”判定，不断言某个固定值。bootimg 内部的自校验（`bootimg.verify` 反解重比）同样通过，二者互证。

### DTB 重建可复现性

`fetch-haydn-dtb.py` 从 HyperOS 的 `boot/vendor_boot/dtbo.img` 重跑，输出与本工作区已用的 `haydn-merged.dtb` **逐字节相同**（`cmp` → IDENTICAL），匹配 overlay `dtbo-10-0`（`Haydn based on ... SM8350`），splash label `cont_splash_region`。

---

## 7. 本机（Linux/WSL2）做不了、需要 macOS 的部分

UniXNU 明确“developed and tested on macOS only”。本机是 WSL2 Linux，以下两步有**工具性**缺口（非设计错误）：

1. **`build kernel`**：XNU 用 iOS SDK 的 Xcode 交叉编译，需 macOS。→ 在 Mac 上跑，或用上游 CI 产物（`--kernel FILE` 传入）。
2. **`KernelCache` LZFSE 解码**：`ipsw fetch`/prelink 用 macOS `/usr/bin/compression_tool`。本机 `ipsw info` 与逐成员读取（HTTP range，CDN 返回 206）**成功**，但 `ipsw fetch` 停在缺 `compression_tool`。
3. **`build userdata`**：用 `hdiutil` 只读挂载 IPSW 里的 OS/SystemOS dmg 再抽取，同样需 macOS。

> 这三步都在 Mac 上属于常规操作；本移植已把它们**前面**的一切（板配置、ADT、payload 打包、UEFI 编译、bootimg、各类校验）在 Linux 上端到端跑通并证明，剩下的就是“把真实 iOS17 内核 + 原始 kernelcache 喂进 prelink”。

SWEET 路线的对照：`haydn/M3.md` 显示在**同一台 WSL2** 上用 `apfs-fuse`（读 APFS）+ ext4 组树 + `libguestfs`（挂 HFS+ `tar-in`）绕开了 hfsplus 缺失，成功产出并刷入 iOS16 HFSX 根，实机 `launchd` 运行。这是 iOS16 侧的既有成果，与本 UniXNU(iOS17) 交付并列。

---

## 8. 尚未完成 / 真机 bring-up 待办（诚实清单）

| 项 | 状态 | 说明 |
|---|---|---|
| 内核预链接 kernel.macho | 需 macOS | 用 iPad7,1 17.7.11 kernelcache；pin 已在 board.toml |
| `root_match_volume_uuid` | 待填 | 需从真实 userdata 的 OS 卷 UUID 取得（当前为 sheng 占位） |
| NativeUfs 认 haydn | 待改 | 上游 `drivers/NativeUfs` 的 match 串是 `qcom,sm8550-ufshc`；haydn ADT 用 `qcom,sm8350-ufshc`，需在设备 drivers/ 覆盖或加兼容串 |
| UFS VCC 投票 | 实机已证 | SWEET 侧确认 `ldob7` 2952 mV 投票修复挂死；UniXNU 的 NativeUfs 需等效（sheng 有 `sheng-pmgr=2`） |
| Focaltech 触摸驱动 | 未做 | SPI `0xa8c000`，非 I2C；参考 `UniXNU-sheng/drivers/NativeNovatekTouch` 结构，控制器协议换成 FTS SPI |
| PM8350 按键 / RTC | 未做 | PON 0x1300 SID1、vol GPIO6；SM8350 的 PMIC 与 sheng 不同 |
| 面板 XML（Binaries） | 待替换 | venus RawFiles 面板非 haydn；需在真机确认并换 panel xml |
| 硬件实机验证 | 未做 | 所有 `hardware_tested:false`；本文件只声明软件级可构建+可校验 |

`unique-chip-id`（平台 seed）不落任何仓库，`payload` 步骤要求显式传入（`--unique-chip-id`）。

---

## 9. 从零复现（Linux 侧）

```sh
# 依赖
apt-get install -y clang lld make cmake ninja python3 python3-pip \
    iasl nasm uuid-dev dtc device-tree-compiler mtools mono-devel python3-venv
# pip（走 http 混合代理）
pip install --break-system-packages pysocks requests rich \
    edk2-pytool-library edk2-pytool-extensions pefile antlr4-python3-runtime coloredlogs

# 1) 设备仓库 + 配置 + 离线 ADT 校验
./unixnu defconfig haydn_defconfig -D ../UniXNU-haydn --out /tmp/haydn-out
python3 ../UniXNU-haydn/scripts/make-haydn-adt.py --output /tmp/haydn.adt --dram-size 0x180000000 ...
python3 ../UniXNU-haydn/scripts/check-adt-haydn.py /tmp/haydn.adt --dram-size 0x180000000

# 2) UEFI 固件（先按 §4 补齐 BaseTools/子模块/nuget/extdep/nasm/CLANG_BIN 尾斜杠）
cd ../uefi-haydn
python3 ../UniXNU-haydn/scripts/check-haydn.py         # 内存图预检
python3 build_uefi.py -d haydn -r DEBUG                # -> SILICIUM_UEFI.fd
python3 ../UniXNU-haydn/scripts/fetch-haydn-dtb.py .cache/haydn/stock  # 重建并 cmp DTB

# 3) payload + bootimg（内核先用现成工件验链路；真交付换成 macOS 的 iOS17 内核）
cd ../unixnu
./unixnu build payload -D ../UniXNU-haydn --out /tmp/haydn-out --kernel <KERN> --unique-chip-id <HEX>
./unixnu bootimg  -D ../UniXNU-haydn --out /tmp/haydn-out --uefi ../uefi-haydn \
    --fd ../uefi-haydn/Build/haydnPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd \
    --dtb ../haydn/dtb/haydn-merged.dtb
python3 ../UniXNU-haydn/scripts/verify-bootimg.py

# 4) macOS 上补齐：build kernel / build userdata / ipsw fetch(kernelcache)
# 5) 真机：fastboot flash userdata userdata-root.img; fastboot flash boot boot.img; fastboot reboot
```

> 刷写门槛（`haydn/FACTS.md`/`M1.md` 实机）：K40 Pro 的 ABL ① `fastboot boot` 是空操作，必须 `flash boot`+reboot；② 拒小镜像，需大容器（SWEET 用 Ubuntu 式 v2 容器；UniXNU v1 镜像若被拒用 `mkcontainer.py` 思路包装）；③ 先 `fastboot --disable-verity --disable-verification flash vbmeta`，失败后 `fastboot set_active a`；④ PSCI SYSTEM_RESET 无效，测完手动长按断电。国行 HyperOS 的 BL 解锁门槛是第一道硬关。

---

## 10. 参考

- UniXNU 主体与文档：<https://github.com/UniXNU/UniXNU> / <https://unixnu.com>
- 移植指南：`/porting/`、`/porting/device-repository`、`/porting/device-tree`、`/devices/sheng`
- 设备参考仓库：`UniXNU-sheng`（SM8550，本仓库结构蓝本）、`uefi-sheng`（Mu-Silicium）
- 本工作区：`haydn/FACTS.md`（硬件事实）、`haydn/M1-M3.md`（实机进度）、`repo/`（SWEET iOS16 路线）
- venus 端口（同 SoC）：`uefi-haydn/Platforms/Xiaomi/venusPkg`、`Resources/Configs/venus.toml`

*本文件所有命令、校验结果与产物哈希均为 2026-10-20 在本工作区实际执行记录；标注“需 macOS/待办”的项目尚未在本机执行。*
