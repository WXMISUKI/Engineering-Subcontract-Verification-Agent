"""结论说明适配既有数据库列，完整原材料及读取限制仍由材料记录保留。"""
from .models import BsFinding


def display_finding_message(finding, negative_coverage=None, timeout_incomplete=False) -> str:
    """展示规则覆盖缺口时不冒充后续 AI 候选核查的最终进度。"""
    message = finding.message or ""
    if (finding.item_id == "W16-02" and finding.status == "undecidable"
            and message.startswith("不可判定：负面清单核查覆盖不足")
            and "当前目录未登记条目" not in message):
        context = ""
        if (isinstance(negative_coverage, dict) and negative_coverage.get("state") == "available"
                and type(negative_coverage.get("ai_attempted")) is int
                and type(negative_coverage.get("ai_unresolved")) is int
                and negative_coverage["ai_attempted"] > 0):
            context = (f"已按主体对 {negative_coverage['ai_attempted']} 项负面清单内容开展 AI 辅助核对；"
                       f"共有 {negative_coverage['ai_unresolved']} 项（按主体计）的辅助结果未完成或仍需核实。")
        message = ("无法核实：现有核查结果尚不足以确认分包范围是否涉及负面清单中的主体工程或关键性工作。"
                   + context + "请结合合同约定的分包范围、工程内容和负面清单核对；"
                   "不需要重复提供负面清单，具体情况仍需人工确认。")
    if timeout_incomplete:
        message += "；本项 AI 辅助核查因超时未完成，请结合材料和规则依据人工核实。"
    return message


def bounded_finding_message(message: str) -> str:
    limit = BsFinding.__table__.c.message.type.length
    if len(message) <= limit:
        return message
    suffix = "…（完整内容请结合材料提示及原件核对）"
    return message[:limit - len(suffix)] + suffix
