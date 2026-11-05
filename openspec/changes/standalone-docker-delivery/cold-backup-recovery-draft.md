# 冷备份与隔离恢复操作草案

本文用于 Linux 主机上的 Docker Compose，适用于前述隔离演练的冷备方式；Windows 操作应在路径可由 Docker 守护进程访问的 Linux 环境中执行。正式执行前须确认维护窗口、磁盘空间、加密备份位置和责任人。备份含账号、任务、材料与密钥，必须严格限制访问并采用合适的加密存储；不得提交 Git 或放入公开交付包。

命令以 Bash 为例，`PROJECT` 必须是经过核实的源 Compose 项目名，`BACKUP` 必须是不存在的绝对路径。不要使用默认或猜测的项目名。退出前确认无用户写入、无知识初始化、上传或解析任务，再由运维人员停止该项目的全部服务。下列检查不会替你停止服务；只要该项目仍有运行中的容器，就不得备份。

```bash
PROJECT='明确核实后的源项目名'
BACKUP='/安全备份位置/新的备份目录'
IMAGE='mysql:8.0.46@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b'
docker compose -p "$PROJECT" ps --all
docker ps --filter "label=com.docker.compose.project=$PROJECT"
```

逐个检查卷所属项目，确认这五个卷就是目标项目的数据；如有任何不一致，停止操作。Compose 配置、`.env` 和知识目录须来自同一个项目快照。MySQL 镜像只用作离线归档工具，必须已存在于本机。

```bash
for name in bs-mysql bs-uploads knowledge-postgres knowledge-files knowledge-redis; do
  docker volume inspect "${PROJECT}_${name}" --format '{{.Name}} {{index .Labels "com.docker.compose.project"}}'
done
docker image inspect "$IMAGE" >/dev/null
```

从该项目的 Compose 目录执行，以下命令在任何容器仍运行、备份目录已存在或卷归属不匹配时退出；只读挂载源卷，不更改源卷。`config.tar.gz` 包含 `.env`、知识原件、映射和查询凭据，不得单独公开。

该命令块已在断网隔离的 Linux Docker 环境使用合成卷执行：运行中容器被拒绝，停止后五个卷及配置归档的校验和与内容检查通过。该验证不覆盖客户主机、并发写入或真实资料。

```bash
set -eu
running=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT")
test -z "$running"
test ! -e "$BACKUP"
test -f .env && test -d delivery-data/knowledge-config && test -d delivery-data/knowledge-source
for name in bs-mysql bs-uploads knowledge-postgres knowledge-files knowledge-redis; do
  test "$(docker volume inspect "${PROJECT}_${name}" --format '{{index .Labels "com.docker.compose.project"}}')" = "$PROJECT"
done
umask 077
mkdir "$BACKUP"
tar -czf "$BACKUP/config.tar.gz" .env delivery-data/knowledge-config delivery-data/knowledge-source
for name in bs-mysql bs-uploads knowledge-postgres knowledge-files knowledge-redis; do
  docker run --rm --network none --pull never --user 0 \
    --mount "type=volume,src=${PROJECT}_${name},dst=/data,readonly" \
    --mount "type=bind,src=$BACKUP,dst=/backup" \
    --entrypoint tar "$IMAGE" -czf "/backup/$name.tar.gz" -C /data .
done
(cd "$BACKUP" && sha256sum ./*.tar.gz > SHA256SUMS && sha256sum -c SHA256SUMS)
```

将备份复制到加密存储后，在独立新环境检验校验和、归档内容、文件可读性和版本；保存与原始服务一致的应用镜像、第三方镜像、`KNOWLEDGE_AES_KEY`、`KNOWLEDGE_JWT_SECRET` 和 `SECRET_KEY`。备份产生时应用已停止，恢复前也不得对备份集单独改动其中任一成员。

恢复必须在独立、无并发运维操作的新 Docker 环境进行，使用新项目名和新目录，不能覆盖现有环境。以下示例中 `RESTORE_PROJECT` 是事先核实未使用过的项目名，`BACKUP` 和 `IMAGE` 如上；先在新目录只放置同版本 `compose.yaml` 并加载对应镜像。备份应来自可信来源，校验和只能验证完整性，不能验证备份来源。操作前人工检查配置归档的路径清单：

```bash
RESTORE_PROJECT='明确核实未使用的新项目名'
(cd "$BACKUP" && sha256sum -c SHA256SUMS)
tar -tzf "$BACKUP/config.tar.gz"
docker info >/dev/null
docker ps -a --filter "label=com.docker.compose.project=$RESTORE_PROJECT"
for name in bs-mysql bs-uploads knowledge-postgres knowledge-files knowledge-redis; do
  docker volume ls --filter "name=^${RESTORE_PROJECT}_${name}$"
done
```

确认归档路径、项目名及目录无误后执行；每个目标卷必须不存在，新建后必须为空，任一异常立即停止。命令失败后保留新环境供排查，不能盲目清理或重试覆盖：

```bash
set -eu
test -f compose.yaml && test ! -e .env && test ! -e delivery-data
all_containers=$(docker ps -aq --filter "label=com.docker.compose.project=$RESTORE_PROJECT")
test -z "$all_containers"
for name in bs-mysql bs-uploads knowledge-postgres knowledge-files knowledge-redis; do
  if docker volume inspect "${RESTORE_PROJECT}_${name}" >/dev/null 2>&1; then
    echo "目标卷已存在，停止恢复: ${RESTORE_PROJECT}_${name}" >&2
    exit 1
  fi
done
(cd "$BACKUP" && sha256sum -c SHA256SUMS)
tar -xzf "$BACKUP/config.tar.gz" -C .
for name in bs-mysql bs-uploads knowledge-postgres knowledge-files knowledge-redis; do
  docker volume create --label "com.docker.compose.project=$RESTORE_PROJECT" \
    --label "com.docker.compose.volume=$name" "${RESTORE_PROJECT}_${name}" >/dev/null
  docker run --rm --network none --pull never --user 0 \
    --mount "type=volume,src=${RESTORE_PROJECT}_${name},dst=/data" \
    --mount "type=bind,src=$BACKUP,dst=/backup,readonly" \
    --entrypoint sh "$IMAGE" -c 'test -z "$(ls -A /data)" && tar -xzf "/backup/$1.tar.gz" -C /data' sh "$name"
done
docker compose -p "$RESTORE_PROJECT" up -d --no-build --pull never
```

逐项验证登录、本人任务、文件读取、知识凭据和目标文件检索，并检查恢复后的业务库、映射和知识库是否属于同一备份集。上述命令块已在断网隔离 Docker 环境中使用合成账号与任务演练：目标卷已存在时拒绝恢复，恢复后全部服务启动并可读取备份前的任务。另一项独立演练验证了单份合成知识文件恢复后的检索；完整六份资料、业务绑定与知识映射作为同一备份集的端到端恢复尚未验收。

恢复时如遇文件校验失败、数据库版本不匹配、密钥缺失、查询凭据失效或知识绑定不一致，应停止恢复并保留备份，不能用 `create_all` 伪装迁移、重新生成 AES 密钥或清空现有卷。升级和灾备切换须另行制定迁移、回退和实际业务验收方案。
