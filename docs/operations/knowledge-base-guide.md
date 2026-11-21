# 知识库配置、访问与文件清单

本说明依据 2026-11-20 试用环境的换库验收快照、源码 Compose 和导入清单。线上访问及文件授权以当前部署配置为准。

## 1. 连接在哪里

| 使用方式 | 入口和身份 |
| --- | --- |
| 业务用户 | 从[试用工作台](https://106.14.226.88:18088/)登录并打开自己的分包任务。在有授权和原件关联的依据项中查看或下载引用原件。 |
| 浏览器访问应用依据 | `downloadBsReferenceFile` 使用登录会话调用 `GET /api/plus/blockchain-supervision/tasks/{task_id}/references/{reference_id}/file`。可随请求指定报告版本。后端按任务、用户、引用和当前授权执行校验。 |
| 后端访问 WeKnora | Compose 内连接 `http://weknora:8080/api/v1`。应用凭据在服务器读取，四个绑定限定为查询，并逐份限制可见文件。 |
| WeKnora 管理端 | 仓库 Compose 的 `weknora` 服务没有宿主机端口发布。已有材料只证明业务入口和内网 API，独立管理 UI 的已部署公网地址尚未核实。运维管理使用专用账号及经确认的内网访问方式。 |

对应实现为[前端 API](../../frontend/src/plus-ui/blockchain-supervision/lib/api.js)、[业务路由](../../backend/app/plus/blockchain_supervision/router.py)、[原件访问校验](../../backend/app/plus/blockchain_supervision/reference_access.py)和[容器编排](../../compose.yaml)。

## 2. 如何配置及排查

| 配置或文件 | 用途 |
| --- | --- |
| `WEKNORA_API_KEY_FILE` | 应用查询凭据文件，Compose 中映射为 `/data/knowledge/query-key`。仓库配置也支持 `WEKNORA_API_KEY`。前端通过应用会话访问，凭据保存在服务端。 |
| `BS_REFERENCE_MAPPING_FILE` | 容器内为 `/data/knowledge/reference-mapping.json`。按文件 SHA-256、集合、KB ID、文档 ID 建立原件与 WeKnora 的联系。 |
| `BS_WEKNORA_KB_MAP` 与业务库绑定 | 支持任务路由和集合标识映射。应用访问以业务 MySQL 的知识绑定和文件白名单为准。当前线上四个集合的文件授权数为 7、10、3、5。 |
| 本地目录 | `delivery-data/knowledge-source/` 是原始种子目录，`delivery-data/knowledge-config/` 存放本地映射及凭据等。本地文件与线上应用的 25 份授权并非同一配置版本。 |
| 线上目录 | 交接记录的部署目录为 `/data/engineering-subcontract/runtime`，其中 `delivery-data/knowledge-config/` 下保存现行映射和查询凭据。 |
| WeKnora 服务 | 使用 PostgreSQL/ParadeDB、知识文件卷、Redis 和 DocReader。迁移时需一并考虑文件、向量、映射、权限和配套的加密配置。 |

业务侧调用链为 `knowledge_runs.py` 或 `retrospective_knowledge.py` → `knowledge.py` 的任务路由 → `multi_kb_query.query_agent_knowledge` → `provider_client.py` 的 WeKnora 查询接口。下载原件经过 `reference_access.py`，固定条款、知识引用与任务快照各有追踪关系。

检索不到内容时，按集合路由、文件白名单、映射、文档状态、检索参数与引用定位依次核查。[knowledge_binding_setup.py](../../backend/app/standalone/knowledge_binding_setup.py) 的种子绑定用于初始化。已有线上 25 份绑定，以[换库验收记录](2026-11-20-knowledge-store-swap-acceptance.md)为维护基线。

## 3. 线上已授权文件

清单按换库后的 25 个文档 ID 与本地批量、试点及 DG/TJ 检索文本导入记录逐项关联。文件名采用导入名。OCR 与检索文本是便于召回的副本，页码、原文及规则判断应与对应原件交叉核对。

| 集合 | 文件数 | 切片与向量数 |
| --- | ---: | ---: |
| 上海依据 `road_shanghai_basis` | 7 | 184 |
| 国家依据 `road_national_basis` | 10 | 183 |
| 集团依据 `road_group_basis` | 3 | 27 |
| 资质标准 `shared_qualification` | 5 | 396 |
| 合计 | 25 | 790 |

Embedding 模型为 `text-embedding-v3`，维度 1024。

### 上海依据

- `01-上海市公路工程施工分包管理实施细则-OCR.md`
- `01-上海市公路工程施工分包管理实施细则-沪交行规2026-8号.pdf`
- `04-2025年分包单位报审及附件要求-6.20.pdf`
- `04-2025年分包单位报审及附件要求-OCR.md`
- `08-DGTJ08-2225-2017-施工现场专业人员配备标准-检索文本.txt`
- `08-DGTJ08-2225-2017-施工现场专业人员配备标准.pdf`
- `14-上海市公路工程施工分包管理实施细则政策解读.pdf`

### 国家依据

- `02-公路工程施工分包管理办法-交公路规2024-2号.pdf`
- `03-公路工程施工分包负面清单-2024年版-交办公路2024-6号.docx`
- `07-建筑工程施工发包与承包违法行为认定查处管理办法-建市规2019-1号.pdf`
- `09-公路水运工程安全生产监督管理办法-交通运输部令2017年第25号.pdf`
- `13-建筑施工特种作业人员管理规定-住建部令2025年第60号.pdf`
- `22-保障农民工工资支付条例-国务院令第724号.pdf`
- `23-企业安全生产费用提取和使用管理办法-财资2022-136号.pdf`
- `24-建筑工人实名制管理办法试行-建市2019-18号.pdf`
- `25-中华人民共和国特种设备安全法.pdf`
- `26-安全生产许可证条例-国务院令第397号.pdf`

### 集团依据

- `05-沪城公路2025-118号-OCR.md`
- `05-沪城公路2025-118号-上海城投分包管理办法.pdf`
- `06-008关于分包报审相关要求的通知.pdf`

### 资质标准

- `10a-建筑业企业资质标准-全文-建市2014-159号.pdf`
- `11-建筑业企业资质管理规定和资质标准实施意见-建市2015-20号.pdf`
- `12a-注册建造师执业工程规模标准-全文-建市2007-171号.pdf`
- `20-注册建造师管理规定-建设部令第153号.pdf`
- `21-建筑业企业资质管理规定-住建部令第22号.pdf`

## 4. 维护边界

知识库的文本用于检索候选依据。业务规则的适用对象、计算口径、证据要求和判定仍由 OpenSpec、规则数据及对应核查模块维护。引用前应核实文号、条号、适用条件和原文；合并同一来源的原件与检索副本时保留原件身份和定位。项目代码入口见[工程分包审查项目与代码导读](../guides/project-and-code-overview.md)。

准入沿用九维结果结构，倒查沿用逐条问题列表。规则评审发现依据缺失时，记录对应规则、缺少的规范或版本、需要入库的原件及验证方式，再按实际缺口补充文件。
