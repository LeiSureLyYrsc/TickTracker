# TickTracker

基于 [NoneBot2](https://nonebot.dev) 的代肝记录管理插件：追踪游戏代肝次数、打卡状态与每日进度，并附带 WebUI 数据面板。

## 功能

- **游戏组**：将多个游戏归入一个游戏组（如「原崩绝」），由游戏组统一记录**应得次数**，组内各游戏单独记录**已完成次数**与**今日打卡**
- 数据聚合：按游戏组统计用户应得 / 已完 / 已打卡进度
- WebUI（前端为 [TickTracker-Web](https://github.com/LeiSureLyYrsc/TickTracker-Web)）：
  - 管理员：代肝数据（按用户聚合 + 搜索）、游戏组/游戏管理、今日进度、留言管理、用户管理（QQ 绑定 / 别名）、系统设置
  - 用户：我的代肝、今日进度、发送留言（仅已绑定游戏）
- 每日打卡状态自动重置（默认 04:00）

## 安装

```sh
nb plugin install commision_tracker
# 或
pip install -e .
```

在 `pyproject.toml` / NoneBot 配置中加入插件，并配置插件项（见下方「配置」）。

## 配置

在 NoneBot 环境变量或 `.env` 中设置：

| 配置项 | 说明 | 默认值 |
| --- | --- | --- |
| `commision_db_path` | 数据库路径 | `./data/commision_tracker.db` |
| `commision_tracker_host` | WebUI 监听地址 | `0.0.0.0` |
| `commision_tracker_port` | WebUI 端口 | `8080` |
| `commision_tracker_server_url` | 对外访问地址 | `http://localhost:8080` |
| `commision_tracker_jwt_secret` | WebUI JWT 签名密钥（务必改为强随机值） | 占位 |
| `commision_tracker_code_expire` | 登录验证码有效期（秒） | `300` |
| `commision_tracker_reset_hour` | 每日打卡重置时间（24 小时制） | `4` |

> 首次启动会生成一个随机的初始管理员密码并打印到控制台，请及时通过 WebUI 或 `/代肝管理员密码设置` 修改。

## BOT 指令

管理员：

```
/代肝创建 用户 名称
/代肝创建 游戏 名称 游戏组
/代肝游戏组创建 名称
/代肝游戏组改名 旧名 新名
/代肝游戏组删除 名称
/代肝游戏移入 游戏名 游戏组
/代肝添加 游戏组 用户名 次数
/代肝次数设置 用户名 游戏组 次数/+n/-n
/代肝添加游戏 用户名 游戏名
/代肝删除组 用户名 游戏组
/代肝别名添加 名称 别名
/代肝别名删除 名称 别名
/代肝绑定 用户名 QQ号/@用户
/代肝解绑 用户名
/代肝打卡 游戏名 用户名 [次数]
/代肝重置 用户名 游戏名
/代肝删除 用户名 游戏名
/代肝管理员密码设置 密码
```

用户：

```
/代肝列表
/进度查询
/代肝留言 游戏名 内容
/代肝登录
/代肝帮助
```

## 前端

WebUI 前端代码位于 [TickTracker-Web](https://github.com/LeiSureLyYrsc/TickTracker-Web)（Vue 3 + @material/web），构建产物输出到本插件 `webui/frontend` 目录，由插件内嵌 FastAPI 直接托管。

## 数据模型

- `users` / `user_aliases`：用户与别名
- `game_groups`：游戏组（应得次数按 用户×游戏组 记录）
- `games` / `game_aliases`：游戏与别名（归属游戏组）
- `group_commissions`：用户在游戏组下的应得次数
- `commissions`：用户在单个游戏下的已完成次数与今日打卡
- `messages` / `login_codes` / `auth_settings`：留言、登录验证码、管理员认证

## License

本项目仅供学习交流使用。
