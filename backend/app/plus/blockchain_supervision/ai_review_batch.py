"""为同一步模型判断规划有界批次，不执行模型或写入任务。"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from copy import deepcopy
from openai import APIConnectionError
from ..request_errors import classify_request_error

from .ai_evidence import MODEL_EVIDENCE_INSTRUCTIONS, model_scope_description, validate_ai_proposal
from .semantic_review import InputBudgetExceeded, InvalidModelResponse, MAX_REQUEST_JSON_CHARS, request_json

_MAX_SEGMENT_CHARS = 16000
_MAX_BATCH_MATERIALS = 12
# 真实材料的 64 条批次出现多次 120 秒超时；减小批次，仍由超时拆批兜底。
_MAX_BATCH_ENTRIES = 32
_TABLE_NOISE = re.compile(r"(?<![A-Za-z])(?:\\{0,2}hline[\s]*){5,}")
_EMPTY_TABLE_ROWS = re.compile(r"(?m)^(?:[ \t]*\|[ \t|]*\|[ \t]*(?:\r?\n|$)){5,}")
CHECKPOINT_SCHEMA_VERSION = "bs-ai-batch-v18"
logger = logging.getLogger(__name__)
_MAX_RETRY_AFTER_SECONDS = 15.0


class BatchRequestBudget:
    """原始批次及其拆分共享总请求数与单调时钟期限。"""

    def __init__(self, max_requests=4, max_seconds=360.0):
        self.remaining_requests = max_requests
        self.sent_requests = 0
        self.deadline = time.monotonic() + max_seconds

    def remaining_seconds(self):
        return max(0.0, self.deadline - time.monotonic())

    def take_request(self):
        remaining = self.remaining_seconds()
        if self.remaining_requests <= 0 or remaining <= 0:
            raise TimeoutError('模型批次请求预算已耗尽')
        self.remaining_requests -= 1
        self.sent_requests += 1
        return remaining


async def _sleep_before_retry(delay):
    await asyncio.sleep(delay)


def _retry_delay(exc, budget):
    headers = getattr(getattr(exc, 'response', None), 'headers', {}) or {}
    if str(headers.get('x-should-retry', '')).lower() == 'false':
        return None
    value = headers.get('retry-after')
    if value is not None:
        if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+)?', value.strip()):
            return None
        delay = float(value)
        if delay > _MAX_RETRY_AFTER_SECONDS:
            return None
        delay = min(_MAX_RETRY_AFTER_SECONDS, delay + random.uniform(0.5, 1.0))
    else:
        delay = random.uniform(0.5, 1.0)
    if budget is not None and (budget.remaining_requests <= 0 or delay >= budget.remaining_seconds()):
        return None
    return delay


class InvalidBatchResponse(ValueError):
    """模型响应未满足批次输出契约。"""


async def _request_review_json(client, system, payload, *, on_usage=None, max_attempts=2,
                               split_on_timeout=False, diagnostic_context=None, request_budget=None):
    """有界重试可恢复错误，不记录材料与模型原文。"""
    chars = len(json.dumps(payload, ensure_ascii=False))
    entries = len(payload.get("items") or [])
    for attempt in range(max_attempts):
        started = time.monotonic()
        try:
            request_options = {}
            if request_budget is not None:
                request_options['timeout_seconds'] = request_budget.take_request()
            context = {**(diagnostic_context or {}), 'request_attempt':
                       request_budget.sent_requests if request_budget is not None else attempt + 1}
            response = await request_json(client, system, payload, on_usage=on_usage,
                                          diagnostic_context=context, **request_options)
            if not isinstance(response.get("proposals"), list) or len(response["proposals"]) > 200:
                raise InvalidBatchResponse("模型返回格式无效")
            logger.info("AI batch request completed task=%s chars=%s entries=%s attempt=%s elapsed_ms=%s",
                        payload.get("task_id"), chars, entries, attempt + 1, int((time.monotonic() - started) * 1000))
            return response
        except Exception as exc:
            category = classify_request_error(exc)
            if isinstance(exc, (json.JSONDecodeError, InvalidBatchResponse, InvalidModelResponse)):
                category = 'invalid_response'
            logger.warning("AI batch request failed task=%s chars=%s entries=%s category=%s attempt=%s elapsed_ms=%s",
                           payload.get("task_id"), chars, entries, category, attempt + 1, int((time.monotonic() - started) * 1000))
            transient = category in {'timeout', 'rate_limit', 'service_error', 'invalid_response'} or isinstance(exc, APIConnectionError)
            if not transient or attempt + 1 == max_attempts or (category == "timeout" and split_on_timeout):
                raise
            delay = _retry_delay(exc, request_budget)
            if delay is None:
                raise
            await _sleep_before_retry(delay)


def _identity(item: dict) -> tuple[str, int | str] | None:
    if not isinstance(item, dict) or ("finding_id" in item) == ("candidate_id" in item):
        return None
    if "finding_id" in item:
        value = item["finding_id"]
        return ("finding_id", value) if type(value) is int and value > 0 else None
    value = item["candidate_id"]
    return ("candidate_id", value) if isinstance(value, str) and re.fullmatch(r"nc-[0-9a-f]{64}", value) else None


def to_wire_batch(batch: dict) -> dict:
    """批内共用相同原文，逐项保留可引用的材料与依据索引。"""
    materials, references, items = [], [], []
    material_keys, reference_keys = {}, {}
    for entry in batch["items"]:
        material = entry["material"]
        material_key = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if material_key not in material_keys:
            material_keys[material_key] = len(materials)
            materials.append(material)
        ref_indexes = []
        for reference in entry["references"]:
            ref_key = json.dumps(reference, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if ref_key not in reference_keys:
                reference_keys[ref_key] = len(references)
                references.append(reference)
            ref_indexes.append(reference_keys[ref_key])
        items.append({key: value for key, value in entry.items()
                      if key not in {"material", "references", "incomplete_material_scope"}} | {
            "材料范围说明": model_scope_description(entry.get("incomplete_material_scope", False)),
            "material_index": material_keys[material_key], "reference_indexes": ref_indexes})
    return {key: value for key, value in batch.items() if key != "items"} | {
        "materials": materials, "references": references, "items": items}


def _size(payload: dict) -> int:
    return len(json.dumps(to_wire_batch(payload), ensure_ascii=False))


def _noise_spans(text):
    return sorted([match.span() for pattern in (_TABLE_NOISE, _EMPTY_TABLE_ROWS)
                   for match in pattern.finditer(text)])


def plan_review_batches(items: list[dict], *, max_chars: int = MAX_REQUEST_JSON_CHARS) -> dict:
    """按结论主体隔离批次，保留每个材料页的原始偏移；无法处理时逐项说明。"""
    if type(max_chars) is not int or max_chars <= 0 or max_chars > MAX_REQUEST_JSON_CHARS:
        raise ValueError("单次请求预算无效")
    batches, limitations, scopes = [], [], {}
    seen_ids = set()
    for item in items:
        identity = _identity(item)
        if identity is None or identity in seen_ids:
            raise ValueError("核查结论标识缺失或重复")
        seen_ids.add(identity)
        identity_field = {identity[0]: identity[1]}
        subject_id, pages, references = item.get("subject_id"), item.get("materials"), item.get("references")
        if (type(item.get("task_id")) is not int or not isinstance(item.get("item_id"), str) or not item["item_id"]
                or (subject_id is not None and type(subject_id) is not int)
                or not isinstance(pages, list) or not pages or not isinstance(references, list) or not references
                or not isinstance(item.get("limitations", []), list)
                or any(not isinstance(value, str) for value in item.get("limitations", []))
                or type(item.get("incomplete_material_scope", False)) is not bool
                or (item.get("required_subject_name") is not None
                    and (not isinstance(item["required_subject_name"], str) or not item["required_subject_name"].strip()))):
            limitations.append({**identity_field, "reason": "context_incomplete"})
            continue
        if any(not isinstance(ref, dict) or not isinstance(ref.get("reference_id"), str)
               or not ref["reference_id"] or not isinstance(ref.get("text"), str) or not ref["text"].strip()
               for ref in references) or len({ref["reference_id"] for ref in references}) != len(references):
            limitations.append({**identity_field, "reason": "context_incomplete"})
            continue
        if any(not isinstance(page, dict) or page.get("task_id") != item["task_id"]
               or type(page.get("material_id")) is not int or type(page.get("page")) is not int or page["page"] < 1
               or not isinstance(page.get("text"), str) or not page["text"].strip()
               or type(page.get("source_start", 0)) is not int or page.get("source_start", 0) < 0
               or ("source_end" in page and (type(page["source_end"]) is not int
                   or page["source_end"] != page.get("source_start", 0) + len(page["text"])))
               or (page.get("subject_id") is not None and type(page.get("subject_id")) is not int)
               or (subject_id is not None and page.get("subject_id") != subject_id) for page in pages):
            limitations.append({**identity_field, "reason": "context_incomplete"})
            continue
        scope = {"task_id": item["task_id"], "subject_id": subject_id,
                 "required_subject_name": item.get("required_subject_name")}
        entries, budget_failed = [], False
        removed_noise = any(_noise_spans(page["text"]) for page in pages)
        for page in pages:
            text = page["text"]
            spans, cursor = [], 0
            for noise_start, noise_end in _noise_spans(text):
                if noise_start > cursor and text[cursor:noise_start].strip():
                    spans.append((cursor, noise_start))
                cursor = max(cursor, noise_end)
            if text[cursor:].strip():
                spans.append((cursor, len(text)))
            for span_start, span_end in spans:
                start = span_start
                while start < span_end:
                    end = min(span_end, start + _MAX_SEGMENT_CHARS)
                    entry = {**identity_field, "item_id": item.get("item_id"), "subject_id": subject_id,
                             "kind": item.get("kind"),
                             "item_label": item.get("item_label") or item["item_id"],
                             "incomplete_material_scope": item.get("incomplete_material_scope", False) or removed_noise,
                             "references": [{"reference_id": ref["reference_id"], "text": ref["text"]} for ref in references],
                             "limitations": deepcopy(item.get("limitations") or []) + (
                                 ["材料含重复格式噪声，相关页面需结合原件核实"] if removed_noise else []),
                             "material": {"task_id": page["task_id"], "subject_id": page.get("subject_id"),
                                          "material_id": page["material_id"], "page": page["page"],
                                          "text": text[start:end], "text_start": page.get("source_start", 0) + start,
                                          "text_end": page.get("source_start", 0) + end}}
                    while end > start and _size({**scope, "items": [entry]}) > max_chars:
                        end = start + (end - start) // 2
                        entry["material"].update(text=text[start:end], text_end=page.get("source_start", 0) + end)
                    if end == start:
                        budget_failed = True
                        break
                    entries.append(entry)
                    start = end
                if budget_failed:
                    break
            if budget_failed:
                break
        if budget_failed or not entries:
            limitations.append({**identity_field, "reason": "input_budget_exceeded" if budget_failed or not removed_noise else "context_incomplete"})
            continue
        scope_key = (item["task_id"], subject_id, scope["required_subject_name"])
        grouped = scopes.setdefault(scope_key, {"scope": scope, "materials": {}})["materials"]
        for entry in entries:
            key = json.dumps(entry["material"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            grouped.setdefault(key, []).append(entry)
    for grouped in scopes.values():
        batch, material_keys = None, set()
        for material_key, entries in grouped["materials"].items():
            for entry in entries:
                if (batch is None or len(batch["items"]) >= _MAX_BATCH_ENTRIES
                        or (material_key not in material_keys and len(material_keys) >= _MAX_BATCH_MATERIALS)
                        or _size({**batch, "items": [*batch["items"], entry]}) > max_chars):
                    batch = {**grouped["scope"], "items": []}
                    batches.append(batch)
                    material_keys = set()
                batch["items"].append(entry)
                material_keys.add(material_key)
    # 单项跨请求时，每次模型只见到该项的一部分，即使原始输入完整也不能
    # 用某一批缺少某证据来证明它在整份材料中不存在。
    occurrences = {}
    for batch in batches:
        for identity in {_identity(entry) for entry in batch["items"]}:
            occurrences[identity] = occurrences.get(identity, 0) + 1
    for batch in batches:
        for entry in batch["items"]:
            if occurrences[_identity(entry)] > 1:
                entry["incomplete_material_scope"] = True
    return {"batches": batches, "limitations": limitations}


async def execute_review_batch(*, client, batch: dict, on_usage=None, max_attempts=2,
                               split_on_timeout=False, diagnostic_context=None, request_budget=None) -> list[dict]:
    """一次调用后逐结论校验双侧引用；结果仅供人工复核，不负责保存。"""
    entries = batch.get("items") or []
    grouped = {}
    for entry in entries:
        identity = _identity(entry)
        if identity is None:
            raise ValueError("批次核查标识无效")
        grouped.setdefault(identity, []).append(entry)
    results = {key: {key[0]: key[1], "proposals": [], "model_proposal_count": 0, "rejection_reasons": [],
                     "limitations": list(dict.fromkeys(message for entry in group
                         for message in entry.get("limitations") or [] if isinstance(message, str))),
                     "execution_status": "completed"} for key, group in grouped.items()}
    for key, group in grouped.items():
        if any(entry.get("incomplete_material_scope") for entry in group):
            results[key]["limitations"].append("本次仅核对部分材料，确定性建议需要完整材料或人工核实")
    identity_example = ('"candidate_id":"候选标识"' if grouped and all(key[0] == "candidate_id" for key in grouped)
                        else '"finding_id":整数')
    candidate_instruction = (
        '负面清单候选须逐项比对，仅当材料原文包含与该条款有关的具体事实且需要人工处理时提出建议；'
        '没有具体相关材料事实时不要生成逐条待办，仅未在片段中看到条款内容不能作为问题。'
        '证据不足不能判为未命中，也不能推断不存在；无可提出建议时返回空 proposals。'
        if grouped and all(key[0] == "candidate_id" for key in grouped) else ''
    )
    try:
        response = await _request_review_json(client,
            "逐项核查输入，在有需要时提出待人工核实的建议，不推断缺失事实，不输出无风险结论。"
            "每项只能引用该项输入的材料及依据，引用必须是同一页的连续原文。"
            "每项的 material_index 和 reference_indexes 指向本次输入中的材料和依据。"
            "指定主体名称时每条材料引文必须包含该名称。"
            "仅提供文本时不能根据文字缺少图形而断言网络图缺失，须提示查看原页。"
            + candidate_instruction + MODEL_EVIDENCE_INSTRUCTIONS +
            '输出 JSON 对象：{"proposals":[{' + identity_example + ',"item_id":"核查项",'
            '"subject_id":主体ID或null,"status":"hit或undecidable","message":"说明",'
            '"material_evidence":[{"material_id":整数,"page":整数,"quote":"材料原文"}],'
            '"rule_evidence":[{"reference_id":"依据ID","quote":"依据原文"}]}]}。'
            '两个 evidence 均为数组，每条建议至少各包含一条。'
            '输入为 candidate_id 的项须原样返回 candidate_id 且不得返回 finding_id；'
            '输入为 finding_id 的项须原样返回 finding_id 且不得返回 candidate_id。',
            to_wire_batch(batch), on_usage=on_usage, max_attempts=max_attempts,
            split_on_timeout=split_on_timeout, diagnostic_context=diagnostic_context, request_budget=request_budget)
        proposals = response.get("proposals")
        if not isinstance(proposals, list) or len(proposals) > 200:
            raise InvalidBatchResponse("模型返回格式无效")
    except Exception as exc:
        if isinstance(exc, InputBudgetExceeded):
            category = "input_budget"
        elif isinstance(exc, TimeoutError):
            category = "timeout"
        elif isinstance(exc, (json.JSONDecodeError, InvalidBatchResponse, InvalidModelResponse)):
            category = "invalid_response"
        else:
            category = classify_request_error(exc)
        message = {
            "input_budget": "输入超出模型处理范围，本批未完成",
            "timeout": "模型响应超时，本批未完成",
            "invalid_response": "模型返回格式无法解析，本批未完成",
            "rate_limit": "模型服务请求受限（HTTP 429），本批未完成",
            "authentication": "模型服务认证、权限或额度异常，本批未完成",
            "service_error": "模型服务暂时不可用，本批未完成",
            "model_call_error": "模型调用异常，本批未完成",
        }[category]
        for result in results.values():
            result["execution_status"] = "failed"
            result["failure_category"] = category
            result["limitations"].append(message + "，请人工核实或重试")
        return list(results.values())
    for proposal in proposals:
        key = _identity(proposal)
        if key is None or key not in grouped:
            for result in results.values():
                result["execution_status"] = "partial"
                result["limitations"].append("模型返回了无法关联本批核查项的建议，已排除，请人工核实")
                result["rejection_reasons"].append("模型返回了无法关联本批核查项的建议")
            continue
        results[key]["model_proposal_count"] += 1
        group = grouped[key]
        first = group[0]
        materials = [entry["material"] for entry in group]
        references = first["references"]
        if any(entry["item_id"] != first["item_id"] or entry["subject_id"] != first["subject_id"]
               or entry["references"] != references
               or entry.get("incomplete_material_scope", False) != first.get("incomplete_material_scope", False)
               for entry in group):
            results[key]["execution_status"] = "partial"
            results[key]["limitations"].append("本项输入不一致，建议未采用")
            results[key]["rejection_reasons"].append("本项输入不一致")
            continue
        validation = validate_ai_proposal(proposal, task_id=batch["task_id"], subject_id=first["subject_id"],
            item_id=first["item_id"], materials=materials, references=references,
            required_subject_name=batch.get("required_subject_name"),
            incomplete_material_scope=first.get("incomplete_material_scope", False),
            kind=first.get("kind"))
        if validation.accepted:
            results[key]["proposals"].append(validation.proposal)
        else:
            results[key]["execution_status"] = "partial"
            results[key]["rejection_reasons"].append(validation.reason)
            results[key]["limitations"].append(validation.reason if first["item_id"] == "RC-A08" and proposal.get("status") == "hit"
                                               else "部分模型建议的证据无法核验，已排除，请人工核实")
    for result in results.values():
        result["assessment_incomplete"] = not result["proposals"]
        if not result["proposals"]:
            result["limitations"].append("本批未提出可复核建议，不能据此判定无问题")
        result["limitations"] = list(dict.fromkeys(result["limitations"]))
        result["rejection_reasons"] = list(dict.fromkeys(result["rejection_reasons"]))
    return list(results.values())
