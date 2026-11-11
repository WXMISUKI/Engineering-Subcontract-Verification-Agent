"""计费域数据模型（2026-10-26，设计《设计-用户管理-积分计费-模型选择-20261026》§4）。

只放与**计费**相关的表：模型目录、价格版本、钱包、流水、冻结、账单、outbox。
用户管理（状态/最近登录/详情）与模型选择（权限/绑定/选择器）不在本期范围，
故 `users` 不扩展；钱包以 ``(user_type, user_id)`` 二元组归属，天然覆盖
web / miniapp / app 三种用户表。

SQLite 兼容：BIGINT 主键在 SQLite 不是 rowid 别名、不会自增，统一用
``BigInteger().with_variant(Integer, "sqlite")``；DECIMAL 用 ``Numeric(18, 3)``。

预留（设计 §18 第 4 条）：本期不建 ``product_skus`` 表，归因先落 ``agent_id``；
后续商品/SKU 按 ``agent_id`` 关联。价格与等级不进 ``agents`` 表。
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)

from ..database import Base

# 微积分：1 积分 = 1,000,000 微积分。金额一律整数，禁止浮点。
MICRO_PER_CREDIT = 1_000_000

BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class LlmProvider(Base):
    """一个 provider 一份密钥引用，其下挂多个模型（设计 §4.1）。

    ``api_key_ref`` 只存 **env 变量名**，不存明文；真正的 key 由调用侧按名字取。
    """

    __tablename__ = "llm_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(32), nullable=False, unique=True, comment="default / coze / dashscope / mimo")
    name = Column(String(64), nullable=False, default="", server_default=text("''"))
    protocol = Column(String(32), nullable=False, default="openai_compatible",
                      server_default=text("'openai_compatible'"),
                      comment="openai_compatible / anthropic / coze / mimo / local")
    base_url = Column(String(255), nullable=True, comment="上游地址；空=按 env 推导")
    api_key_ref = Column(String(128), nullable=True, comment="env/secret 引用名，不存明文")
    extra_json = Column(JSON, nullable=True)
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class LlmModel(Base):
    """平台模型目录（设计 §4.2）。"""

    __tablename__ = "llm_models"
    __table_args__ = (
        Index("idx_llm_models_provider_enabled", "provider_id", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(64), nullable=False, unique=True, comment="平台编码，如 qwen3.7-flash")
    display_name = Column(String(64), nullable=False, default="", server_default=text("''"))
    provider_id = Column(Integer, ForeignKey("llm_providers.id", ondelete="RESTRICT"), nullable=False)
    upstream_model = Column(String(128), nullable=False, default="", server_default=text("''"))
    unit_type = Column(String(16), nullable=False, default="token", server_default=text("'token'"),
                       comment="token / page / image / char / second")
    unit_scale = Column(Integer, nullable=False, default=1, server_default=text("1"),
                        comment="计价倍率：token/char=1000，page/image/second=1")
    context_size = Column(Integer, nullable=True)
    # 2026-11-02：官方最大输入上下文（百炼 /models/{id} 的 max_input_tokens）。
    # 管理员可在模型目录里调低 context_size，但不能超过这个上限。
    context_size_limit = Column(Integer, nullable=True,
                                comment="模型官方最大输入上下文；生效 context_size 不得超过它")
    max_output = Column(Integer, nullable=True, comment="预授权输出上限估算")
    supports_vision = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    supports_tools = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    supports_reasoning = Column(Boolean, nullable=False, default=False, server_default=text("0"))
    is_user_selectable = Column(Boolean, nullable=False, default=True, server_default=text("1"),
                                comment="用户选择器可见；本期不做选择器，仅目录属性")
    # 每模型独立 Key 模式下的 env/secret 引用名；不存明文。
    # shared 模式下忽略，统一用 provider.api_key_ref。
    api_key_ref = Column(String(128), nullable=True, comment="模型级 Key 的 env/secret 引用名；不存明文")
    is_default = Column(Boolean, nullable=False, default=False, server_default=text("0"),
                        comment="全局默认（业务上唯一）")
    enabled = Column(Boolean, nullable=False, default=True, server_default=text("1"))
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ModelPriceVersion(Base):
    """模型价格版本（设计 §4.3）。已发布不可改；调权=新版本。"""

    __tablename__ = "model_price_versions"
    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_model_price_version"),
        Index("idx_model_price_versions_model_status", "model_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(Integer, ForeignKey("llm_models.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False, comment="同模型自增")
    status = Column(String(16), nullable=False, default="draft",
                     comment="draft / published / archived")
    effective_from = Column(DateTime, nullable=True)
    effective_to = Column(DateTime, nullable=True)
    # token 四桶权重（微积分/千 token）
    cache_hit_input_micro_per_1k = Column(BigInteger, nullable=True)
    cache_miss_input_micro_per_1k = Column(BigInteger, nullable=True)
    cache_write_input_micro_per_1k = Column(BigInteger, nullable=True)
    output_micro_per_1k = Column(BigInteger, nullable=True)
    # 非 token 单价（微积分/单位）
    unit_micro_per_unit = Column(BigInteger, nullable=True)
    # 只拿到 total 时的兜底单价；NULL=不计费（不猜）
    total_fallback_micro_per_1k = Column(BigInteger, nullable=True)
    # 输入长度阶梯价（2026-11-05，对齐百炼）：[{"up_to_input_tokens": 32000,
    # "cache_hit_input_micro_per_1k": ..., "cache_miss_input_micro_per_1k": ...,
    # "cache_write_input_micro_per_1k": ..., "output_micro_per_1k": ...}, ...]。
    # NULL/空=单档价，用上面的基础四桶；有值时按**单次调用**输入 token 选档，
    # 聚合台账用 raw_usage_json.calls 求平均每调输入。
    tiers_json = Column(JSON, nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    published_at = Column(DateTime, nullable=True)
    note = Column(String(255), nullable=True, comment="调权原因")


class UserWallet(Base):
    """用户钱包（设计 §4.4）。可用 = balance_micro - frozen_micro。

    归属仍是 ``(user_type, user_id)`` 二元组。租户/企业池不在本期（对标 P1）；
    未来若引入租户，新增可空 ``tenant_id`` 列并让主键带上它即可，**不要**
    在本期预留占位列（空列会被误读成"已支持企业钱包"）。
    """

    __tablename__ = "user_wallets"

    user_type = Column(String(20), primary_key=True)
    user_id = Column(Integer, primary_key=True)
    balance_micro = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    frozen_micro = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    version = Column(Integer, nullable=False, default=0, server_default=text("0"),
                     comment="乐观锁版本")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CreditLedger(Base):
    """积分流水（只追加，设计 §4.5）。冲正=反向流水，绝不改行。"""

    __tablename__ = "credit_ledger"
    __table_args__ = (
        Index("idx_credit_ledger_user_created", "user_type", "user_id", "created_at"),
        Index("idx_credit_ledger_usage", "usage_id"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_type = Column(String(20), nullable=False)
    user_id = Column(Integer, nullable=False)
    entry_type = Column(String(16), nullable=False,
                        comment="grant / consume / refund / adjust / reversal")
    grant_source = Column(String(16), nullable=True,
                          comment="welcome / admin / promo / purchase（预留）")
    amount_micro = Column(BigInteger, nullable=False, comment="有符号")
    balance_after_micro = Column(BigInteger, nullable=False, comment="快照")
    ref_type = Column(String(32), nullable=True, comment="usage / admin_adjust / welcome")
    ref_id = Column(String(64), nullable=True)
    usage_id = Column(BigInteger, nullable=True)
    price_version_id = Column(Integer, nullable=True)
    operator_id = Column(Integer, nullable=True)
    reason = Column(String(255), nullable=True)
    idempotency_key = Column(String(120), nullable=False, unique=True)
    # 计费归因（设计 §18 第 4 条）：场景/智能体/任务范围，历史行与旧调用为 NULL。
    billing_scene = Column(String(32), nullable=True, comment="scenes.py 定义的场景编码")
    agent_id = Column(Integer, nullable=True, comment="智能体归属；NULL=解析不到，不猜")
    billing_scope_type = Column(String(32), nullable=True,
                                comment="任务范围类型 as_session/oc_task/sr_conversation/bs_task/sc_task")
    billing_scope_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class CreditHold(Base):
    """预授权/结算/释放（设计 §4.6）。"""

    __tablename__ = "credit_holds"
    __table_args__ = (
        Index("idx_credit_holds_user_status", "user_type", "user_id", "status"),
        Index("idx_credit_holds_scope", "scope_type", "scope_id"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    user_type = Column(String(20), nullable=False)
    user_id = Column(Integer, nullable=False)
    scope_type = Column(String(32), nullable=True,
                        comment="as_session / oc_task / sr_conversation / bs_task / chat_conversation")
    scope_id = Column(String(64), nullable=True)
    model_id = Column(Integer, nullable=True)
    held_micro = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    settled_micro = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    status = Column(String(16), nullable=False, default="held",
                    comment="held / settled / released / expired")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    settled_at = Column(DateTime, nullable=True)


class UsageCharge(Base):
    """通用账单，与台账 1:1（设计 §4.8）。``usage_id`` 唯一保证不重复出账。"""

    __tablename__ = "usage_charges"
    __table_args__ = (
        UniqueConstraint("usage_id", name="uq_usage_charges_usage"),
        Index("idx_usage_charges_user_created", "user_type", "user_id", "created_at"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    usage_id = Column(BigInteger, nullable=False)
    user_type = Column(String(20), nullable=False)
    user_id = Column(Integer, nullable=False)
    model_id = Column(Integer, nullable=True)
    price_version_id = Column(Integer, nullable=True)
    unit_type = Column(String(16), nullable=False, default="token", server_default=text("'token'"))
    billable_quantity = Column(Numeric(18, 3), nullable=True)
    cache_hit_input_tokens = Column(Integer, nullable=True)
    cache_miss_input_tokens = Column(Integer, nullable=True)
    cache_write_input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    raw_micro = Column(BigInteger, nullable=False, default=0, server_default=text("0"),
                       comment="未取整")
    charged_micro = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    status = Column(String(16), nullable=False, default="rated", server_default=text("'rated'"),
                    comment="rated / partial / unknown / legacy / reversed / price_missing")
    # 计费归因（从台账同名列复制，便于按任务/智能体聚合；历史行为 NULL）。
    billing_scene = Column(String(32), nullable=True)
    agent_id = Column(Integer, nullable=True)
    billing_scope_type = Column(String(32), nullable=True)
    billing_scope_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class BillingOutbox(Base):
    """出账任务（设计 §4.9，不丢账的关键）。一条台账一条任务。"""

    __tablename__ = "billing_outbox"
    __table_args__ = (
        UniqueConstraint("usage_id", name="uq_billing_outbox_usage"),
        Index("idx_billing_outbox_status_retry", "status", "next_retry_at"),
    )

    id = Column(BigIntPK, primary_key=True, autoincrement=True)
    usage_id = Column(BigInteger, nullable=False)
    status = Column(String(16), nullable=False, default="pending",
                     comment="pending / processing / done / failed")
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_retry_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class BillingSettings(Base):
    """计费设置（单行 id=1，设计 §4.13）。"""

    __tablename__ = "billing_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    enforce_mode = Column(String(16), nullable=False, default="shadow",
                          comment="off / shadow / enforce")
    default_model_id = Column(Integer, nullable=True)
    default_permission_policy = Column(String(32), nullable=False, default="all_enabled")
    # Key 归属模式：shared=provider 一把 key 多模型；per_model=模型各自 api_key_ref。
    model_key_mode = Column(String(16), nullable=False, default="shared", server_default=text("'shared'"),
                            comment="shared / per_model")
    welcome_credit_micro = Column(BigInteger, nullable=False, default=100_000_000,
                                  comment="新用户固定额度，默认 100 积分")
    treat_unreported_cache_as_miss = Column(Boolean, nullable=False, default=True,
                                            server_default=text("1"))
    overdraft_limit_micro = Column(BigInteger, nullable=False, default=0)
    min_charge_micro = Column(BigInteger, nullable=False, default=0)
    # 无输入规模可估时的最低余额闸门（enforce 生效）。新建默认 100 积分（1 元，
    # 与欢迎额度对齐）；管理员可显式改 0 表示“不额外收紧”。
    min_balance_micro = Column(BigInteger, nullable=False, default=100_000_000,
                               server_default=text("100000000"),
                               comment="Plus 等无法预估任务的启动最低余额")
    # 014 一次性迁移标记：false=尚未判定过旧默认值。启动时对未标记行做一次
    # 「旧默认 0 → 100 积分」回填并置 true；管理员显式保存 min_balance（含 0）
    # 也会置 true，之后启动不再覆盖。
    min_balance_default_migrated = Column(
        Boolean, nullable=False, default=True, server_default=text("1"),
        comment="014 旧默认0一次性回填标记",
    )
    # 平台预期输出上限：模型没有 max_output 且调用方没传 max_tokens 时用它估算，
    # 界面上必须标注“按平台预期输出上限估算”，不冒充真实上限。
    default_output_tokens = Column(Integer, nullable=False, default=1024,
                                   server_default=text("1024"))
    # Plus 任务拿不到真实输入规模时的场景默认估算（明确标注“按场景估算”）。
    scene_default_input_tokens = Column(Integer, nullable=False, default=2000,
                                        server_default=text("2000"))
    scene_default_output_tokens = Column(Integer, nullable=False, default=1024,
                                         server_default=text("1024"))
    billing_epoch = Column(DateTime, nullable=True, comment="计费起点；早于此的台账 legacy")
    require_ledger = Column(Boolean, nullable=False, default=True, server_default=text("1"),
                            comment="enforce 模式下台账写不进去是否拒绝请求")
    # 法币锚（设计 §18 第 3 条）：1 积分 = 1/credits_per_yuan 元。整数，管理端可调。
    credits_per_yuan = Column(Integer, nullable=False, default=100, server_default=text("100"),
                              comment="每元多少积分；默认 100（1 积分 = 0.01 元）")
    # 默认价基线迁移标记（2026-11-05）：与代码里的 BASELINE_KEY 一致时跳过；
    # 用来保证「应用新价 + 重算历史 token 账单」每次库只跑一次。
    price_baseline_key = Column(String(64), nullable=True,
                                comment="默认价基线迁移标记；匹配代码基线则跳过")
    updated_by = Column(Integer, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ModelBinding(Base):
    """会话/任务级模型绑定（设计 §8.1、§4.11）。

    归属与 scope 一同落库：同一 ``(scope_type, scope_id)`` 只允许一条，避免不同
    用户对同号 scope（尤其 as_session 的自增/短 id 体系）互相覆盖。``scope_id`` 用
    字符串统一承接 int 任务 id 与 36 位会话 id。

    本表只记「用户当前选择」；解析时若绑定模型已停用/移出目录，回退全局默认，
    但**不静默改写绑定值**（用户下次仍能看到自己的选择与失效原因）。
    """

    __tablename__ = "model_bindings"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id", name="uq_model_binding_scope"),
        Index("idx_model_bindings_user", "user_type", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_type = Column(String(20), nullable=False, server_default=text("''"),
                       comment="绑定归属用户类型")
    user_id = Column(Integer, nullable=False, server_default=text("0"),
                     comment="绑定归属用户 id")
    scope_type = Column(String(32), nullable=False,
                        comment="as_session/oc_task/sr_conversation/sc_task/bs_task")
    scope_id = Column(String(64), nullable=False)
    model_code = Column(String(64), nullable=False, comment="所选模型目录 code")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class BillingEstimate(Base):
    """任务级预估（设计 §18 第 2 条）。

    预估与结算必须引用**同一个价格版本**：这里存 ``price_version_id``，
    结算时由账单携带的版本比对，不一致就在任务页显式提示，不允许两套账
    静默不一致。

    归属：预估必须带 ``user_type/user_id``，唯一键为
    ``(user_type, user_id, scope_type, scope_id)``；否则不同用户对同一 scope
    （尤其 chat/as_session 的自增 id）会互相覆盖、读到对方的账。
    """

    __tablename__ = "billing_estimates"
    __table_args__ = (
        UniqueConstraint(
            "user_type", "user_id", "scope_type", "scope_id",
            name="uq_billing_estimate_owner_scope",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_type = Column(String(20), nullable=False, server_default=text("''"),
                       comment="预估归属用户类型")
    user_id = Column(Integer, nullable=False, server_default=text("0"),
                     comment="预估归属用户 id")
    scope_type = Column(String(32), nullable=False,
                        comment="as_session/oc_task/sr_conversation/bs_task/sc_task/chat")
    scope_id = Column(String(64), nullable=False)
    model_code = Column(String(64), nullable=True)
    scene = Column(String(32), nullable=True)
    agent_id = Column(Integer, nullable=True)
    price_version_id = Column(Integer, nullable=True)
    estimated_micro = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    input_tokens_est = Column(Integer, nullable=True)
    output_tokens_est = Column(Integer, nullable=True)
    # 估算依据：request_text / scene_default / model_max_output / platform_default，
    # 任务页据此标注“按场景/平台预期估算”，不把假设说成真实输入。
    input_basis = Column(String(24), nullable=True)
    output_basis = Column(String(24), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())