# zlls-coop-keyboard

一套键盘控制多台机器：通过 P2P 将按键事件按配置转发到多台 Receiver，支持可配置的监听键、绑定（触发键 → 目标机器 + 按键 + 动作）与局域网 zeroconf 发现。

## 要求

- Python 3.12+
- 使用 [uv](https://docs.astral.sh/uv/) 或 pip 安装依赖

## 安装

```bash
uv sync
# 或
pip install -e .
```

## 快速开始

### 1. 启动 Receiver（被控端）

每台被控机运行一个 Receiver：

```bash
# 复制示例配置
cp config.receiver.example.yaml config.yaml
# 启动（可选 -c 指定配置、--port 指定端口）
uv run zlls-coop-keyboard-receiver
```

终端会输出本机 multiaddr，例如：`Receiver running (id='receiver123'). Connect Controller to: /ip4/.../tcp/端口/p2p/...`

- **receiver.identity.id**：机器标识，不填则用 hostname；Controller 的 bindings 里 `target` 填该值。
- **receiver.keyboard.enable**：`true` 时在本地模拟按键，`false` 时仅打印事件。

### 2. 启动 Controller（控制器端）

在控制键盘的那台机器上运行：

```bash
cp config.controller.example.yaml config.yaml
uv run zlls-coop-keyboard-controller
```

**两种连接方式：**

- **手动写 peers**：把 Receiver 输出的 multiaddr 填到 `controller.peers.<target>.address`，同机测试时把地址里的 `0.0.0.0` 改成 `127.0.0.1`。
- **zeroconf 发现**：`controller.discovery.enable: true` 且 `controller.discovery.type: zeroconf` 时，Controller 自动发现局域网内已注册的 Receiver，无需在 peers 里写 address；bindings 的 `target` 填 Receiver 的 `receiver.identity.id` 或 hostname 即可。

### 3. 配置 bindings

- **listen_keys**：只处理列表中的键（或 `"*"` 表示全部）；不配置则不监听任何键。
- **bindings**：`触发键` → `on: down/up/both` + `actions`。每条 action 为 `target`（机器标识）、`key`（要发送的键）、`event: down/up/both`。
- 触发键可带修饰：如 `"Ctrl+KeyA"`，匹配时要求当前修饰键一致。

示例：按 `a` 时向 `receiver123` 发送 `b` 的按下+抬起：

```yaml
controller:
  listen_keys: ["a", "b"]
  bindings:
    a:
      on: both
      actions:
        - target: receiver123
          key: b
          event: both
```

## 模块结构图

```text
zlls-coop-keybord
├─ CLI / 启动入口
│  ├─ controller_main.py
│  │  └─ Controller 端主程序
│  │     - 读取配置
│  │     - 启动本地键盘监听
│  │     - 按 bindings 执行动作
│  │     - 发送按键、命令或 HTTP 请求
│  ├─ receiver_main.py
│  │  └─ Receiver 端主程序
│  │     - 读取配置
│  │     - 启动 P2P 服务
│  │     - 接收 KeyEvent / ActionEvent
│  │     - 注入键盘或执行动作
│  └─ keyboard_listener_tool.py
│     └─ 独立键盘监听调试工具
│
├─ 核心业务模块
│  ├─ bindings.py
│  │  └─ 按键绑定规则引擎
│  │     - listen_keys
│  │     - 组合键匹配（如 Ctrl+KeyA）
│  │     - 生成 send_key / run_command / http_request 动作
│  ├─ keyboard_events.py
│  │  └─ 键盘事件处理
│  │     - 采集本地按键
│  │     - 规范化键名
│  │     - Receiver 端注入按键
│  ├─ protocol.py
│  │  └─ 网络消息模型
│  │     - KeyEvent
│  │     - ActionEvent
│  │     - JSON line 编码/解码
│  ├─ p2p.py
│  │  └─ P2P 通信封装
│  │     - 创建 host
│  │     - 建立连接 / stream
│  │     - 发送 key_event / action_event
│  │     - 注册 Receiver 处理器
│  ├─ targets.py
│  │  └─ 目标机器解析
│  │     - 根据 target 名称查地址
│  │     - 优先 peers
│  │     - 补充 discovery 结果
│  ├─ discovery.py
│  │  └─ 局域网发现
│  │     - Receiver 注册 zeroconf 服务
│  │     - Controller 浏览并维护发现表
│  └─ config.py
│     └─ 配置加载与校验
│        - 读取 config.yaml
│        - 判断 controller / receiver 模式
│
├─ 配置与文档
│  ├─ config.controller.example.yaml
│  ├─ config.receiver.example.yaml
│  ├─ README.md
│  └─ DESIGN.md
│
└─ 辅助脚本
   └─ scripts/
      - Windows 启动脚本
      - 静默启动脚本
```

### 运行关系图

```text
[你的键盘]
    │
    ▼
Controller (controller_main.py)
    │
    ├─ keyboard_events.py   采集按键
    ├─ bindings.py          判断该按键要触发什么动作
    ├─ targets.py           把 target 名称解析成地址
    ├─ discovery.py         自动发现局域网 Receiver
    └─ p2p.py               发送消息
            │
            ▼
      protocol.py
   (KeyEvent / ActionEvent)
            │
            ▼
Receiver (receiver_main.py)
    │
    ├─ 收到 KeyEvent     → keyboard_events.py 注入本机键盘
    ├─ 收到 run_command  → 本机执行命令
    └─ 收到 http_request → 本机发送 HTTP 请求
```

### 当前代码已支持的动作类型

- `send_key`：向指定 Receiver 发送按键事件。
- `run_command`：在 Controller 本机执行命令，或发到目标 Receiver 远端执行。
- `http_request`：在 Controller 本机发 HTTP 请求，或发到目标 Receiver 远端请求。

## 配置与命令行

- **默认配置**：当前目录 `config.yaml`；可用 `-c/--config` 指定文件。
- **Receiver**：`--port 端口`（默认 0 为随机端口）。
- **Controller**：`--port 端口`。

示例配置见 `config.receiver.example.yaml`、`config.controller.example.yaml`。

## 开机自启（Windows）

在 Windows 上可让 Controller 或 Receiver 在登录后自动启动，两种常用方式如下。

### 方式一：启动文件夹（推荐，操作简单）

1. **选脚本**（任选其一）  
   - 需要看到控制台窗口（方便看日志）：用 `scripts\start-controller.bat` 或 `scripts\start-receiver.bat`  
   - 不需要窗口（静默在后台跑）：用 `scripts\start-controller-silent.vbs` 或 `scripts\start-receiver-silent.vbs`

2. **创建快捷方式**  
   右键对应脚本 → “创建快捷方式”。

3. **放进启动文件夹**  
   - `Win + R` → 输入 `shell:startup` → 回车  
   - 把快捷方式复制（或移动）到这个文件夹里。

下次登录后，程序会自动运行。工作目录为项目根目录，会读取项目下的 `config.yaml`（或你通过参数 `-c` 指定的配置）。

### 方式二：任务计划程序（可精确控制触发时机）

1. **Win + R** → 输入 `taskschd.msc` → 回车，打开“任务计划程序”。
2. 右侧 **“创建基本任务”**：
   - 名称：如 `zlls-coop-keyboard-controller`
   - 触发器：**“当用户登录时”**
   - 操作：**“启动程序”**
   - 程序/脚本：填 **`uv`** 的完整路径（如 `C:\Users\你的用户名\.local\bin\uv.exe`，或用 `where uv` 在终端查）。
   - 添加参数：`run zlls-coop-keyboard-controller`（Receiver 则改为 `run zlls-coop-keyboard-receiver`）。
   - 起始于：项目根目录（如 `K:\UserFiles\Development\Projects\ZRC\zlls-coop-keybord`）。
3. 完成创建后，可在该任务属性里勾选“不管用户是否登录都要运行”等（按需）。

**注意**：开机自启前请在本机先配好 `config.yaml`（Controller 与 Receiver 各自对应 `config.controller.example.yaml` / `config.receiver.example.yaml` 的复制与修改），并确保 `uv` 在系统 PATH 中。

## 设计文档

详见 [DESIGN.md](DESIGN.md)。
