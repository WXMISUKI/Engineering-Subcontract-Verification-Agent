# 镜像许可证待核清单与源码线索

2026-11-05，依据候选包中 Syft 1.56.0 生成的七份 SPDX JSON，筛选 `licenseDeclared` 为 `NOASSERTION` 或 `NONE` 的条目，生成候选包 `license-unresolved.json`。保留镜像、SPDX ID、名称、版本、软件包类型、purl 和扫描来源，方便逐项核对。此表是机器筛选结果，可能包含误报、同一组件重复条目和镜像自身节点，不代表这些组件没有许可证；也不能证明扫描覆盖全部文件或模型。

| 扫描类型 | 未识别声明的条目数 | 初步处理方向 |
| --- | ---: | --- |
| Rust | 2128 | 先核对 `uv` / `uvx` 所含版本对应的依赖、许可证与 NOTICE。 |
| Debian 包 | 470 | 按镜像、包名和版本核对发行版版权文件与对应源码。 |
| Go | 333 | 核对模块版本、上游许可和二进制构建依赖。 |
| Java | 138 | 核对构件版本与分发许可、NOTICE。 |
| 二进制识别项 | 89 | 核对来源与实际是否属于交付物，排除误识别。 |
| Python | 27 | 核对包内许可证和发行版本；不要只依赖元数据字段。 |
| npm | 10 | 核对是否实际进入交付镜像及各包许可。 |
| 镜像根节点 / Alpine 包 | 7 / 1 | 分别核对镜像整体声明与 Alpine 实际包。 |

两个知识镜像中，Rust 条目各 1064 项，分别来自两份 `uv` 和 `uvx` 二进制；四处检测的 purl 集合完全一致，均为 532 个不同 purl。两个镜像内 `uv` / `uvx` 均为 0.13.6、同名二进制摘要分别相同，并与官方发布包对应文件一致；上游 0.13.6 源码的 Cargo.lock 包含这 532 个包坐标（比较前解码 purl 百分号转义）。已保存上游主项目 MIT / Apache-2.0 许可和源码快照，但锁文件没有证明这些传递依赖的许可证及对应源码均已备齐。全部 3203 条待核记录包含 1412 个不同 purl，另有 51 条没有 purl；这些只是去重线索，不是已完成许可证判定。

## 重点源码线索

- 后端镜像实际运行时报告 PyMuPDF 1.28.0、MuPDF 1.29.0。下载的 [PyPI 官方 Linux wheel](https://pypi.org/project/PyMuPDF/1.28.0/#files) SHA-256 为 `44f0973f5e5edbaec95bc34b64e71d1959d4ee90b1328de1b4f4f5b4fa78673f`，与官方页面一致；其中 `__init__.py`、MuPDF 两个共享库及一个静态库的摘要与候选镜像逐项一致。官方 wheel 本身含 `libmupdf.so.29.0`，因此该版本差异不单独证明镜像被替换。
- 上游 [1.28.0 发布说明](https://github.com/pymupdf/PyMuPDF/releases/tag/1.28.0)写明使用 MuPDF 1.28.0，而其 [问题 #5052](https://github.com/pymupdf/PyMuPDF/issues/5052)报告了 PyMuPDF 1.28.0 运行时为 MuPDF 1.29.0 的相同组合。两者进一步说明版本信息需核实，但问题报告没有确定当前 wheel 所用 MuPDF 的精确源码提交，不能作为完整对应源码证据。
- 已下载 [PyPI 官方 PyMuPDF 1.28.0 源码分发包](https://pypi.org/project/PyMuPDF/1.28.0/#files)，SHA-256 `e53f3567403a92da15caa9e7ae0164327fff48817e9f40175367fb9de524258d` 与官方页面一致，放入候选包 `third-party-sources/`。该包的 `setup.py` 默认版本为 MuPDF 1.28.0，并说明 MuPDF 单独下载与构建；包中没有 MuPDF 原始源码目录。其与上述 wheel 报告的 1.29.0 存在差异，尚未确认 wheel 的完整构建输入、MuPDF 1.29.0 对应源码及补丁，因此此源码包只能作为部分准备，不能声称已提供完整对应源码。[PyMuPDF 许可说明](https://pymupdf.io/licensing)列出 AGPL 与商业路径，适用义务仍须核查。
- [ParadeDB v0.25.11 许可证](https://github.com/paradedb/paradedb/blob/v0.25.11/LICENSE)为 AGPL；须核实该镜像的实际构建源码、其他组件、版本与适用义务，不假定仅有上游标签就满足对应源码要求。
- 已保存 [MySQL 8.0.46 官方源码包](https://dev.mysql.com/downloads/mysql/8.0.html?os=src)，MD5 与官网一致，并使用与 [MySQL 官方文档](https://dev.mysql.com/doc/refman/8.0/en/checking-gpg-signature.html)所列指纹一致的发布公钥验证签名；该包不含 Boost，仍需确认 Docker 镜像实际构建差异、其他组件及 GPL 分发要求，见 `third-party-source-notes.md`。
- [WeKnora v0.9.0 许可证](https://github.com/Tencent/WeKnora/blob/v0.9.0/LICENSE)和 [Redis 7.0.15 许可证](https://github.com/redis/redis/blob/7.0.15/COPYING)是主项目线索，不能代替各自镜像内传递依赖与系统包核查。WeKnora 两个镜像的 revision 与上游标签一致；已保存对应快照。ParadeDB 镜像无 revision 标签，已保存上游标签指向提交的参考快照，不能当作已验证对应源码；参见 `third-party-source-notes.md`。

优先核实 PyMuPDF/MuPDF、ParadeDB 的对应源码与许可证路径，再处理镜像内大批重复依赖和系统包；确认权利人、产品分发方式和需提供的材料后才能形成正式结论。以上不是法律意见，也没有宣称源代码链接或 SPDX 自动清单已完成合规。
