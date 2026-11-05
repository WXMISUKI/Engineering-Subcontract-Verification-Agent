# 第三方源码快照说明（部分准备）

本目录中的源码是待核的上游材料，不包含全部对应源码、依赖、补丁或构建环境，不能单凭此目录认定满足许可证。SHA-256 用于标识本包中的文件；除 PyMuPDF 的摘要已与 PyPI 页面核对外，其余为下载后本地计算的摘要，不是上游签名或独立发布者校验值。

| 文件 | 来源及版本证据 | SHA-256 | 仍需核对 |
| --- | --- | --- | --- |
| `pymupdf-1.28.0.tar.gz` | [PyPI 1.28.0 源码分发包](https://pypi.org/project/PyMuPDF/1.28.0/#files)，摘要与 PyPI 页面一致。 | `e53f3567403a92da15caa9e7ae0164327fff48817e9f40175367fb9de524258d` | sdist 默认 MuPDF 1.28.0，而镜像内官方 wheel 报告 1.29.0；实际 wheel 对应 MuPDF 源码及构建输入尚未确认。 |
| `weknora-f17ee8a8.tar.gz` | [WeKnora 上游提交 f17ee8a](https://github.com/Tencent/WeKnora/commit/f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7)的 GitHub 源码快照。WeKnora 和 DocReader 镜像的 OCI revision 与 v0.9.0 标签所指提交一致。 | `4d5f8f93298a049ea9a35163d8317af7c918e62411a0a2573dce8ddbb2d6f831` | 快照根 LICENSE 与此前收录的版本一致；镜像标签属于发布者声明，并不证明所有镜像文件可由该快照复现。第三方包、系统包、模型、构建输入仍需核查。 |
| `paradedb-e66d6b01.tar.gz` | [ParadeDB v0.25.11 上游标签对应提交](https://github.com/paradedb/paradedb/commit/e66d6b01be8054694d18b1132d8300223f379ac4)的 GitHub 源码快照。 | `2aa80feb48eb3f6eef44c05998667083e73b55cd68859b7b690d16bdfc61051b` | 快照根 LICENSE 与此前收录版本一致；当前镜像未提供 OCI revision，不能仅凭标签证明此快照是完整对应源码。数据库、扩展、依赖、补丁和构建过程仍需核查。 |

快照不自动包含其他独立仓库或外部下载内容。分发许可、源代码和声明要求还须按实际交付及运行方式核对，详见 `../license-review.md` 和 `../license-triage.md`。
