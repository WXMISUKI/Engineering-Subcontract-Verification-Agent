## ADDED Requirements

### Requirement: 明确选择 MoMA 文本协议
系统 SHALL 仅在显式选择 MoMA 文本 provider 时使用经配置的 HTTPS 端点和标准对话参数，不发送现供应商专有扩展字段。

#### Scenario: 文本请求
- **WHEN** 选择 MoMA 且模型、地址和密钥齐备
- **THEN** 系统向指定服务发送对话请求并处理结果与可用的用量信息

#### Scenario: 不安全配置
- **WHEN** provider 不受支持或 MoMA 地址不是 HTTPS
- **THEN** 请求失败且不发送材料

### Requirement: 扫描页经指定服务识别
系统 SHALL 以 PNG Base64 data URL 将扫描页发送到显式配置的 MoMA 图片理解接口，且不得创建公网文件链接或隐式切换供应商。

#### Scenario: 识别成功
- **WHEN** 服务返回完整非空页面文字
- **THEN** 系统保留页面文字和原物理页映射；没有真实坐标时布局为空

#### Scenario: 识别失败
- **WHEN** 图片超限、接口失败、响应为空或输出截断
- **THEN** 页面按未读或人工核实处理，不自动转发到其他供应商或本地 OCR

### Requirement: 核验安全和真实联通
系统 SHALL 为部署人员提供安全配置和验收步骤，并区分模拟验证与真实服务验收。

#### Scenario: 部署准备
- **WHEN** 准备接入业主服务
- **THEN** 负责人确认指定模型、网络、数据处理条款、证书、密钥和测试授权后再进行真实测试
