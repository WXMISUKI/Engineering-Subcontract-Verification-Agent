# 第三方许可证核查（进行中）

用户选择按开源许可证路径核查。下列为已识别的重点，尚不是完整依赖清单或法律结论；公开仓库存在不等于已满足二进制分发、对应源码、版权声明及网络使用等适用义务。确认完整软件物料清单、版本对应源码和许可证后，才能定稿对外交付。

已从当前候选后端镜像导出 65 个 Python 发行包的元数据，并从本地安装的前端生产依赖生成许可分类，见 `third-party-inventory.json`。Python 元数据中没有完全缺少许可字段和许可分类的条目，但元数据不等于核查结论；前端结果来自本机依赖而非最终 bundle 清单。该清单不包含系统包、WeKnora、DocReader、数据库及其模型文件，不能代替完整 SBOM。

另用 Syft 1.56.0 扫描七个候选镜像，生成 SPDX JSON 清单并放入候选包 `sbom/`。扫描结果分别识别后端 209、前端 73、MySQL 142、ParadeDB 330、Redis 22、WeKnora 3778、DocReader 2353 个组件条目；其中许可证声明为 NOASSERTION 或 NONE 的分别为 45、1、4、57、6、1546、1544 条。条目数可能包含重复或工具识别结果，声明缺失也不等于没有许可证；必须逐项核对来源、版本及对应许可证。镜像扫描不能保证发现所有模型、字体、静态打包依赖或其他非包文件，不能代替人工合规审查。

已导出 3203 条待核记录及来源到候选包 `license-unresolved.json`，按类型和重复线索整理在 `license-triage.md`。其中知识镜像的 2128 条 Rust 记录来自四处 `uv` / `uvx` 检测，purl 集合相同、为 532 个不同 purl；这只是排查去重线索，不代表许可证已核实。后端实际运行时报告 MuPDF 1.29.0，已核对官方 PyMuPDF 1.28.0 wheel 与镜像内相关文件一致，并放入校验过的 PyMuPDF 1.28.0 源码包；该源码包默认的 MuPDF 版本为 1.28.0，仍须核查实际 wheel 的完整对应 MuPDF 源码与构建方式。

对候选 ParadeDB 镜像作离线只读核查：安装的软件包 `postgresql-17-pg-search` 为 0.25.11，扩展控制文件默认版本亦为 0.25.11；另有 PostgreSQL 17.11 和 pgvector 0.8.4。镜像内软件包版权文件声明 `AGPL-3.0-or-later`，其原文已收录为 `third-party-licenses/ParadeDB-pg-search-0.25.11-copyright.txt`。但该版权文件所指的同目录 `LICENSE` 实际缺失，尽管 `dpkg -L` 仍列出此路径；候选包另行收录了上游 0.25.11 的许可证文本。包版本与上游标签一致只支持版本识别，尚不能证明该标签快照包含构建此二进制的完整对应源码、补丁和依赖，也不能据此认定分发义务已满足。

进一步从 [ParadeDB v0.25.11 官方发布](https://github.com/paradedb/paradedb/releases/tag/v0.25.11)下载 PostgreSQL 17、Debian trixie、amd64 的官方 deb，包 SHA-256 与 GitHub 发布资源摘要一致。包中 `pg_search.so` 的 SHA-256 与候选镜像对应文件完全一致；包内 LICENSE 的 SHA-256 也与候选包所收录的上游许可证一致。此比对加强了该扩展二进制和许可证来源证据；未验证发布签名、源码构建过程或镜像其他文件，不能代替完整对应源码及许可证义务核查。

`third-party-licenses/` 已收录上表五个重点项目对应版本的上游许可文件。Redis 和 MySQL 的版本通过候选镜像内命令核对分别为 7.0.15 和 8.0.46；保留许可证原文只完成部分准备工作，尚需确认镜像内全部组件、对应源码及适用的分发条件。

| 组件及实际版本 | 初步证据 | 交付前待核 |
| --- | --- | --- |
| PyMuPDF 1.28.0（后端直接依赖） | 镜像内包元数据标注 AGPL-3.0 或商业授权；[权利人说明](https://pymupdf.io/licensing)介绍两种许可路径。 | 核查项目代码与打包、分发及网络运行方式对应的 AGPL 条件，提供对应源码、许可证及适用声明；如不采用开源路径须另有适用商业授权。 |
| ParadeDB v0.25.11（知识数据库镜像） | [该版本许可证](https://github.com/paradedb/paradedb/blob/v0.25.11/LICENSE)为 AGPL-3.0；[项目说明](https://github.com/paradedb/paradedb#license)区分社区与商业许可。 | 核对镜像内实际组件与版本、对应源码及分发和网络使用条件，不假定服务进程分离就没有义务。 |
| MySQL 8.0.46 | [MySQL 社区版说明](https://www.mysql.com/products/community/)标注 GPL；容器还包含其他许可组件。 | 核对具体镜像内各组件许可、对应源码与二进制分发要求。 |
| WeKnora v0.9.0 / DocReader v0.9.0 | [该版本许可文件](https://github.com/Tencent/WeKnora/blob/v0.9.0/LICENSE)标注项目为 MIT，列出部分第三方组件。 | 核对两个实际镜像全部第三方组件、模型文件、版权及许可证声明。 |
| Redis 7.0 | [Redis 官方许可表](https://redis.io/legal/licenses/)说明 7.2 及更早版本为 BSD-3-Clause。 | 核对实际镜像内其他组件并保留相应版权和许可。 |
| 前端、后端及基础镜像其余依赖 | `frontend/pnpm-lock.yaml`、`backend/requirements.txt` 和 Dockerfile 固定依赖/基础镜像。 | 生成包含传递依赖和容器系统包的清单；核对许可证、NOTICE、字体、OCR 模型及源码来源；记录无法识别项并处理。 |

源项目迁入的企业代码、内部规则和六份知识资料还需按实际权利归属、用户已确认范围及对外分发方式分别核验。知识原件不提交公开仓库；来源与版本缺口详见 `knowledge-inventory.md`。

当前没有声明该独立应用自身的开源许可证。确定项目及被迁入代码的权利人、兼容的发布许可和对应源码提供方式之前，不能仅因第三方存在 AGPL 就自行替用户指定整个项目的许可证。
