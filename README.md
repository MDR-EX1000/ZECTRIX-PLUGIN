# Zectrix API Usage Dashboard

这个目录按职责分成三层：

```text
api_usage.py    获取并规范化 Kimi / DeepSeek 用量
usage_image.py  生成 800x600 灰度 PNG
push_usage.py   通过 Zectrix Open API 推送图片
```

## 安装

```bash
cd /data/CODE/ZECTRIX
python3 -m pip install -r requirements.txt
```

Kimi 继续支持 `KIMI_API_KEY`。为了让 cron 不依赖 shell 初始化文件，也可以
将 Key 以纯文本保存到：

```text
~/.config/zectrix/kimi_api_key
```

DeepSeek Dashboard Token 继续使用：

```text
~/.config/zectrix/deepseek_dashboard_token
```

## 生成预览图

```bash
python3 usage_image.py
```

默认生成当前目录的 `./api_usage.png`，后续每次运行直接覆盖该文件。输出为
800x600；版式使用 400x300 逻辑坐标，因此文字和图形比例保持不变。也可以
使用 `--output` 指定其他位置。

Banner 左侧使用按东八区日期轮换的 AI Coding Slogan。同一天重复生成时
保持不变，英文和中文严格隔天交替；右侧显示东八区更新时间。
项目内置精简的 Noto Sans SC 字体子集。设置 `SLOGAN_FONT_FILE` 可以使用
本机其他中文字体；本地存在 `assets/fonts/msyhbd.ttc` 或 `msyh.ttc` 时会
优先使用微软雅黑。

图片设计支持注册多个实现。默认的 `rotate` 会按东八区自然日自动选择设计：
Slogan 仍然保持原有的 14 条轮换和英文/中文隔天交替；设计则每天前进一个
实现，因此有两个设计时会严格隔日切换。当前内置设计为：

- `daily-grid`：当前的横向用量网格版
- `ring-gauge`：第二版环形配额仪表盘，带黑色中段时间/标语条

```bash
# 查看可用设计
python3 usage_image.py --list-designs

# 固定使用当前设计
python3 usage_image.py --design daily-grid

# 固定使用第二版环形仪表盘
python3 usage_image.py --design ring-gauge

# 或通过环境变量固定设计
USAGE_IMAGE_DESIGN=daily-grid python3 usage_image.py
```

单个 Provider 暂时不可用时，图片仍会生成，失败区域显示
`UNAVAILABLE`。两个 Provider 都不可用时命令退出，不生成新图片。

## 测试

测试集中放在 `test/` 目录，运行完整测试套件：

```bash
python3 -m unittest discover -s test -t .
```

## 配置 Zectrix

安全保存 Open API Key：

```bash
install -d -m 700 ~/.config/zectrix
read -rsp 'Zectrix API Key: ' ZECTRIX_KEY
printf '\n'
printf '%s' "$ZECTRIX_KEY" > ~/.config/zectrix/api_key
chmod 600 ~/.config/zectrix/api_key
unset ZECTRIX_KEY
```

如果 API Key 下只有一台设备，程序会自动发现。存在多台设备时，将目标设备
ID 保存到：

```bash
read -rp 'Zectrix Device ID: ' ZECTRIX_DEVICE
printf '%s' "$ZECTRIX_DEVICE" > ~/.config/zectrix/device_id
chmod 600 ~/.config/zectrix/device_id
unset ZECTRIX_DEVICE
```

也可以使用环境变量：

```text
ZECTRIX_API_KEY
ZECTRIX_DEVICE_ID
ZECTRIX_API_BASE_URL
ZECTRIX_PAGE_ID
```

## 推送

```bash
# 查看 API Key 下的设备
python3 push_usage.py --list-devices

# 实时采集、生成并推送到 page 1
python3 push_usage.py

# 自动轮换设计并推送
python3 push_usage.py --design rotate

# 推送已有图片
python3 push_usage.py --image ./api_usage.png

# 覆盖生成 ./api_usage.png 并校验，不连接 Zectrix
python3 push_usage.py --dry-run
```

自动生成模式每次都会先覆盖当前目录的 `./api_usage.png`，再执行推送。
生成器输出 8-bit 灰度 PNG，保留文字和图形边缘的灰度信息；推送时默认开启
Zectrix 服务端抖动，由设备链路负责转换为适合 E-Ink 的黑白点阵。
需要对比硬阈值效果时使用：

```bash
python3 push_usage.py --no-dither
```

## 定时执行

脚本是单次运行程序，建议交给 cron 或 systemd timer。例如每 30 分钟推送
一次：

```cron
*/30 * * * * cd /data/CODE/ZECTRIX && /usr/bin/python3 push_usage.py >> ./zectrix-usage.log 2>&1
```
