# Zectrix API Usage Dashboard

这个目录按职责分成三层：

```text
api_usage.py    获取并规范化 Kimi / DeepSeek 用量
usage_image.py  生成 400x300 黑白 PNG
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

默认生成当前目录的 `./api_usage.png`，后续每次运行直接覆盖该文件。也可以
使用 `--output` 指定其他位置。

单个 Provider 暂时不可用时，图片仍会生成，失败区域显示
`UNAVAILABLE`。两个 Provider 都不可用时命令退出，不生成新图片。

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

# 推送已有图片
python3 push_usage.py --image ./api_usage.png

# 覆盖生成 ./api_usage.png 并校验，不连接 Zectrix
python3 push_usage.py --dry-run
```

自动生成模式每次都会先覆盖当前目录的 `./api_usage.png`，再执行推送。
生成的图片已经是 1-bit 黑白 PNG，因此默认关闭服务端抖动。只有需要推送
包含灰阶的其他图片时才使用 `--dither`。

## 定时执行

脚本是单次运行程序，建议交给 cron 或 systemd timer。例如每 30 分钟推送
一次：

```cron
*/30 * * * * cd /data/CODE/ZECTRIX && /usr/bin/python3 push_usage.py >> ./zectrix-usage.log 2>&1
```
