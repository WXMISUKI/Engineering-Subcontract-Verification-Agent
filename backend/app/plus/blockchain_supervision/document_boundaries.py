"""独立文档切段边界，不用于认证材料真实性或判定提交义务已履行。"""
import re

_TITLE = re.compile(r"^[^:：|]{0,80}(?:证书|操作证|人员证|身份证|证明|执照|许可证|合同书?|名单|名册|承诺书|记录|考勤表|施工方案|方案报审表|方案审批表|通知单|检验报告|变更报审表|保函|保险单|凭证|回单|收据)"
                    r"(?:正本|副本|[（(][^（）()\n]{0,20}[）)])?$")
_AGREEMENT = re.compile(r"^(?:分包)?合同(?:变更|补充)协议(?:正本|副本|[（(](?:正本|副本|复印件|电子版)[）)])?$")


def is_document_boundary(line):
    plain = re.sub(r"\s+", "", str(line or "")).strip(" #")
    # 意见中的整改要求不是独立标题，即使以证书或报告名称结尾。
    if re.match(r"^(?:应当?|须|必须|需要?|请|建议|不应|未提供)", plain):
        return False
    if re.search(r"[，；。]|不替代|不构成|不等于|(?:应当?|须|需要?|必须)[^:：|]{0,8}(?:提供|提交|开设|支付|承担|配合|建立|报送|出具|开具)", plain):
        return False
    return bool(_TITLE.fullmatch(plain) or _AGREEMENT.fullmatch(plain))
