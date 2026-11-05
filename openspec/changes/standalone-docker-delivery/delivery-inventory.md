# 离线交付候选：镜像与资源记录

记录时间：2026-11-05；目标架构为 Linux amd64。本文件对应候选包，不构成正式交付或客户环境容量承诺。`images.tar` 的 SHA-256 为 `0399f1a9d30141eccea0775e1f1b52d04f69fba0ec64c889d8c74181fb4f2083`。归档内 OCI index 的七个镜像摘要与本机候选镜像逐项一致。

| 镜像标签 | 镜像摘要（sha256） | Docker 报告的 Size，字节 |
| --- | --- | ---: |
| bs-backend-candidate:local | 905a9205f8a75d42611a77e6fe05d4249a6959b65dc0114005a5bbab06cbc092 | 362436288 |
| bs-frontend-candidate:local | 19eef4b5e4950fe843781773c553f4a7a56c914b6eff54134d1372fd3e225d67 | 26759580 |
| mysql:8.0.46 | 7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b | 233594134 |
| paradedb/paradedb:v0.25.11-pg17 | 78d65750f0f88a56548526201bcab9910f83b90fe929cdbf979890f3f80b0f95 | 366859358 |
| redis:7.0-alpine | c9d92d840fd011c908f040592857c724ae6d877f2aba5c40ad963276507386b2 | 13547033 |
| wechatopenai/weknora-app:v0.9.0 | 25b20959b7456f7842ef55ed4feff3694dbd0c6f8af4117c1fb3c5dec6bdf14d | 583749175 |
| wechatopenai/weknora-docreader:v0.9.0 | 8c71ec3cc2e291829c3938d2c7dc5f921c1d10551c8774bf844c83bc4aa3cb84 | 1672251393 |

候选目录约 4.05 GB，其中 `images.tar` 为 3,282,902,528 字节。七个镜像报告的 Size 合计约 3.26 GB，可能包含共享层；该数字不等于实际增量磁盘占用，也不包含数据库、知识索引、上传文件、备份、Docker 工作空间或文件系统开销。至少要同时考虑交付包和导入镜像所需空间，再按客户数据量、备份策略及余量规划；本机未测得足以保证生产的内存或磁盘下限。

所有最终文件以候选包内 `SHA256SUMS` 为准。七个镜像的 SPDX 自动扫描清单在 `sbom/`，许可审查仍未完成，见 `license-review.md`。
