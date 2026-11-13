## Why

独立版已具备可选 MoMA 文本模型和扫描页 OCR 适配，但原过程规格位于 Git 忽略的本地目录，其他环境无法从仓库复现其需求及验收边界。建立可跟踪的正式基线，保留待验证的 OCR 用量归集和真实业务验收事项。

## What Changes

- 跟踪已实现的显式 provider 选择、HTTPS、失败处理和 OCR 原页码等契约。
- 区分模拟接口、本地联通与目标环境及真实材料验收。
- 保留任务级 OCR 用量归集作为后续需求，不在本次规格对账中修改计费或业务代码。

## Capabilities

### New Capabilities
- `bs-moma-baseline`: 独立版 MoMA 模型和 OCR 接入的可追踪规格。

### Modified Capabilities

## Impact

- 仅整理规格与验收状态；不修改运行配置、应用代码或目标环境。
- 本地原 MoMA 过程资料继续保留；不将凭据或现场运行信息写入本规格。
