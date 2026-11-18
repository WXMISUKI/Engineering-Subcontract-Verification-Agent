"""显式结论语义；NULL/未知值不得推断为普通提示。"""

FINDING_SEMANTICS_MACHINE = "machine_conclusion"
FINDING_SEMANTICS_MANUAL_PROMPT = "manual_prompt"
FINDING_SEMANTICS_CODES = (FINDING_SEMANTICS_MACHINE, FINDING_SEMANTICS_MANUAL_PROMPT)


def is_manual_prompt(finding) -> bool:
    value = (finding.get("finding_semantics") if isinstance(finding, dict)
             else getattr(finding, "finding_semantics", None))
    return value == FINDING_SEMANTICS_MANUAL_PROMPT
