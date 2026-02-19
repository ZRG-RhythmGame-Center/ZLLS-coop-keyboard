- 可配置的 一对一 或者 一对多的按键映射
- 可配置的 按下按键发送到哪台机器

## Milestone 1：最小可用 Demo

### 运行方式

1. **安装**：`uv sync` 或 `pip install -e .`

2. **启动 Receiver**（被控端）  
   - 复制 `config.receiver.example.yaml` 为 `config.yaml`，或使用 `-c` 指定配置。  
   - 运行：`uv run zlls-coop-keyboard-receiver` 或 `zlls-coop-keyboard-receiver`  
   - 终端会打印：`Connect Controller to: /ip4/0.0.0.0/tcp/端口/p2p/PeerID`  
   - 同机测试时，Controller 需使用 **127.0.0.1** 替代该地址中的 **0.0.0.0**，端口与 PeerID 保持不变。

3. **启动 Controller**（控制器端）  
   - 复制 `config.controller.example.yaml` 为 `config.yaml`，将 `controller.peers.default.address` 改为上一步的地址（同机用 127.0.0.1）。  
   - 运行：`uv run zlls-coop-keyboard-controller` 或 `zlls-coop-keyboard-controller`  
   - 在本机按键，Receiver 终端会打印收到的 `[key_event] down/up key=...`。

### 配置

- 默认配置文件：当前目录下的 `config.yaml`；可用 `-c/--config` 指定路径。  
- Receiver 可选参数：`--port 端口`（默认 0 为随机端口）。  
- Controller 可选参数：`--port 端口`。

详细设计见 [DESIGN.md](DESIGN.md)。

