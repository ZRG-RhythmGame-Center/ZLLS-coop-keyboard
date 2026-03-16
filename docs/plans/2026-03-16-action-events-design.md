# 键盘绑定扩展：ActionEvent 设计

**Goal:**  
在现有 zlls-coop-keybord 的基础上，扩展「按键绑定」能力，使单次按键可以触发多种类型的操作：  
- 继续支持现有的跨机发键 (`send_key`)；  
- 新增「执行命令」(`run_command`) 与「发送 HTTP 请求」(`http_request`)，可在本机（Controller）或指定 Receiver 端执行。

---

## 一、配置设计

沿用现有 `controller.bindings` 结构，在 `actions` 中增加 `type` 与通用字段：

```yaml
controller:
  listen_keys: ["f13"]
  bindings:
    "F13":
      on: down
      actions:
        # 1) 兼容原有发键（不写 type 默认为 send_key）
        - type: "send_key"
          target: "pc1"
          key: "KeyF5"
          event: "both"        # down | up | both

        # 2) 在 Controller 本机执行命令
        - type: "run_command"
          where: "local"       # local | target（默认 local）
          command: "C:\\path\\to\\app.exe"
          args: []             # 可选，list[str]
          cwd: "C:\\path\\to"  # 可选
          shell: false         # 可选，默认 false

        # 3) 在指定 target Receiver 上执行命令
        - type: "run_command"
          where: "target"
          target: "pc2"
          command: "/usr/bin/app"
          args: ["--foo", "bar"]

        # 4) 在 Controller 本机发送 HTTP 请求
        - type: "http_request"
          where: "local"
          method: "POST"       # 默认 GET
          url: "http://127.0.0.1:8080/api/do_something"
          headers:
            X-Token: "abc"
          body: '{"foo": "bar"}'

        # 5) 在指定 target Receiver 上发送 HTTP 请求
        - type: "http_request"
          where: "target"
          target: "pc2"
          method: "GET"
          url: "http://localhost:9000/open_app"
```

约定：

- `type` 缺省时视为 `"send_key"`，以兼容旧配置。
- `where` 缺省时视为 `"local"`。
- `target`：
  - 对于 `send_key`：沿用现有含义，通过 `targets.resolve` 解析到 multiaddr。
  - 对于 `run_command` / `http_request`：
    - `where == "local"`：忽略 `target`，在 Controller 本机执行；
    - `where == "target"`：必须提供 `target`，通过 `targets.resolve` 找到 Receiver 地址并通过 p2p 发送 `ActionEvent`。

---

## 二、协议扩展：ActionEvent

现有协议只定义了 `KeyEvent`：

```json
{"type": "key_event", "event": "down", "key": "KeyF1", "modifiers": null}
```

在此基础上新增 `ActionEvent`，统一通过同一 libp2p 流发送 JSON 行，区分方式为 `type` 字段：

```json
{
  "type": "action_event",
  "kind": "run_command",             // run_command | http_request
  "payload": {
    "command": "/usr/bin/app",
    "args": ["--foo", "bar"],
    "cwd": null,
    "shell": false
  }
}
```

或：

```json
{
  "type": "action_event",
  "kind": "http_request",
  "payload": {
    "method": "POST",
    "url": "http://localhost:9000/api",
    "headers": {"X-Token": "abc"},
    "body": "{\"foo\":\"bar\"}"
  }
}
```

约定：

- `KeyEvent` 保持现在的结构不变，仅通过 `type == "key_event"` 与 Action 区分。
- `ActionEvent` 不关心来源键值，只描述要执行的动作；按键到 `ActionEvent` 的映射由 Controller 端的 `Bindings` 决定。

---

## 三、Controller 侧逻辑

### 3.1 Bindings 解析

`bindings.py` 中的 `Action` 数据类扩展：

- 增加 `type: str` 字段，支持：
  - `"send_key"`
  - `"run_command"`
  - `"http_request"`
- 增加一个通用 `payload: dict[str, Any]`，根据 `type` 填入对应字段：
  - 对于 `send_key`：现有 `target` / `key` / `event` 直接保留，`payload` 可选。
  - 对于 `run_command`：`where`、`command`、`args`、`cwd`、`shell`。
  - 对于 `http_request`：`where`、`method`、`url`、`headers`、`body`。

`Bindings.get_actions()` 继续返回 `list[Action]`，Controller 主循环按 `Action.type` 分派。

### 3.2 主循环执行策略

在 `controller_main._async_main()` 主循环中，从 `Bindings` 拿到的 `actions` 需要按类型处理：

- `send_key`：
  - 与现在一致：解析 `target` -> multiaddr；
  - 组装 `KeyEvent` 列表；
  - 调用 `connect_and_send_key_event()` 发送。

- `run_command`：
  - 若 `where == "local"`：
    - 使用 `trio.to_thread.run_sync()` 包装 `subprocess.Popen` / `subprocess.run` 执行命令；
    - 不等待进程结束（或采用短超时），仅记录启动/失败日志。
  - 若 `where == "target"`：
    - 按 `target` 解析出 multiaddr；
    - 组装 `ActionEvent(kind="run_command", payload=...)`；
    - 调用新函数 `connect_and_send_action_event()` 通过 p2p 发给 Receiver。

- `http_request`：
  - 若 `where == "local"`：
    - 使用 `httpx.AsyncClient` 发送请求；
    - 使用固定超时（例如 5 秒），不做自动重试；
    - 失败时记录 `warning` 日志，但不中断其它动作。
  - 若 `where == "target"`：
    - 同样解析 `target` 为 multiaddr；
    - 组装 `ActionEvent(kind="http_request", payload=...)`；
    - 使用 `connect_and_send_action_event()` 发送。

为避免阻塞主循环，可以对本地命令 / HTTP 在 Trio 并发中起任务，但首次实现可先串行执行，保证逻辑简单。

---

## 四、Receiver 侧逻辑

Receiver 当前的 stream handler 只处理 `KeyEvent`：

1. 从 `read_json_lines()` 读一行 bytes；
2. 尝试用 `KeyEvent.from_json_line()` 解析；
3. 成功则根据 `keyboard_enabled` 决定是否注入按键，否则打印日志。

扩展后：

- 在 `protocol.py` 中新增 `ActionEvent` 类与 `from_json_line()`；
- Receiver 的 stream handler 流程改为：

```python
async for line in read_json_lines(stream):
    if KeyEvent.from_json_line(line) as ev:
        ...  # 原逻辑
        continue
    if ActionEvent.from_json_line(line) as act:
        await handle_action_event(act)
        continue
    logger.debug("ignored line: %s", line[:80])
```

其中 `handle_action_event(act)` 根据 `act.kind` 分派：

- `run_command`：
  - 使用 `subprocess` 在 Receiver 本机执行；
  - 与 Controller 一致的参数含义；
  - 日志记录命令、返回码/异常。

- `http_request`：
  - 使用 `httpx.AsyncClient` 发送 HTTP 请求；
  - 同样使用固定超时、无重试；
  - 日志记录 URL、状态码/异常。

安全上假设 Receiver 与 Controller 共用同一配置源，仅用于受信环境内的自动化控制。

---

## 五、P2P 层扩展

`p2p.py` 目前只提供：

- `register_keyboard_handler(host, async_handler)`
- `read_json_lines(stream)`
- `connect_and_send_key_event(host, addr, KeyEvent, stream_cache)`

扩展内容：

- 保持同一个 `PROTOCOL_ID`，只是在协议层区分 `KeyEvent` 与 `ActionEvent`。
- 新增：

```python
async def _write_action_event_to_stream(stream, action_event: ActionEvent) -> None:
    ...

async def connect_and_send_action_event(
    host,
    multiaddr_str: str,
    action_event: ActionEvent,
    stream_cache: dict[str, Any] | None = None,
) -> None:
    # 逻辑基本照抄 connect_and_send_key_event，只是写入 ActionEvent
```

这样可以与现有的 stream 复用逻辑共享实现，保证对同一 peer 的多次操作不会频繁 new_stream。

---

## 六、HTTP 实现细节（httpx）

- 使用 `httpx.AsyncClient`：

```python
async with httpx.AsyncClient(timeout=5.0) as client:
    resp = await client.request(
        method=method,
        url=url,
        headers=headers or None,
        content=body if body is not None else None,
    )
```

- 不做自动重试；
- 对非 2xx 状态码：
  - 记录 `warning` 日志（包括状态码与部分响应内容），不抛出异常；
- 对网络异常：
  - 捕获 `httpx.RequestError`，记录 `warning`，不中断其他动作执行。

---

## 七、测试与示例

1. 编写一个简单示例配置，绑定某个功能键（如 F13）：
   - 给本机某 Receiver 发一个按键；
   - 在 Controller 本机运行一个明显可见的命令（如 `notepad.exe` / `echo`）；
   - 在 Controller 本机对本机开一个简单 HTTP 服务并发请求；
2. 分别在以下场景下手动测试：
   - Controller + Receiver 同机、本机动作；
   - Controller + Receiver 不同机器，通过 multiaddr 互联；
3. 确认：
   - 按键触发顺序正确；
   - 即使某个动作失败（HTTP 超时、命令不存在），其他动作仍会执行；
   - 日志输出足够排查问题。

