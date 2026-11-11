"""计费服务层（2026-10-26）。

分层：
- ``normalizer``  上游 usage → token 四桶 / 非 token 数量，未知绝不当 0；
- ``pricing``     用量 + 价格版本 → 微积分，四舍五入到 1 微；
- ``registry``    模型目录、默认模型、生效价格版本、种子；
- ``ledger``      统一用量台账（start / finish / 入 outbox）；
- ``outbox``      出账 worker（幂等：usage_charges.usage_id + credit_ledger.idempotency_key）；
- ``wallet``      钱包/流水/预授权；
- ``reconciler``  对账与告警；
- ``instrument``  统一调用包装器与当前计费主体上下文；
- ``scenes``      计费场景（scene）的唯一定义点；
- ``attribution`` 智能体归因解析（conversation / as_session / Plus task）；
- ``money``       微积分 / 积分 / 元 三种口径的唯一换算点；
- ``estimates``   任务级预估与实际（同价格版本比对、402 门槛）；
- ``model_keys``  模型 Key 归属解析（shared / per_model / 回退，绝不外泄 Key 值）。
"""