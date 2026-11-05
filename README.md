# 工程分包审查智能体（独立版候选）

本项目将工程分包审查工作台单独交付，包含账号登录、本人任务、材料解析、五步核查、人工复核、补件和报告。它不包含智能体广场或其他完整智能体。业务任务与知识库使用独立数据；历史账号、合同和任务不随代码迁移。

目前是**验收候选**，不是已经完成客户部署或专家验收的正式版本。已知限制包括部分扫描知识资料缺少可靠页码、正式业务结论及完整许可证义务仍待核查。当前进度见 [实施进度](openspec/changes/standalone-docker-delivery/implementation-status.md)，详细边界和阶段见 [后续任务与验收方案](openspec/changes/standalone-docker-delivery/continuation-plan.md)。

## 运行结构

`compose.yaml` 编排前端、后端、MySQL、WeKnora、DocReader、ParadeDB 和 Redis。首次部署需要独立数据库和账号、分包知识原件、Embedding 服务，以及扫描件所需的视觉识别服务；业务 LLM 和 OCR 按环境配置。镜像和知识原件可离线交付，外部模型是否可离线取决于实际部署。

分包材料扫描识别、质量重试和旋转补读均使用配置的线上 OCR；线上不可用时保留未读及人工核实，不使用本地 OCR 模型。知识种子识别使用单独的视觉模型配置。

从 `.env.example` 创建本地 `.env`，不要将真实密钥提交到仓库；知识原件与初始化输出分别放入 `delivery-data/knowledge-source/` 和 `delivery-data/knowledge-config/`，均不纳入 Git。初始化、单实例限制、HTTPS 和恢复边界见 [运维说明](openspec/changes/standalone-docker-delivery/operator-guide-draft.md)及[冷备与恢复草案](openspec/changes/standalone-docker-delivery/cold-backup-recovery-draft.md)。

从源码重建后端镜像需要预先准备固定版本的 PyMuPDF 和 MuPDF 源码包，构建依赖需联网获取；输入位置、摘要及验证范围见 [PDF 源码构建记录](openspec/changes/standalone-docker-delivery/pdf-source-build.md)。离线安装使用已导出的镜像，不需要重新编译。

## 许可证与材料

第三方组件和知识资料的权利范围应在对外分发前确认。[许可证核查记录](openspec/changes/standalone-docker-delivery/license-review.md)列出了已识别的 AGPL、GPL 和其他许可及未完成事项；公开仓库本身不代表许可核查已通过。知识资料原件不放在本仓库。
