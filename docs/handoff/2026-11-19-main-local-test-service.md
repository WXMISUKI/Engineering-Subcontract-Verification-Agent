# main本地测试服务与开发边界

测试入口：http://192.168.0.219:8090 ，沿用原账号及数据。8089候选版已主动停止，非异常宕机。

main提交为4a65a17af412ebdce43ec681a993f5103ec1811c；独立源码目录C:/project/nanjin/Engineering-Subcontract-Verification-Agent-main-test，tracked文件无改动。main前后端均固定镜像tag 4a65a17；backend镜像sha256:fee2ba70da75a37a79b6d24364ed820a5edf023254e0ed82da366a11a05e6ebe，已对照194个tracked Python/JSON文件一致；frontend由main独立工作树构建。

用户明确选用当前Docker MySQL的bs_review（12个任务），不是Windows 3306旧集成数据库。main复用原上传卷及知识服务；旧候选backend/frontend已停并restart=no，防止Docker重启后同库双后端。main restart=unless-stopped。

开发目录C:/project/nanjin/Engineering-Subcontract-Verification-Agent仍在featurelyl1118。可以继续改代码和隔离单测；禁止把开发版同时接入此业务库及上传卷。开发编辑不会改变main镜像。后续并行联调需为开发版单独建库和上传卷。

启动、停止、回退说明及受限备份位于C:/project/nanjin/bs-main-test-runtime/README.md，验收记录validation.json。运行目录的.env.shared及before-main-switch含敏感配置与备份，不得进Git或聊天。初始独立数据库方案已放弃，新建空卷/容器和重复副本已清理，原有数据卷未删除。

有限验证已通过：127.0.0.1与本机访问192.168.0.219的health，静态资源及PDF worker MIME，真实临时账号HTTP登录和Cookie，空会话创建/读取/删除，既有账号的会话/任务列表与详情，现有知识服务health。临时账号与会话已删除。未验证其他电脑防火墙可达性，未做付费Provider/OCR、完整业务重跑或完整灾备演练。
