# 独立分包服务运维说明（候选，未完成离线交付验收）

## 范围与部署前提

该 Compose 仅部署工程分包及其基础服务。镜像和知识种子可离线提供，LLM、Embedding 以及扫描件识别服务仍须按环境配置并可访问。安装前核对 CPU 架构、磁盘、内存、模型接入、访问域名和 HTTPS；客户机器与资源需求尚未实测。

只运行一个 backend 实例和一个 worker。所有连接同一 `bs_review` 数据库的后端必须使用同一个支持 Linux `flock` 的上传卷；不同上传卷连接同一数据库不受此锁保护，不支持。运行中不得删除上传卷内 `.bs-backend.lock`，不得绕过独立 ASGI 入口。后端启动先获取锁，再把中断中的材料标为失败、把 `preparing` 任务恢复为 `setup`；已完成材料不会重置。运行者应在界面查看事件，按提示重新上传或重试。使用网络文件系统时须先验证其跨容器锁语义。

已登录的限流操作按签名有效、未过期的账号会话分别计数；登录接口仍按连接来源共享限制。由于前端反向代理会汇聚连接来源，多人集中登录可能共享登录额度，出现 429 时应稍后重试；不要直接信任客户端传入的来源地址头。

知识原件在 `delivery-data/knowledge-source/`，与清单名称、路径、SHA-256 一致；`delivery-data/knowledge-config/` 保存初始化日志、映射和查询密钥，必须限制访问。目录和 `.env` 不应提交 Git；业务数据库、上传文件及真实凭据不能包含在种子或镜像中。

## 首次安装流程

在交付包目录执行，以下命令是候选操作流程，须在最终镜像及目标机器上复验。

1. 从 `.env.example` 建立本机 `.env`，生成独立随机密码和密钥。`DATABASE_URL` 指向 `mysql` 容器的 `bs_review`，且连接密码与 `MYSQL_PASSWORD` 一致，特殊字符按 URL 编码。保管 `SECRET_KEY`、`KNOWLEDGE_AES_KEY` 等稳定密钥；AES 密钥必须为 32 字节，丢失后知识服务加密内容可能无法恢复。准备独立的新 WeKnora 管理账号及 Embedding 配置；六份知识原件含扫描件，因此还需配置 `KNOWLEDGE_VLM_*`。模型地址只允许必要的精确可信域名，按需设置 `KNOWLEDGE_SSRF_WHITELIST_EXTRA`。业务模型和上传材料 OCR 分别使用 `LLM_*`、`OCR_CLOUD_*`。
2. 在本机试用时使用仅监听 `127.0.0.1` 的 HTTP 入口并设置 `COOKIE_SECURE=false`；生产使用 HTTPS 反向代理并设置 `COOKIE_SECURE=true`。默认 secure Cookie 不会随普通 HTTP 请求发送。不要将测试用 HTTP 入口暴露给其他主机。
3. 准备好本地目录和离线镜像后启动基础服务：

   ```sh
   docker compose up -d --wait --wait-timeout 180 mysql knowledge-postgres knowledge-redis docreader weknora
   docker compose ps
   docker compose run --rm --no-deps backend python -m app.standalone.manage init-db
   docker compose run --rm --no-deps backend python -m app.standalone.manage create-user USERNAME
   ```

   `--wait` 等待带健康检查的服务通过检查、其他服务进入运行状态；若超时或失败，排查后再继续，不要直接执行初始化。需要支持该选项的 Docker Compose 版本。建号命令在交互终端输入两次密码，密码不写命令行或聊天。`init-db` 创建缺失表，不是升级迁移工具，只用于新库或确认版本兼容的库。
4. 确认 WeKnora 健康、模型可用、源文件和外置密钥齐备，执行：

   ```sh
   docker compose --profile init run --rm knowledge-init python -m app.standalone.knowledge_seed_cli --bootstrap
   ```

   命令只连接 Compose 内部 WeKnora 和独立 `bs_review`，创建新管理员、模型及分包知识库，等待解析、生成映射和受限查询凭据。受限凭据和模型的管理凭据分离。扫描资料通过外部 OCR/VLM 处理时，文件内容会发往所配置的服务。解析成功不代表业务依据和页码准确；上线前需核对五步召回和专家结论。
5. 启动应用并检查：

   ```sh
   docker compose up -d backend frontend
   docker compose ps
   ```

   等待前端代理及后端启动完成，访问入口对应的 `/api/health` 返回成功后再登录；启动窗口内可能短暂返回 502，`docker compose ps` 显示运行并不等于应用已就绪。然后登录测试账号，确认会话、任务创建、本人任务隔离与知识召回。健康接口只证明基础数据库连通，不代表模型、知识检索和业务结果均正常。

## 中断与重复执行

初始化会先记录待确认操作，再请求知识服务；已明确完成的步骤可重复执行。资料上传发生响应丢失后，确认没有其他初始化进程或人员修改该独立知识库，再用原参数重新执行 `--bootstrap`：程序核对管理员和模型配置、已确认的目标库及完整文件列表，只在同名候选唯一、库与模型一致、解析完成且下载的远端原件 SHA-256 与种子一致时自动恢复日志。下载只在内存中计算摘要，不写入原件或将内容写日志；该步骤需要管理员对原件的读取权限。若文件仍在解析，可待其完成后重试。列表不完整、权限不足、重复、原件不符或配置变化均保持停止；不得自行删除 pending 后盲目重试。该能力仅覆盖文档上传，其他未知操作仍需维护人员先备份配置目录及相关数据库、核对远端后处理。查询密钥创建若结果未知，不能从日志取回原始密钥，需单独处理已创建凭据并验证最小权限。

初始化锁异常残留时，必须先确认没有运行中的初始化容器，再备份锁和日志并核对中断状态；不能因看到锁文件就删除后重试。后端锁是另一种运行期文件锁，正常进程退出会释放文件锁，文件本身应保留。

## 升级和备份边界

业务 MySQL、上传卷、WeKnora PostgreSQL、知识文件卷、知识原件、知识映射和查询密钥、以及稳定加密密钥应作为相互关联的一致备份集保存。维护窗口内停止写入并确认无初始化或解析任务后备份，分别校验和保存；恢复只在隔离的新环境进行，不覆盖现存数据。Redis 和临时目录是否可重建须随对应 WeKnora 版本确认。操作草案见 `cold-backup-recovery-draft.md`；下述隔离演练提供部分证据，但不能推断客户环境和全部业务场景均可灾难恢复。

隔离合成演练已验证一种冷备方式：停止 Compose 服务，在只读挂载的数据卷上用 `tar` 归档 `bs-mysql`、`bs-uploads`、`knowledge-postgres`、`knowledge-files` 和 `knowledge-redis`，在另一套确认空置的卷中以 root 保留归档所有权解包，再用相同镜像版本和稳定密钥启动。恢复后核对了业务账号和任务、知识管理员登录、PostgreSQL 测试记录及上传和知识文件。

另一次知识服务演练将合成文本索引后，冷备并恢复知识数据库、知识文件、Redis、受限查询凭据及稳定密钥，在恢复环境中通过原凭据再次检索到同一文件。之后还在隔离环境将六份知识资料、业务库绑定、映射和凭据作为同一备份集恢复，五步各抽查一项在恢复前后均命中目标文件；其中第二步没有可靠页码。Embedding 服务仍需可访问；全部规则、目标环境和专家业务结果尚未验收，不能将这几项抽查视为完整灾备验收。

当前没有版本化数据库迁移机制。升级前必须核对应用及 WeKnora 的版本变更、数据库迁移要求和完整备份，在隔离副本验证迁移与回退；禁止把 `create_all` 当作现有表升级。正式版本及迁移方案确定后再执行升级。
