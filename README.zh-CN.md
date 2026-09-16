# trim-doctor

[![English](https://img.shields.io/badge/English-555555?style=flat)](README.md) [![简体中文](https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-555555?style=flat)](README.zh-CN.md)

只读检查 Linux 挂载点的 SSD TRIM/discard 配置，将设备能力、LUKS 设置、LVM 配置以及挂载选项和定时任务汇总到一份报告中，指出首先发现的阻塞项或无法判断的环节。

![trim-doctor 输出示例](docs/images/example-output.png)

[演示视频](docs/demo.mp4)

## 环境与安装

需要 Linux、Python 3.9+，以及 util-linux 提供的 `lsblk`、`findmnt`。加密卷和 LVM 环境还需要 `cryptsetup`、`lvs`（lvm2）；周期 TRIM 检查使用 `systemctl`。无 Python 运行时依赖。

```bash
git clone https://github.com/zhuhroscar-tech/trim-doctor.git
cd trim-doctor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## 快速使用

```bash
trim-doctor /
trim-doctor /home --json
trim-doctor --version
```

报告检查设备声明的 discard 支持、LUKS 标志和配置、LVM 的 `issue_discards`，以及挂载选项 `discard` 或 `fstrim.timer` 是否启用。退出码 `0` 表示本工具的检查未发现异常；`2` 同时涵盖**发现问题和无法判断**，请结合状态及解释阅读，不要将所有非零结果都当成已确认的阻塞。

也可从[发布页面](https://github.com/zhuhroscar-tech/trim-doctor/releases)下载 `trim-doctor.pyz`，核对同一版本的 `SHA256SUMS.txt` 后运行 `python3 trim-doctor.pyz /`。

## 安全与结果解读

- 不运行 `fstrim`，不修改配置，不解锁卷，也不索取密码；无网络请求和遥测。
- 部分设备、LUKS 头或 LVM 信息需要 root 才能读取。本工具不会自行提权；仅在检查确有需要时使用相应权限。
- 这是配置层面的启发式检查，**不能证明 discard 请求已实际到达物理 SSD**。LVM 的 `issue_discards` 控制 LVM 操作发出的 discard，本身不能证明文件系统 discard 能否透传。自定义 TRIM 任务和特殊存储拓扑需要人工核查。
- 加密存储启用 discard 可能泄露空间分配模式。修改设置前应评估这一取舍；本工具不会代为修改。

## 开发与卸载

```bash
python -m pytest -q
python -m pip uninstall trim-doctor
```

[发布文件](https://github.com/zhuhroscar-tech/trim-doctor/releases) · [MIT 许可证](LICENSE)
