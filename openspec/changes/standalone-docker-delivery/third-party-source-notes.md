# 第三方源码快照说明（部分准备）

本目录中的源码是待核的上游材料，不包含全部对应源码、依赖、补丁或构建环境，不能单凭此目录认定满足许可证。SHA-256 用于标识本包中的文件；PyMuPDF 的摘要已与 PyPI 页面核对，MySQL 包已验证官方签名，其余快照的摘要为下载后本地计算，不是上游签名或独立发布者校验值。

| 文件 | 来源及版本证据 | SHA-256 | 仍需核对 |
| --- | --- | --- | --- |
| `pymupdf-1.28.0.tar.gz` | [PyPI 1.28.0 源码分发包](https://pypi.org/project/PyMuPDF/1.28.0/#files)，摘要与 PyPI 页面一致。 | `e53f3567403a92da15caa9e7ae0164327fff48817e9f40175367fb9de524258d` | sdist 默认 MuPDF 1.28.0，而镜像内官方 wheel 报告 1.29.0；实际 wheel 对应 MuPDF 源码及构建输入尚未确认。 |
| `mysql-8.0.46.tar.gz` | [MySQL 官方 8.0.46 源码包](https://dev.mysql.com/downloads/mysql/8.0.html?os=src)，MD5 与官网一致；使用官方签名及官方文档列示的密钥指纹 `BCA43417C3B485DD128EC6D4B7B3B788A8D3785C` 验证通过。签名和公钥一并保存。 | `e0d1a98ae5188c6a4efed74c74ff28115d4b791bc9d591a78e14d20aded67344` | 这是不含 Boost 的官方通用源码包；镜像中的构建差异、其他依赖、系统组件和适用分发义务仍需核对，不能认定完整对应源码已齐。 |
| `uv-329bf944.tar.gz` | [uv 0.13.6 上游提交](https://github.com/astral-sh/uv/commit/329bf9448afd9f9088d14a4d57fbc61eb454f117)的 GitHub 源码快照。两个知识镜像中的 `uv`、`uvx` 分别与 [0.13.6 官方 Linux 发布包](https://github.com/astral-sh/uv/releases/tag/0.13.6)内的同名文件摘要一致。 | `292da3a4139df78caf647364db4a4fee639fb4f3e9a2635fd52d5258006e7112` | 上游锁文件包含扫描到的 532 个不同 Rust 包坐标，但快照不包含它们的全部源码与许可；发布二进制的具体构建配置及所有传递依赖仍须核查。 |
| `weknora-f17ee8a8.tar.gz` | [WeKnora 上游提交 f17ee8a](https://github.com/Tencent/WeKnora/commit/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7)的 GitHub 源码快照。WeKnora 和 DocReader 镜像的 OCI revision 与 v0.9.0 标签所指提交一致。 | `4d5f8f93298a049ea9a35163d8317af7c918e62411a0a2573dce8ddbb2d6f831` | 快照根 LICENSE 与此前收录的版本一致；镜像标签属于发布者声明，并不证明所有镜像文件可由该快照复现。第三方包、系统包、模型、构建输入仍需核查。 |
| `paradedb-e66d6b01.tar.gz` | [ParadeDB v0.25.11 上游标签对应提交](https://github.com/paradedb/paradedb/commit/e66d6b01be8054694d18b1132d8300223f379ac4)的 GitHub 源码快照。 | `2aa80feb48eb3f6eef44c05998667083e73b55cd68859b7b690d16bdfc61051b` | 快照根 LICENSE 与此前收录版本一致；当前镜像未提供 OCI revision，不能仅凭标签证明此快照是完整对应源码。数据库、扩展、依赖、补丁和构建过程仍需核查。 |

候选 ParadeDB 镜像内 `postgresql-17-pg-search` 软件包及扩展默认版本均为 0.25.11；镜像版权文件已另行保存。软件包清单中列出的 LICENSE 路径在镜像文件系统内不存在，候选包另外保留上游许可证文本。上游 v0.25.11 官方发布的 trixie amd64、PostgreSQL 17 deb（SHA-256 `d47e999cd53e80f6e9bb8c4781d7045def89af245a1c15dc631141b30a04c78a`）内 `pg_search.so` 与镜像对应文件摘要均为 `637debeef0546779070ae58f354219029b755971c4185f0db71276968c71e709`；deb 内 LICENSE 与已收录许可原文摘要均为 `8486a10c4393cee1c25392769ddd3b2d6c242d6ec7928e1414efff7dfb2f07ef`。未验证签名、二进制构建过程或完整对应源码；见 `../license-review.md`。

快照不自动包含其他独立仓库或外部下载内容。分发许可、源代码和声明要求还须按实际交付及运行方式核对，详见 `../license-review.md` 和 `../license-triage.md`。
