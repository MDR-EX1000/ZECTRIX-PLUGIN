# Zectrix API Usage Dashboard

这个目录按职责分成三层：

```text
api_usage.py    获取并规范化 Kimi / DeepSeek 用量
usage_image.py  生成 400x300 1-bit 黑白 PNG
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
设备原生 400x300 1-bit PNG，不经过 JPEG 或其他有损压缩，也不再上传
800x600 图片交给服务端二次缩放。可以使用 `--output` 指定其他位置。

`preview/` 目录存放三张设计的手动预览样本（V1/V2/V3），供快速查看效果，
不参与自动轮换。

Banner 左侧使用按东八区日期轮换的 AI Coding Slogan。同一天重复生成时
保持不变，英文和中文严格隔天交替；右侧显示东八区更新时间。
项目内置精简的 Noto Sans SC Medium 字体子集（`assets/fonts/NotoSansSC-Slogan-Medium.otf`），
在 400x300 的 E-Ink 面板上比 Bold 字重更轻、更清晰。
设置 `SLOGAN_FONT_FILE` 可以使用本机其他中文字体；本地存在 `assets/fonts/msyh.ttc`
或 `msyhbd.ttc` 时会作为后备字体使用。

图片设计支持注册多个实现。默认的 `rotate` 会按东八区自然日自动选择设计：
Slogan 仍然保持原有的 14 条轮换和英文/中文隔天交替。当前自动轮换名单固定
为 V1 和 V2，因此继续严格隔日切换；V3 已注册为手动选择设计，不改变现有
轮换行为。当前内置设计为：

- `daily-grid`：V1，横向用量网格版，参与自动轮换
- `ring-gauge`：V2，环形配额仪表盘，参与自动轮换
- `big`：V3，突出 WEEK 大数字并使用黑色 DeepSeek 指标区，不参与自动轮换

```bash
# 查看可用设计
python3 usage_image.py --list-designs

# 固定使用当前设计
python3 usage_image.py --design daily-grid

# 固定使用第二版环形仪表盘
python3 usage_image.py --design ring-gauge

# 固定使用第三版大数字仪表盘
python3 usage_image.py --design big

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

# 实时采集、生成并推送到 page 1（rotate/V1/V2 固定路由）
python3 push_usage.py

# 自动轮换设计并推送
python3 push_usage.py --design rotate

# 生成并推送第三版大数字仪表盘到 page 2
python3 push_usage.py --design big

# 推送已有图片
python3 push_usage.py --image ./api_usage.png

# 覆盖生成 ./api_usage.png 并校验，不连接 Zectrix
python3 push_usage.py --dry-run
```

自动生成模式每次都会先覆盖当前目录的 `./api_usage.png`，再执行推送。
生成器直接输出 1-bit 黑白 PNG，与 E-Ink 面板实际显示一致；`push_usage.py`
仍保留 `--dither / --no-dither` 选项，但只影响服务端对这张 1-bit 图片的
后期处理，通常保持默认即可。

生产路由固定为：

```text
rotate / daily-grid / ring-gauge -> page 1
big                             -> page 2
```

因此 V3 使用 `--page-id 1` 会直接报错，避免生产任务把 V3 推到错误页面。

## 定时执行

脚本是单次运行程序，生产环境使用仓库内的 `run_usage.sh`。它会：

- 每次任务使用 `flock` 防止上一轮未结束时重入；
- 生成图片后先写临时文件并校验，再原子替换 `api_usage.png`；
- 对网络错误、超时、429 和 5xx 推送失败自动重试一次，等待 5 秒；
- 对认证类 4xx 不盲目重试；
- 两个 Provider 都失败时保留上一张有效图片。

硬件每 10 分钟刷新一次时，建议 cron 也每 10 分钟运行。若要同时维持
page 1 的 V1/V2 日轮换和 page 2 的 V3 固定布局，使用 runner 的
`--all-pages` 模式；它们在同一把 `flock` 锁内串行执行：

```cron
*/10 * * * * /data/CODE/ZECTRIX/run_usage.sh --all-pages >> /data/CODE/ZECTRIX/zectrix-usage.log 2>&1
```

单独运行时，runner 默认使用 `rotate` 并推送到 page 1；需要单独推送 V3
到 page 2 时使用：

```bash
/data/CODE/ZECTRIX/run_usage.sh --design big
```
