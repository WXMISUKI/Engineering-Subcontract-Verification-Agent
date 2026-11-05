# PDF 组件源码构建记录（进行中）

用户已选择开源许可路线，并同意尝试从官方源码重建。目标为独立后端镜像的 PyMuPDF 1.28.0 与 MuPDF 1.28.0；新镜像须经验证后才替换原候选，第三方许可及完整对应源码仍需逐项核查。

## 输入与构建方式

- [PyMuPDF 1.28.0 PyPI 源码包](https://pypi.org/project/PyMuPDF/1.28.0/#files)：SHA-256 `e53f3567403a92da15caa9e7ae0164327fff48817e9f40175367fb9de524258d`。
- [MuPDF 1.28.0 官方源码包](https://github.com/ArtifexSoftware/mupdf-downloads/releases/tag/1.28.0)：SHA-256 `21c7f064903154f1c3a7458bee81f130fc36f9b5147ea13328f9980e02d2dea2`。摘要与上游发布资源元数据一致；包含第三方源码，其许可仍需核查。
- 独立后端 Dockerfile 期望将上述两份源码包放在被 Git 忽略的 `delivery-data/build-input/`，并在构建时校验摘要。构建阶段使用 Linux amd64 Python 3.13 基础镜像、`pipcl==13`、`swig==4.4.1`、`libclang==18.1.1`、`packaging==26.3` 及 Debian 编译工具；构建本身需要取得系统及 Python 构建依赖，不能视为离线构建。
- `PYMUPDF_SETUP_MUPDF_BUILD` 指向已校验的 MuPDF 源码目录。最终镜像安装构建阶段产物，业务依赖安装中排除 PyPI 的预编译 PyMuPDF wheel。构建工具与系统仓库包尚未全部固定，不能声称可逐字节复现。

重建时，在仓库根目录创建 `delivery-data/build-input/`，将候选包 `third-party-sources/` 中上述两份同名源码包复制进去，再运行 `docker build -f docker/Dockerfile.backend -t bs-backend-candidate:local .`。Dockerfile 在解压前再次检查摘要。该构建需要可访问依赖仓库，并会改变本机目标镜像标签；运维人员应先备份现有版本并在隔离环境验证，不应直接覆盖正在使用的生产部署。离线安装使用预先导出的镜像，不需要重新编译。

## 隔离试构建证据

首次使用 SWIG 4.3.1 时，生成的扩展引用 Python 3.13 已移除的旧缓冲区函数，未构建成功；[Python 3.13 发布说明](https://docs.python.org/3.13/whatsnew/3.13.html)列出该 API 移除。改用 SWIG 4.4.1 并补装 `pkg-config` 后，试构建产生 `pymupdf-1.28.0-cp313-abi3-linux_x86_64.whl`，摘要 `084d21c941d7db51dbf6005545fb635dfae65c04bf4194d8c417e60a5d341e33`。该摘要仅对应试构建，不能假定后续构建逐字节相同。

在一次性、断网容器中加载该 wheel，确认 PyMuPDF/MuPDF 均报告 1.28.0；合成 PDF 文本提取和渲染通过，5 份授权 PDF 只读打开并渲染首页，其中 3 份被识别为需要视觉模型处理。基于试构建 wheel 的隔离回归覆盖分包文字 PDF 全流程、ZIP、合成印章和知识种子，10 项通过、141 项警告。结果不证明真实 OCR、全部页码、客户业务结论或许可证审核已通过。

独立后端新候选镜像 `sha256:e89aa8e1e825227acb74633f5a438a759d21fac27f83aa3a930e4a5515a2093b` 已从同样校验过的源码重新构建；其 wheel 摘要为 `2378be92369bff50bb88747003d45689b05232714dc39ef759feb45e70ed444e`，与试构建不同，符合尚未做到逐字节复现的预期。新镜像运行时版本、合成 PDF、5 份授权 PDF 的首页渲染和扫描页检查通过；定向回归 10 项通过、137 项警告。新归档在断网隔离 Docker 中加载并启动七项服务，合成账号登录、创建任务及 PDF 版本检查通过；临时环境已清理。首次离线脚本误消费输入导致登录检查未执行，修正脚本后重新演练通过。验证未覆盖真实模型、完整业务链、实际视觉印章和客户环境。
