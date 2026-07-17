# 快速获取 DeepSeek Dashboard Token

本文用于获取 `https://platform.deepseek.com/usage` 页面使用的
Dashboard Bearer Token，并将它安全保存到 Linux，供
`api_usage.py` 中的 `usage_deepseek()` 使用。

## 实际环境

本文假设你的环境是：

```text
macOS
  ├── Safari：已经登录 DeepSeek
  └── Terminal：可以通过 SSH 连接 Linux
           |
           v
Linux
  ├── 项目：/data/CODE/ZECTRIX
  └── Token：~/.config/zectrix/deepseek_dashboard_token
```

Safari 操作全部在 **macOS** 上完成；Python 函数和 Token 文件位于
**Linux**。不要尝试在 Linux 上寻找 Safari。

## 60 秒最快流程

### 第一步：在 macOS Safari 复制 Token

1. 打开并登录 `https://platform.deepseek.com/usage`。
2. 按 `Option + Command + I` 打开 Web Inspector。
3. 选择 `Network`。
4. 保持 Web Inspector 打开，按 `Command + R` 刷新页面。
5. 搜索 `get_user_summary`；找不到时搜索 `by_api_key`。
6. 选择一个 `GET` 请求。
7. 打开 `Headers -> Request Headers`。
8. 找到 `authorization: Bearer ...`。
9. 只复制 `Bearer ` 后面的 Token。

### 第二步：在 macOS Terminal 传给 Linux

保持 Token 位于 macOS 剪贴板，然后在 **macOS 本地 Terminal** 运行：

```bash
pbpaste | tr -d '\r\n' | ssh foolma@100.100.37.22 '
  umask 077
  mkdir -p "$HOME/.config/zectrix"
  cat > "$HOME/.config/zectrix/deepseek_dashboard_token"
  chmod 600 "$HOME/.config/zectrix/deepseek_dashboard_token"
'
```

注意：

- 这条命令在 macOS 执行，不是在 Linux SSH shell 中执行。
- `pbpaste` 读取的是 macOS 剪贴板。
- Token 不会显示在终端输出中。

### 第三步：在 Linux 验证

SSH 进入 Linux 后运行：

```bash
file="$HOME/.config/zectrix/deepseek_dashboard_token"
printf 'mode=%s bytes=%s\n' \
  "$(stat -c '%a' "$file")" \
  "$(wc -c < "$file")"
```

当前 Token 格式的预期结果：

```text
mode=600 bytes=64
```

然后运行：

```bash
cd /data/CODE/ZECTRIX

python3 - <<'PY'
from pprint import pprint
from api_usage import usage_deepseek

pprint(usage_deepseek())
PY
```

完成以上三步后，后面的章节只用于详细说明和故障排查。

## 先明确两种凭据

DeepSeek 有两种不同的凭据：

| 凭据 | 常见形式 | 用途 |
| --- | --- | --- |
| 正式 API Key | `sk-...` | 调用模型、查询余额 |
| Dashboard Token | 当前观察到是 64 位字符串 | 查询控制台内部 usage 接口 |

`DEEPSEEK_API_KEY` 不能直接调用 Dashboard 的详细 usage 接口。
本文获取的是第二种 Dashboard Token。

## 详细操作：从 macOS Safari Network 获取

### 1. 在 macOS Safari 打开并登录 DeepSeek

在 Safari 打开：

```text
https://platform.deepseek.com/usage
```

确认页面已经登录，并能正常显示用量。

### 2. 在 macOS Safari 启用开发者功能

如果菜单栏中没有“开发”菜单：

```text
Safari -> 设置 -> 高级 -> 显示网页开发者功能
```

### 3. 在 macOS Safari 打开网页检查器

保持 DeepSeek usage 页面位于最前面，然后按：

```text
Option + Command + I
```

也可以使用：

```text
开发 -> 显示网页检查器
```

### 4. 在 macOS Safari 捕获 usage 请求

1. 打开网页检查器中的 `Network`。
2. 清空已有记录。
3. 保持网页检查器打开。
4. 按 `Command + R` 刷新 DeepSeek 页面。
5. 在过滤框依次搜索：

```text
get_user_summary
```

如果没有，再搜索：

```text
by_api_key
```

或者：

```text
/api/v0/usage
```

正常情况下会看到以下一个或多个请求：

```text
/api/v0/users/get_user_summary
/api/v0/usage/by_api_key/amount
/api/v0/usage/by_api_key/cost
```

### 5. 在 macOS Safari 复制 Dashboard Token

选择任意一个上述 `GET` 请求，打开请求详情：

```text
Headers -> Request Headers
```

找到：

```http
authorization: Bearer <Dashboard Token>
```

只复制 `Bearer ` 后面的内容：

```text
<Dashboard Token>
```

不要把 `Bearer ` 一起复制。

当前观察到的 Token 长度是 64，但平台以后可能调整格式，因此长度只用于
快速排查，不能作为永久规则。

## 从 macOS 安全写入 Linux

本项目默认从下面的文件读取 Token：

```text
~/.config/zectrix/deepseek_dashboard_token
```

### 方法 A：在 macOS Terminal 使用剪贴板直接传输

确保 macOS 剪贴板中只有纯 Token，然后在 macOS 本地终端运行：

```bash
pbpaste | tr -d '\r\n' | ssh foolma@100.100.37.22 '
  umask 077
  mkdir -p "$HOME/.config/zectrix"
  cat > "$HOME/.config/zectrix/deepseek_dashboard_token"
  chmod 600 "$HOME/.config/zectrix/deepseek_dashboard_token"
'
```

Token 不会出现在命令文本或终端输出中。

### 方法 B：在当前 Linux SSH 终端隐藏输入

已经 SSH 到 Linux 时，可以运行：

```bash
mkdir -p "$HOME/.config/zectrix"
chmod 700 "$HOME/.config/zectrix"
umask 077

read -rsp 'Paste DeepSeek Dashboard Token: ' TOKEN
printf '\n'
printf '%s' "$TOKEN" > "$HOME/.config/zectrix/deepseek_dashboard_token"
chmod 600 "$HOME/.config/zectrix/deepseek_dashboard_token"
unset TOKEN
```

`read -s` 会隐藏粘贴内容。

## 在 Linux 验证文件，不显示 Token

运行：

```bash
file="$HOME/.config/zectrix/deepseek_dashboard_token"
printf 'mode=%s bytes=%s\n' \
  "$(stat -c '%a' "$file")" \
  "$(wc -c < "$file")"
```

当前格式的预期结果类似：

```text
mode=600 bytes=64
```

如果长度比预期多约 7 个字符，通常是误把 `Bearer ` 前缀也复制进去了。

## 在 Linux 验证接口

在项目目录运行：

```bash
cd /data/CODE/ZECTRIX

python3 - <<'PY'
from pprint import pprint

from api_usage import usage_deepseek

pprint(usage_deepseek())
PY
```

成功结果类似：

```python
{
    "month": {
        "tokens": "419.8M",
        "cost_cny": 43.9,
    },
    "today": {
        "tokens": "143.1M",
        "cost_cny": 12.4,
    },
    "3d": {
        "cache_hit_percent": 98.7,
    },
}
```

数据会随实际使用变化。

## macOS Safari 找不到 Network 请求

按下面顺序排查：

1. 确认打开的是 `/usage` 页面，而不是首页或 API Keys 页面。
2. 确认页面已经登录且可以显示用量。
3. 先打开 Web Inspector，再刷新页面。
4. 在 Network 中选择 `All`、`XHR` 或 `Fetch`，不要只看 Document。
5. 清除搜索条件后重新搜索 `api/v0`。
6. 不要选择静态 JS、字体、图片或 `OPTIONS` 请求。
7. 选择 Host 为 `platform.deepseek.com` 的 `GET` 请求。
8. 如果页面出现验证码、登录页或 `429`，先在 Safari 中正常完成验证，
   等页面恢复后再刷新。

## macOS Safari 找到请求但没有 Authorization

确认选择的是以下业务请求：

```text
GET /api/v0/users/get_user_summary
GET /api/v0/usage/by_api_key/amount
GET /api/v0/usage/by_api_key/cost
```

以下请求通常不是目标：

```text
OPTIONS ...
main.*.js
favicon.ico
字体、图片和 CSS
```

如果 Request Headers 中确实没有 `authorization`，退出网页检查器，确认
DeepSeek 页面仍然处于登录状态，再重新打开检查器并刷新。

## 常见错误

### `code=40003`

```text
Authorization Failed (invalid token)
```

常见原因：

- Token 已失效；
- 复制不完整；
- 包含了 `Bearer ` 前缀；
- Token 前后存在空格或换行；
- DeepSeek 已撤销当前登录会话。

重新登录 DeepSeek 并重复本文步骤，然后覆盖本地 Token 文件。

### `INVALID_PARAM`

这通常是 usage 请求的时间范围没有按自然日边界对齐，不代表 Token 无效。
使用本项目的 `usage_deepseek()` 时，时间边界会自动处理。

### `429`

请求频率过高或触发了平台防护。不要每秒调用 Dashboard 接口。建议将结果
缓存 5 至 10 分钟。

## 更新或撤销 Token

更新 Token：

```bash
umask 077
printf '%s' '<new-token>' \
  > "$HOME/.config/zectrix/deepseek_dashboard_token"
chmod 600 "$HOME/.config/zectrix/deepseek_dashboard_token"
```

上面的 `<new-token>` 只是占位符。不要把真实 Token 写进脚本、Git 或文档。

删除本地 Token：

```bash
rm -f "$HOME/.config/zectrix/deepseek_dashboard_token"
```

使服务端会话失效：

```text
在 DeepSeek 控制台退出登录，然后重新登录。
```

## 安全注意事项

- Dashboard Token 等同于当前控制台登录凭据。
- 不要把完整 Token 发到聊天、Issue、邮件或截图中。
- 不要把 Token 写入 `api_usage.py`、测试文件或 Git 仓库。
- 本地文件权限保持为 `600`。
- 不要提交 Safari 的 HAR、Copy as cURL 或 Cookie 导出文件。
- 如果 Token 曾公开出现，应退出 DeepSeek 并重新登录，取得新 Token。
- Dashboard usage 接口不是公开 API，路径和认证方式将来可能发生变化。
