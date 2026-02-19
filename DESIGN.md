## 项目概要

本项目目标：使用 **一套键盘/控制器** 同时控制 **两台或多台机器**，并且：

- **可配置的一对一 / 一对多按键映射**（物理键 → 逻辑键集合）。
- **可配置“按下按键发送到哪台机器”**（每个键可指定目标机器集合）。
- **可配置监听哪些按键**，并在触发时执行**一系列操作**；每条操作明确：向哪台机器、发送什么键、什么动作（down/up/both）。
- **机器标识**：默认使用本机 hostname，支持在配置中自定义。

底层通信选型：使用 [py-libp2p](https://py-libp2p.readthedocs.io/en/stable/introduction.html) 提供的 P2P 网络与自定义协议，在控制器与各被控端之间建立加密的流式连接。

---

## 技术栈（确定）

| 类别 | 选型 | 说明 |
|------|------|------|
| **运行环境** | Python 3.12+ | 与现有 `pyproject.toml` 一致。 |
| **P2P 网络** | **libp2p**（PyPI: `libp2p`） | py-libp2p，TCP + Noise + 多路复用；自定义协议 `/zlls-coop-keyboard/1.0.0`，Stream 上发 JSON 行。 |
| **键盘采集与模拟** | **pynput**（PyPI: `pynput`） | Controller 端监听全局按键（`keyboard.Listener`），Receiver 端模拟按键（`keyboard.Controller`）；跨平台，与 Python 3.12 兼容。 |
| **配置格式与解析** | **YAML + PyYAML**（PyPI: `PyYAML`） | 配置文件使用 YAML；解析用 `PyYAML`（`pyyaml`），简单够用，无需保留注释与格式。 |
| **局域网发现** | **zeroconf**（PyPI: `zeroconf`） | mDNS 服务发现，Receiver 注册 `_zlls-coop-keyboard._tcp.local`，Controller 浏览并维护「机器标识 → multiaddr」表。（Milestone 5 接入。） |
| **日志** | 标准库 **logging** | 不引入额外日志库；统一用 `logging` 配置级别与输出。 |
| **并发模型** | **asyncio** | 主循环异步；libp2p、zeroconf 均为 async；pynput 在独立线程，通过 `asyncio.Queue` 与主循环衔接。详见「并发模型：整体采用异步」。 |

### 依赖汇总（pyproject.toml）

```toml
[project]
requires-python = ">=3.12"
dependencies = [
    "libp2p",
    "pynput",
    "PyYAML",
]
# zeroconf 在实现 Milestone 5 时加入：
# "zeroconf",
```

### 未选 / 备选

- **TOML**：设计示例与可读性以 YAML 为主，故首选 YAML；若后续需要可再支持 TOML 作为另一种格式。
- **ruamel.yaml**：需要保留注释或 round-trip 时再考虑；当前仅读配置，PyYAML 即可。

---

## 并发模型：整体采用异步（asyncio）

**结论：整体采用 asyncio 异步**，与依赖库一致，并避免单点阻塞。

### 原因

| 组件 | 说明 |
|------|------|
| **py-libp2p** | API 为 **async**（`host.run()`、`set_stream_handler` 的 handler、`dial`、`open_stream`、`stream.read/write` 等均为 async）。以 asyncio 为主循环最自然。 |
| **zeroconf** | 提供 **AsyncZeroconf**、**AsyncServiceBrowser**，可与 asyncio 同一事件循环，无需另起线程。 |
| **pynput** | 为**同步**、基于线程的 API（`Listener(callback)`）。不阻塞主循环的做法：在**单独线程**中跑 pynput，将按键事件放入 **asyncio.Queue**，主循环从队列取事件再异步发给各 peer。 |
| **发送多目标** | 向多台机器发同一按键时，用 `asyncio.gather` 并发发送，一台慢或不可达不会阻塞其他。 |

### 实现要点

- **主入口**：`asyncio.run(main())`，主协程内启动 libp2p Host、zeroconf（若启用）、以及一个“消费按键队列并按 bindings 发送”的协程。
- **键盘输入**：单独线程运行 `pynput.keyboard.Listener(on_press=..., on_release=...)`，回调里将事件放入 `asyncio.Queue`（可用 `loop.call_soon_threadsafe(queue.put_nowait, event)`）。
- **Receiver**：stream handler 为 `async def`，内层 `await stream.read()` 读 JSON 行，解析后调用 pynput 注入（同步调用，耗时短，可接受；若需可 `run_in_executor`）。
- **zeroconf**：使用 `AsyncZeroconf`、`AsyncServiceBrowser`，在同一个 asyncio 循环中做发现与更新「机器标识 → multiaddr」表。

### 小结

- **整体异步**：主逻辑跑在 asyncio 上，libp2p 与 zeroconf 原生适配。
- **唯一同步桥**：pynput 在独立线程，通过 **asyncio.Queue** 把按键事件交给异步侧，避免阻塞主循环。

---

## 机器标识（Hostname + 可自定义）

- **默认**：每台机器的标识符使用**本机 hostname**（如 `DESKTOP-ABC123`、`dev-server-01`）。
- **可自定义**：在配置中可为该机指定一个固定 id，覆盖 hostname。
  - **Receiver 端**：在 `receiver.identity.id` 中配置；若不配置则运行时使用 `socket.gethostname()`。
  - **Controller 端**：peers 的 key 即为机器标识（hostname 或 id），value 中为 address 等；连接建立后可从 Receiver 侧获取其上报的 id，用于日志与界面展示。
- **在操作中的使用**：向某台机器发送操作时，**直接写该机器的标识**（hostname 或 Receiver 配置的 `identity.id`），例如 `target: DESKTOP-ABC123`、`target: my-laptop`。无需额外“别名”；机器列表（peers）为可选，若配置则其 key 即为该机器标识（hostname/id），value 为 address 等信息。

---

## 监听键与操作序列（核心行为模型）

控制器**只对配置中指定的按键**进行监听；当这些键发生指定动作（按下/抬起）时，执行**一系列操作**。

### 监听键（listen_keys）

- **配置项**：`controller.listen_keys`（**可选**）。
  - 取值：
    - **列表**：仅监听列出的键，如 `["KeyF1", "KeyF2", "KeyA"]`。未在列表中的键**不触发任何规则**。
    - **`"*"`**：监听所有键；是否产生网络发送仍由各键的 **bindings** 决定（无 binding 的键可不发）。
- **已约定**：若**未配置** `listen_keys`，则**不监听任何键**（等价于空列表）；只有显式列入 listen_keys 或配置为 `"*"` 的键才会被处理。

### 操作序列（bindings）

- **配置项**：`controller.bindings`，结构为「触发键（可带修饰键）→ 操作列表」。
- **触发条件**：每个 binding 可指定在何时触发：
  - `on: "down"` — 仅在该键**按下**时执行；
  - `on: "up"` — 仅在该键**抬起**时执行；
  - `on: "both"` — **已约定**：按下时执行一次该条目的操作列表，抬起时再执行一次该操作列表（共两轮）。
- **每个操作（action）** 包含三要素：
  1. **target**：目标机器标识，**直接写 hostname 或该机 Receiver 配置的 `identity.id`**（如 `DESKTOP-ABC123`、`my-laptop`），表示“向哪台机器发送”。Controller 通过 peers 或发现机制用该标识解析出连接地址。
  2. **key**：要发送的**逻辑键名**（如 `"a"`、`"KeyF1"`、`"enter"`），即“发送什么键”。
  3. **event**：要发送的**动作类型**（`"down"` | `"up"` | `"both"`），即“什么操作”；`"both"` 表示先发 down 再发 up。

**触发键的配置格式**（用于 bindings 的 key）：

- 仅主键：`"KeyF1"`、`"KeyA"`、`"enter"` 等。
- 带修饰键：`"Ctrl+KeyA"`、`"Alt+Shift+KeyF1"` 等，约定顺序可为 `Ctrl`、`Alt`、`Shift`、`Meta`（或 Win）+ 主键，大小写不敏感。匹配时要求当前按下修饰键与配置一致。

**单条操作（Action）结构**（配置与内存一致）：

| 字段    | 含义                             | 示例        |
|---------|----------------------------------|-------------|
| `target` | 目标机器标识（hostname 或 id）   | `"DESKTOP-ABC123"`、`"my-laptop"` |
| `key`    | 要发送的逻辑键                   | `"a"`、`"KeyF1"` |
| `event`  | 动作类型                         | `"down"` \| `"up"` \| `"both"` |

因此，整体流程为：

1. 用户按下/抬起某个键 → 若该键不在 listen_keys 内（或 listen_keys 未配置）且 listen_keys ≠ `"*"` 则忽略。
2. 若在 listen_keys 内，则查 bindings，找到与该键（及当前修饰键）匹配的条目。
3. 按顺序执行该条目下的 **actions**：对每个 action，向 **target** 机器发送一次 **key** 的 **event**（若 event 为 `both` 则发送 down 再发送 up）。

这样即满足：「可配置监听哪个按键 → 执行一系列操作 → 每条操作 = 向哪台机器 + 发送什么键 + 什么操作」。

---

## 系统角色与整体架构

### 角色

- **Controller（控制器端）**
  - 连接物理键盘，监听本地键盘事件。
  - 按配置对按键做一对一 / 一对多映射。
  - 根据配置决定每次按键要发送到哪些目标机器。
  - 通过 py-libp2p 将“按键事件”转发给多个 Receiver。

- **Receiver（接收端）**
  - 运行在每台被控机器上。
  - 通过 py-libp2p 监听自定义协议，接收来自 Controller 的按键事件。
  - 在本机模拟按键（和/或鼠标）输入，作用到当前焦点窗口。

### 通信与协议

- **网络栈**：py-libp2p
  - 使用 TCP 传输，Noise 安全协议，加上连接多路复用。
  - 每台机器运行一个 libp2p Host，拥有 Peer ID 和若干 multiaddr。
- **自定义协议 ID（示例）**：`/zlls-coop-keyboard/1.0.0`
  - Receiver 通过 `set_stream_handler` 在该协议上注册处理函数。
  - Controller 通过 `dial` + `open_stream` 打开到 Receiver 的流，发送按键事件。

### 消息模型（按键事件）

统一使用 JSON 文本行（每个事件一行），便于调试与扩展，例如：

```json
{
  "type": "key_event",
  "event": "down",          // "down" | "up"
  "key": "a",               // 标准化后的键位名，如 "a"、"enter"、"shift"
  "modifiers": ["ctrl"],    // 可选修饰键列表
  "timestamp": 1739999999,  // 可选，Unix 秒或毫秒
  "sequence": 42            // 可选，自增序列号
}
```

后续可扩展：

- 鼠标事件（move/click/scroll）。
- 特殊控制命令（如切换目标机器、心跳、配置下发等）。

---

## 模块划分

项目将按“核心逻辑”和“可执行入口”拆分模块。

### 1. 核心模块

#### 1.1 `keyboard_events` 模块

**职责**：本地键盘事件采集与模拟。

- `keyboard_events.capture`（控制器端）
  - 基于 `pynput` 在**独立线程**中监听全局键盘按下/抬起事件，将事件转换为统一内部表示 `KeyEvent`，并通过 **asyncio.Queue** 投递到主异步循环（见并发模型）。

- `keyboard_events.inject`（接收端）
  - 统一使用 **pynput**（`keyboard.Controller`）做按键模拟，与 Controller 端技术栈一致；若某平台 pynput 不可用再考虑 pyautogui 等备选。
  - 将 `KeyEvent` 转换为本地 key_down/key_up 调用；兼容主流平台（Windows 优先）。

#### 1.2 `bindings` 模块（监听键 + 操作序列）

**职责**：根据配置决定「监听哪些键」以及「触发后执行的操作序列」。

- 载入配置：
  - `controller.listen_keys`：监听的键列表或 `"*"`。
  - `controller.bindings`：触发键（可带修饰键）→ 触发时机（on: down/up/both）→ 操作列表。
- 每条 **action** 结构：`{ target: 机器标识（hostname 或 id）, key: 逻辑键名, event: "down"|"up"|"both" }`。
- 提供接口：
  - `should_handle(key, modifiers) -> bool`：当前键是否在 listen_keys 内、是否需要处理。
  - `get_actions(trigger_key, modifiers, key_event_type: "down"|"up") -> list[Action]`：返回该次触发要执行的所有 action（每个 action 含 target、key、event）。
- **匹配顺序（已约定）**：先匹配「修饰键+触发键」精确项（如 `Ctrl+KeyA`）；若无匹配再匹配「仅触发键」的条目（如 `KeyA`）。同一触发键最多命中一条。

#### 1.3 `mapping` 模块（可选，首版不做）

**职责**：若保留「透传式」映射（不按操作序列，而是按键直接转发），可用 mapping 做一对一/一对多键位映射。

- **首版约定**：只实现 **bindings**，不实现 mapping；所有「向远程发键」都通过 bindings 的操作序列完成。mapping 留作后续扩展（如需要「按 A 直接透传为 B 到多机」再加）。

#### 1.4 `targets` 模块

**职责**：按「机器标识（hostname 或 id）」解析 target，得到连接/地址，供 bindings 与发送逻辑使用。

- 载入可选的 `controller.peers`：key 为**机器标识**（hostname 或 Receiver 的 `identity.id`），value 至少含 `address`（multiaddr），可选其他字段。
- 提供：根据 action 中的 `target`（即机器标识）解析出 address/连接。**解析来源**：优先从 `controller.peers` 中查；若启用局域网发现，则也可从发现结果（hostname/id → multiaddr 缓存）中查。未解析到则记录日志并跳过该 action。

#### 1.5 `p2p` 模块（基于 py-libp2p）

**职责**：封装 Host、连接、流与协议。

- 公共部分：
  - `start_host(config) -> HostContext`
    - 初始化 py-libp2p Host（TCP、Noise、多路复用）。
    - 监听指定端口或随机端口。
    - 返回包含 `peer_id`、监听地址列表的上下文对象。

- 控制器端子模块：
  - 管理与多台 Receiver 的连接：
    - 维护「机器标识（hostname/id）→ connection」映射（或 peer_id → connection，由 targets 按 target 解析出 peer/连接）。
    - 支持自动重连、失败重试（后续扩展）。
  - 对外暴露发送 API：
    - `send_key_event(target: Target, event: KeyEvent)`
    - 内部负责 `dial`（如未连接）+ `open_stream`（可长期保持）+ 写入 JSON 行。

- 接收端子模块：
  - `register_keyboard_protocol(handler)`：
    - 对 py-libp2p Host 调用 `set_stream_handler(protocol_id, handler)`；handler 为 **async**。
    - handler 内 `await stream.read()` 按行读取 JSON，解析为 `KeyEvent` 后交给 `keyboard_events.inject`（同步调用，耗时短）。

#### 1.6 `config` 模块

**职责**：加载与校验配置文件。

- 使用 **YAML** 格式，**PyYAML** 解析（见技术栈）。
- 抽象出统一配置对象，例如：

```yaml
mode: controller  # 或 receiver

controller:
  # 只监听以下键；设为 "*" 表示监听所有键（是否发送仍由 bindings 决定）
  listen_keys: ["KeyF1", "KeyF2", "KeyA", "KeyB"]

  # 触发键 → 操作序列（target 直接写机器标识：hostname 或 Receiver 的 identity.id）
  bindings:
    KeyF1:
      on: "down"
      actions:
        - target: DESKTOP-ABC123    # hostname
          key: "KeyF1"
          event: "down"
        - target: my-laptop         # 该机 Receiver 配置的 identity.id
          key: "KeyF2"
          event: "down"
    "Ctrl+KeyA":
      on: "down"
      actions:
        - target: DESKTOP-ABC123
          key: "a"
          event: "both"
    KeyF2:
      on: "up"
      actions:
        - target: my-laptop
          key: "KeyF2"
          event: "up"

  # 机器列表（可选）：key 为机器标识（hostname 或 id），value 为地址等；不配置或为空则不会向任何机器发送
  peers:
    DESKTOP-ABC123:
      address: "/ip4/192.168.1.10/tcp/8000/p2p/XXXX"
    my-laptop:
      address: "/ip4/192.168.1.11/tcp/8000/p2p/YYYY"

receiver:
  # 本机标识：不配置则使用 hostname
  identity:
    id: "my-custom-name"   # 可选，默认 socket.gethostname()
  keyboard:
    enable: true
```

- **机器列表（peers）为可选**：
  - 在 **controller 模式**下，`controller.peers` 为**可选配置**。不配置或为空时，是否能向远程机器发送取决于是否启用了 zeroconf 等发现机制。
  - 操作中的 `target` **直接写机器标识**（hostname 或 Receiver 的 `identity.id`）。若配置了 peers，则 peers 的 **key 即为该机器标识**，用于根据 target 查地址。
  - 若某条 action 的 `target` 在运行时在 peers 中查不到，且**发现缓存中也查不到对应 multiaddr**，则**本次请求不发送**（只记录日志）；即「peers 没有 + 网络发现没有」时直接跳过该 action。
  - **receiver 模式**下不需要 peers。

---

## 局域网发现（LAN Discovery）— 使用 zeroconf

使用 **zeroconf** 库（Python 的 [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf)，
PyPI 包名 `zeroconf`）实现 mDNS 服务发现，这样多数情况下**不必在 peers 里手写每台机器的 address**；Controller 通过发现得到「机器标识（hostname/id）→ multiaddr」的映射，再按 target 发按键。

### 思路

- **Receiver**：在局域网内通过 zeroconf **注册**一个 mDNS 服务，声明自己的「机器标识 + 监听端口 + multiaddr（或 peer_id）」。
- **Controller**：通过 zeroconf **浏览**该类型服务，得到「target（hostname/id）→ address」表；执行 action 时用 target 查该表即可拨号发送，无需在配置里写 peers。

### 服务类型与约定

- **服务类型**：`_zlls-coop-keyboard._tcp.local`（固定，便于 Controller 只发现本项目的 Receiver）。
- **服务名（必须唯一）**：使用**机器标识**（hostname 或 `receiver.identity.id`）作为前缀，若在 LAN 内检测到冲突，则由 Receiver 在其基础上**追加唯一后缀**（例如 `-<port>` 或随机短串），最终保证服务名在同一 LAN 内全局唯一。
- **端口**：与 py-libp2p Host 监听的 TCP 端口一致。
- **TXT 记录**（可选但推荐）：存放 `multiaddr` 或 `peer_id`，便于 Controller 直接拼出完整 multiaddr（`/ip4/<解析到的 IP>/tcp/<port>/p2p/<peer_id>`），避免只依赖端口。

### 基于 zeroconf 的实现要点

- **Receiver 端**：
  - Host 启动并得到监听地址（IP、port）和 Peer ID 后，构造 `ServiceInfo`：`type_="_zlls-coop-keyboard._tcp.local"`，`name="<机器标识>._zlls-coop-keyboard._tcp.local"`，`port=...`，`properties={"multiaddr": "<完整 multiaddr>"}` 或 `peer_id=...`。
  - 使用 `Zeroconf`（或 `AsyncZeroconf`）调用 `register_service(ServiceInfo)` 注册；进程退出时 `unregister_service`。
- **Controller 端**：
  - 使用 `ServiceBrowser` 浏览 `_zlls-coop-keyboard._tcp.local`；在 `add_service` 回调中解析服务名得到机器标识，通过 `zeroconf.get_service_info` 得到 IP、port 及 TXT 中的 multiaddr（若有则直接用，否则用 IP+port+peer_id 拼 multiaddr）。
  - 维护「机器标识 → multiaddr」的发现表（可带 TTL 或随服务移除更新）；`targets` 模块解析 target 时优先查 peers，再查该发现表。

### 依赖

- 项目依赖中增加 **`zeroconf`**（Python 3 的 mDNS 实现），与 `libp2p`、`pynput` 等一并安装。

### 与配置的关系

- **启用局域网发现时**：在配置中设置 `controller.discovery.enable: true`（或 `type: "zeroconf"`）；`controller.peers` 可为空或不配置，target 的 address 由 zeroconf 发现结果提供。若某台机器同时出现在 peers 里，约定**以 peers 中的配置为准**，发现结果仅作补充。
- **未启用或发现不到时**：仍依赖 `controller.peers` 中手写的 address。

示例配置片段：

```yaml
controller:
  discovery:
    enable: true
    type: "zeroconf"
  # peers 可选；启用发现后可不写
  # peers: { ... }
```

### 小结

使用 **zeroconf** 做局域网发现后，只需在操作里写 `target: hostname` 或 `target: my-laptop`，无需为每台被控机配置 address；适合同一局域网内的键盘协同场景。

---

- `config.load(path) -> Config`，并做基本校验：
  - mode 是否为 `controller` 或 `receiver`。
  - 若 mode 为 controller 且 peers 已配置：校验 bindings 中出现的 `target` 是否均存在于 `controller.peers` 的 key 中；**不存在时打警告、不阻断启动**（因可能依赖 discovery 后续解析）。
  - `listen_keys` 若为列表，则需为合法键名列表。

- **配置文件路径约定**：
  - 默认配置文件名为 `config.yaml`，位于当前工作目录。
  - CLI 支持通过 `-c/--config` 显式指定路径；当参数与默认文件同时存在时，以 CLI 参数为准。

---

### 2. 可执行入口

#### 2.1 `zlls_coop_keyboard_controller`（控制器 CLI）

功能流程：

1. 启动时读取配置（`config.load`）。
2. 启动 py-libp2p Host（`p2p.start_host`），可打印自己的 multiaddr 做调试。
3. 若配置了 `config.controller.peers`，则按需连接其中列出的机器（key = 机器标识 → address）。
4. 启动键盘监听（`keyboard_events.capture`）。
5. 对每个捕获的 `KeyEvent`（key, modifiers, event_type: down/up）：
   - 若 `bindings.should_handle(key, modifiers)` 为假（该键不在 listen_keys 或未配置 binding），则忽略。
   - 否则调用 `bindings.get_actions(trigger_key, modifiers, key_event_type)` 得到本次要执行的 **actions**。
   - 对每个 action（target = 机器标识 hostname/id、key、event）：
     - 通过 `targets` 用 target 解析出连接（从 peers 或发现缓存）；
     - 若解析成功，调用 `p2p.send_key_event(target, key, event)` 发送（若 event 为 `both` 则先发 down 再发 up）；若解析失败则记录日志并跳过。

边界处理：
- 若某 target 不可达，可选择忽略并记录日志，或在一定重试次数后临时禁用该目标。

#### 2.2 `zlls_coop_keyboard_receiver`（接收端 CLI）

功能流程：

1. 启动时读取配置（`config.load`）。
2. 确定本机标识：`receiver.identity.id` 若已配置则使用，否则使用 `socket.gethostname()`。
3. 启动 py-libp2p Host，输出自身 multiaddr 及本机 id，供 Controller 配置与识别。
4. 注册 `/zlls-coop-keyboard/1.0.0` 协议的 stream handler：
   - 在 handler 中循环读取 JSON 行。
   - 解析为 `KeyEvent` 后，调用 `keyboard_events.inject` 在本地模拟按键。

扩展点：
- 连接建立时可向 Controller 上报本机 id（hostname 或自定义），便于 Controller 日志与展示。
- 日志中记录来自哪个 Peer 的事件。
- 简单的鉴权（如约定 token，放入事件中 or 另一路握手）。

---

## 配置与使用场景示例

### 场景 1：操作中直接写 hostname / id，按 F1 向两台机器发不同键

- 操作中的 `target` 直接写机器标识：一台为 hostname（如 `DESKTOP-ABC123`），一台为 Receiver 自定义 id（如 `my-laptop`）。
- 按 F1 时：向 `DESKTOP-ABC123` 发送 F1 down，向 `my-laptop` 发送 F2 down。
- peers 为可选；若配置则 key 与 target 一致（hostname 或 id）。

```yaml
mode: controller

controller:
  listen_keys: ["KeyF1", "KeyF2", "KeyA"]
  bindings:
    KeyF1:
      on: "down"
      actions:
        - target: DESKTOP-ABC123
          key: "KeyF1"
          event: "down"
        - target: my-laptop
          key: "KeyF2"
          event: "down"
    "Ctrl+KeyA":
      on: "down"
      actions:
        - target: DESKTOP-ABC123
          key: "a"
          event: "both"
  # 可选：机器列表 key = 机器标识（hostname 或 id）
  peers:
    DESKTOP-ABC123:
      address: "/ip4/192.168.1.10/tcp/8000/p2p/..."
    my-laptop:
      address: "/ip4/192.168.1.11/tcp/8000/p2p/..."
```

### 场景 2：机器标识用 hostname 或自定义 id

- 被控机 1 不配置 `receiver.identity.id`，则其标识为 hostname（如 `DESKTOP-ABC123`）；在操作中写 `target: DESKTOP-ABC123`。
- 被控机 2 配置 `receiver.identity.id: "my-laptop"`；在操作中写 `target: my-laptop`。
- Controller 的 peers（若配置）用同一套标识作为 key，连接后可在日志中看到对端上报的 id。

Receiver 配置（机器 1，用 hostname）：
```yaml
mode: receiver
receiver:
  keyboard:
    enable: true
  # 不配置 identity.id，使用 hostname
```

Receiver 配置（机器 2，自定义 id）：
```yaml
mode: receiver
receiver:
  identity:
    id: "laptop"
  keyboard:
    enable: true
```

---

## 未确认与待定（清单）

以下为设计里曾模糊或可选的点，**已在本节或前文约定**；实现时按此执行即可。

| 项 | 约定 |
|----|------|
| `on: "both"` 语义 | 按下时执行一次操作列表，抬起时再执行一次（共两轮）。 |
| 未配置 `listen_keys` | **默认不监听**任何键（等价于空列表）；只有显式配置 listen_keys 或 `"*"` 才处理。 |
| bindings 匹配顺序 | 先匹配「修饰+键」精确项，再匹配「仅键」；同键只命中一条。 |
| Receiver 键盘模拟 | 统一用 **pynput**，与 Controller 一致；仅在某平台不可用时再考虑 pyautogui。 |
| mapping 模块 | **首版不实现**，只做 bindings；mapping 留作后续扩展。 |
| target 不在 peers 时校验 | **警告**，不阻断启动（允许依赖 discovery）。 |
| 默认配置文件路径 | **实现时定**：首版可仅支持 CLI 参数 `-c/--config` 指定路径，无默认；或约定默认 `./config.yaml`。 |
| P2P 监听端口 | **实现时定**：配置项如 `controller.p2p.port`、`receiver.p2p.port`，未配置则随机并打印。 |
| zeroconf 服务名冲突 | 同一 LAN 内机器标识（hostname/id）宜唯一；若冲突可由 Receiver 在 id 后加后缀（如端口）保证服务名唯一。 |

若后续有新的未决项，可在此表补充一行「项 + 约定」。

---

## 开发里程碑与优先级

### Milestone 1：最小可用 Demo

- [x] 加入依赖（见技术栈）：`libp2p`、`pynput`、`PyYAML`。（zeroconf 在 Milestone 5 加入。）
- [x] 实现 Receiver 端：
  - [x] 启动 py-libp2p Host，输出 multiaddr。
  - [x] 注册协议 `/zlls-coop-keyboard/1.0.0` 的 stream handler。
  - [x] 将收到的简单 JSON 事件（含 key、event）打印到控制台。
- [x] 实现 Controller 端：
  - [x] 启动 py-libp2p Host。
  - [x] 通过配置里的 multiaddr 连接单个 Receiver。
  - [x] 监听键盘，将按键事件简单转成 JSON 并通过 stream 发到 Receiver。
  - [x] Receiver 控制台能实时打印事件。

### Milestone 2：本地键盘模拟

- [x] 在 Receiver 端集成 `keyboard_events.inject`，将接收到的 key_down/key_up 事件映射为本地按键模拟。
- [x] 在同一台机器上自测（Controller 与 Receiver 分别为两个进程），验证基础流程。

### Milestone 3：映射与多目标

- [ ] 实现 `mapping` 模块与配置。（首版约定仅实现 bindings，mapping 留作后续扩展。）
- [x] 实现 `bindings` 模块（listen_keys、bindings、should_handle、get_actions）与 `targets` 模块（从 controller.peers 解析 target → address）。
- [x] Controller 支持多台 Receiver，同一事件按 bindings 的 actions 可发送到多台；修饰键追踪并参与 bindings 匹配。

### Milestone 4：健壮性与体验

- [ ] 日志与错误处理（连接失败、重试策略）。
- [ ] 简单的命令行参数（如选择配置文件路径、显示本机 multiaddr 等）。
- [ ] 文档：在 `README.md` 中加入使用说明与示例配置。

### Milestone 5（可选）：局域网发现（zeroconf）

- [ ] 增加依赖 **zeroconf**（PyPI: `zeroconf`）。
- [ ] Receiver 端：Host 启动后使用 zeroconf 注册 mDNS 服务（类型 `_zlls-coop-keyboard._tcp.local`，服务名为机器标识，TXT 中带 multiaddr）；退出时反注册。
- [ ] Controller 端：使用 zeroconf 的 ServiceBrowser 浏览上述服务类型，在回调中维护「机器标识 → multiaddr」发现表；`targets` 解析时优先 peers，再查发现表。
- [ ] 配置项 `controller.discovery.enable`、`controller.discovery.type: "zeroconf"`；与 peers 并存时以 peers 为准。

---

## 后续可选扩展

- **局域网发现**：已选定 **zeroconf** 实现，见上文「局域网发现（LAN Discovery）— 使用 zeroconf」；实现后 peers 可不再手写 address。
- **认证与访问控制**：在应用层实现简单签名或 token，防止未授权控制。
- **GUI 管理界面**：可视化地添加机器、编辑映射规则、查看连接状态。
- **支持鼠标与剪贴板同步**：在现有键盘协议之上，增加更多事件类型。

---

## 总结

本设计将系统拆分为：

- 键盘采集/注入（`keyboard_events`）
- **监听键 + 操作序列**（`bindings`）：可配置监听哪些键，触发后执行「向哪台机器、发送什么键、什么操作（down/up/both）」的列表
- 可选透传映射（`mapping`）
- 机器标识与连接解析（`targets`）
- 基于 py-libp2p 的 P2P 通信封装（`p2p`）
- 配置加载与校验（`config`）
- 控制器 / 接收端两个 CLI 入口

满足：

- **机器标识**：默认 hostname，可配置自定义 id（Receiver 端 `receiver.identity.id`）。**机器列表（peers）为可选**；操作中的 **target 直接写 hostname 或 id**，无需额外别名。
- **可配置监听哪些按键**（`controller.listen_keys`）。
- **按键触发的一系列操作**：每条操作 = 向哪台机器（target，即 hostname/id）+ 发送什么键（key）+ 什么操作（event: down/up/both）。
- 使用 py-libp2p 在多机之间构建安全、可扩展的 P2P 控制通道。

