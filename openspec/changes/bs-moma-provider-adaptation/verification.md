# 验证记录

## 独立交接分支

2026-11-10，从 `origin/main` 的 `17893ba08f26dad28dee34dd3c08913f191840bc` 创建的 `codex/moma-adaptation-20261110` 单独执行：`test_moma_adaptation.py`、`test_standalone_cloud_ocr.py`、`test_standalone_online_ocr.py`、`test_standalone_usage.py` 共 26 通过、零失败，1 条配置文件警告；OpenSpec 严格校验通过。使用既有独立项目虚拟环境和模拟接口，没有连接真实模型。下方 47 项结果属于不同的混合工作区，不是本分支额外的测试结果。

## 原独立项目混合工作区

2026-11-10，本地独立版现有工作区与项目 `.venv`。现有工作区包含大量其他未提交变更，本结果针对当时完整工作区，不能代表单独提交或线上版本。

- `tests/test_moma_adaptation.py`、`test_standalone_cloud_ocr.py`、`test_standalone_online_ocr.py`、`test_bs_request_error_visibility.py`、`test_standalone_usage.py`、`test_bs_ai_checkpoint_resume.py`：47 通过、零失败；测试有配置缺失和依赖弃用类警告。
- 合成数据和 MockTransport 验证文本协议、OCR 图片 data URL、无词框、真实扫描 PDF 页码与未读、截断、超限、不安全地址、重定向、旧协议防误用和检查点隔离；没有访问真实模型。
- `openspec validate bs-moma-provider-adaptation --strict` 通过；`docker compose --env-file .env.example config --no-interpolate --quiet` 通过语法校验，不代表实际启动或凭据有效；已检查本次修改的已跟踪文件 `git diff --check`。
- 未进行后端全量测试、真实 MoMA 接口、安全条款/网络核验、目标环境部署、真实材料或专家业务验收。当前生产可用性不可判定。
