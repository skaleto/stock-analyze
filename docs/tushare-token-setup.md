# Tushare Pro Token 接入手册

本系统的主数据源是 **Tushare Pro**(需要 token,2000 积分起步)。Baostock 作为兜底(无 token)。本文档说明如何注册、拿 token、本地与 ECS 注入,以及安全规则。

## 1. 注册 + 获取 2000 积分(用户动手,~10 分钟)

### 1.1 注册

打开 https://tushare.pro/register,用手机号或邮箱注册并激活。

→ 系统自动给 **100 积分**(基础)。

### 1.2 完善资料

登录后到 https://tushare.pro/user/info,填写:

- 真实姓名(必填,会脱敏存储)
- 身份证号或证件号(必填)
- 工作单位 / 职业 / 学历
- 主要研究方向(选"个人投资"或"量化研究")

→ 系统额外给 **+20 积分**(资料完善奖励)。

到这一步**积分总和约 120**,不够 2000。继续:

### 1.3 凑到 2000 积分的几条路

| 方法 | 难度 | 积分 |
|---|---|---|
| 邀请新用户注册 | 低 | 50 / 人 |
| 写一篇 Tushare 相关博客 / 知乎文章 | 中 | 100~1000(取决于质量) |
| 提交代码贡献 / Issue | 高(需懂代码) | 100~500 |
| 提交 bug 确认 | 中 | 5~50 |
| 加入官方 QQ 群参与社区维护 | 中 | 50~200 |
| **付费直购** | 低 | ¥500 = 5000 积分(一次性,不订阅) |

**推荐**:**直接充值 ¥500 = 5000 积分**。理由:

- 一次性付费,不订阅
- A 股常规日线 / 财报接口几乎无频次限制
- 省去为凑积分到处发文章的时间

如果你坚持完全免费:**写一篇知乎质量文章 = 500 积分**(标题如《用 Tushare 做 A 股因子回测的踩坑日记》),加上注册的 120 ≥ 620,够日常使用(虽然不是 2000 全权限,但 daily / fina_indicator / index_weight 等核心接口都能用)。

### 1.4 拿到 Token

充值或攒够积分后,打开 https://tushare.pro/user/token,点 "复制 Token"。这串 32 位字符就是 API 密钥。

**⚠️ Token 安全等同密码**:
- 不要写进 git
- 不要写进配置文件
- 不要在日志 / dashboard / 错误消息里打印
- 一旦泄漏立即去 token 页面重置

## 2. 本地开发机注入(MacOS / Linux)

### 方式 A:写进 shell rc(简单)

```bash
echo 'export TUSHARE_TOKEN=你的32位token' >> ~/.zshrc   # zsh
# 或
echo 'export TUSHARE_TOKEN=你的32位token' >> ~/.bashrc  # bash
source ~/.zshrc  # 立即生效
```

测试:

```bash
echo "$TUSHARE_TOKEN" | head -c 8  # 应输出前 8 位字符
```

### 方式 B:direnv(推荐)

如果装了 [direnv](https://direnv.net/):

```bash
cd /Users/you/Documents/stock/stock-analyze
echo 'export TUSHARE_TOKEN=你的32位token' > .envrc
direnv allow
```

`.envrc` 已在 `.gitignore` 中,不会进 git。

进入这个目录会自动加载 token,离开自动卸载。

### 验证

```bash
cd /path/to/stock-analyze
python3 -c "import os, tushare as ts; ts.pro_api(os.environ['TUSHARE_TOKEN']).trade_cal(start_date='20260101', end_date='20260108')"
```

应返回前 8 天的交易日历 DataFrame。若 raise `Exception('您的Token认证错误')`,token 不对;若 raise `Exception('权限不够')`,积分不够 2000。

## 3. ECS 部署机注入

### 3.1 建 secrets 文件(只 root 可读)

```bash
sudo mkdir -p /etc/stock-analyze
sudo nano /etc/stock-analyze/secrets.env
```

写入:

```
TUSHARE_TOKEN=你的32位token
```

保存后:

```bash
sudo chmod 600 /etc/stock-analyze/secrets.env
sudo chown root:root /etc/stock-analyze/secrets.env
```

### 3.2 systemd unit 加载

修改 `deploy/systemd/stock-analyze-market-data.service`(及任何需要 Tushare 的 service):

```
[Service]
EnvironmentFile=/etc/stock-analyze/secrets.env
ExecStart=/opt/stock-analyze/venv/bin/python3 -m stock_analyze prepare-market-data
...
```

`EnvironmentFile` 行让 systemd 把文件里的变量注入到进程 env,**不写进日志**。

重载:

```bash
sudo systemctl daemon-reload
sudo systemctl restart stock-analyze-market-data.timer
```

### 3.3 验证 ECS 拿到 token

```bash
sudo systemctl start stock-analyze-market-data.service
sudo journalctl -u stock-analyze-market-data.service -n 50
```

应看到 prepare-market-data 拉到数据。日志中**不应**出现 token 本身。

## 4. 安全规则(必须遵守)

| 规则 | 实施方式 |
|---|---|
| Token 不进 git | `.envrc` / `.env` / `secrets.env` 都在 `.gitignore` |
| Token 不进日志 | `stock_analyze/data_provider.py` 的错误处理 strip token 字段 |
| Token 不进 cache 文件 | provider 落 cache 时不写 token |
| Token 不进 dashboard | reporting 渲染 HTML 时主动 sanitize |
| 错误堆栈不漏 token | 异常消息只引用 "TUSHARE_TOKEN 未设" 而非值 |

## 5. 故障排查

| 现象 | 原因 | 处置 |
|---|---|---|
| `TushareTokenMissing: TUSHARE_TOKEN env var not set` | env 没注入 | 看 §2 / §3.2 |
| `您的Token认证错误` | token 写错 / 已重置 | 去 https://tushare.pro/user/token 重新复制 |
| `权限不够,请到 https://tushare.pro/forum 获取积分` | 积分 < 2000 | 看 §1.3 |
| `当前接口需要至少 5000 积分` | 个别高级接口要 5000 | 本系统的 7 个核心接口都是 2000 阈值;如遇 5000 错误说明走到了非预期接口 |
| `每分钟请求次数超过 200 次` | 限频(2000 积分用户) | data_provider 内部有 1s sleep,正常 weekly run 不会触发;如触发应等 1 分钟再试 |
| `连接超时` | 网络问题 | Tushare 服务器在国内,如本机走代理可能影响;同 push2 那条经验,试 `unset ALL_PROXY` |

## 6. 与 Baostock 兜底的关系

`make_provider()` 工厂的行为:

```
有 TUSHARE_TOKEN env  →  TushareProvider(主源)
                          └ 临时不可达 → 单次降级到 Baostock(每个 fetch 调用独立判断)
无 TUSHARE_TOKEN env  →  BaostockProvider(从一开始就用 Baostock,无 Tushare 尝试)
```

→ **你不设 token,系统也能跑**,只是慢一点(Baostock 每股查询 ~0.5s,800 票 ~10 分钟;Tushare 1 次拿全市场 ~5 秒)。

## 7. 后续维护

- **Token 过期**:Tushare 没设硬性过期,但建议每 6-12 个月手动重置一次
- **积分到期**:个别情况下 Tushare 会 expire 积分,查 https://tushare.pro/document/1?doc_id=307
- **接口变更**:Tushare 偶尔会改字段。`data_provider.py` 已经定义 mapping,变了改 mapping 一处即可

## 引用

- Tushare Pro 官方文档:https://tushare.pro/document/2
- 接口积分要求总表:https://tushare.pro/document/1?doc_id=290
- 我们项目的 OpenSpec change:`openspec/changes/migrate-data-source-to-tushare-pro/`
