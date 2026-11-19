"""任务级模式；历史快照缺值时兼容准入，不写全局设置。"""
ADMISSION = "admission"
RETROSPECTIVE = "contract_retrospective"
MODES = {ADMISSION, RETROSPECTIVE}


def validate(value: str) -> str:
    if value not in MODES:
        raise ValueError("核查阶段仅支持准入阶段或合同倒查阶段")
    return value


def of(task) -> str:
    return validate((task.options_snapshot_json or {}).get("review_mode", ADMISSION))


def is_retrospective(task) -> bool:
    return of(task) == RETROSPECTIVE
