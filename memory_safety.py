"""Shared pure safety predicates for durable Memory text."""

from __future__ import annotations

import re
from typing import Any


_INSTRUCTION_SENSITIVE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"\b(?:ignore|disregard|override|bypass)\b.{0,100}\b(?:previous|prior|system|developer|instruction|policy|permission)\b",
        r"\b(?:reveal|exfiltrate|upload|send|print|return)\b.{0,100}\b(?:api[ _-]?key|access[ _-]?token|password|credential|secret)\b",
        r"\b(?:call|invoke|execute|run)\b.{0,80}\b(?:tool|command|shell|terminal|powershell)\b",
        r"\b(?:always|automatically|auto)\b.{0,60}\b(?:approve|authorize|grant|allow|permit)\b",
        r"\bnever\b.{0,60}\b(?:ask|request|require|wait\s+for)\b.{0,60}\b(?:approval|permission|authorization|confirmation)\b",
        r"\b(?:skip|bypass|disable|ignore)\b.{0,60}\b(?:approval|permission|authorization|confirmation|checkpoint|rollback)\b",
        r"\bwithout\b.{0,60}\b(?:approval|permission|authorization|confirmation|asking)\b",
        r"\bdo\s+not\b.{0,30}\b(?:ask|request|require|wait\s+for)\b.{0,30}\b(?:approval|permission|authorization|confirmation)\b",
        r"\bno\b.{0,20}\b(?:approval|permission|authorization|confirmation)\b.{0,20}\b(?:is\s+)?required\b",
        r"\bno\b.{0,30}\b(?:approvals?|permissions?|authorizations?|confirmations?)\b.{0,50}\b(?:future|any|all|every)\b.{0,30}\b(?:write|edit|change|delete|modification)s?\b",
        r"\b(?:permission|authorization|approval)\b.{0,20}\b(?:is\s+)?granted\b.{0,40}\b(?:all|any|every|future)\b.{0,30}\b(?:edit|change|write|modification)s?\b",
        r"\bauthori[sz]ed\b.{0,30}\b(?:to\s+)?(?:modify|edit|write|change|delete)\b",
        r"\bfuture\b.{0,30}\b(?:change|edit|write|modification)s?\b.{0,30}\b(?:already\s+)?(?:approved|authorized|permitted|allowed)\b",
        r"(?<!not\s)(?<!never\s)(?<!don't\s)\bgrant(?:ed|ing)?\b.{0,20}\b(?:permission|authorization|approval)\b",
        r"[\"']role[\"']\s*:\s*[\"'](?:system|developer|assistant)[\"']",
        r"(?:^|\s)(?:system|developer|assistant)\s*:",
        r"<\/?(?:system|developer|assistant)>|\[/?INST\]",
        r"(?:忽略|无视|無視|绕过|繞過|覆盖|覆蓋).{0,60}(?:系统|系統|开发者|開發者|指令|权限|權限|规则|規則)",
        r"(?:泄露|洩漏|发送|發送|上传|上傳|输出|輸出).{0,60}(?:密钥|密鑰|令牌|權杖|密码|密碼|凭证|憑證|秘密)",
        r"(?:调用|呼叫|执行|執行|运行|運行).{0,40}(?:工具|命令|终端|終端)",
        r"(?:始终|始終|总是|總是|永远|永遠|自动|自動).{0,30}(?:批准|核准|授权|授權|允许|允許|同意)",
        r"(?:不要|无需|無需|不用|永不).{0,30}(?:询问|詢問|请求|請求|要求|等待).{0,30}(?:批准|核准|权限|權限|授权|授權|确认|確認)",
        r"(?:无需|無需|不用|不必).{0,20}(?:批准|核准|权限|權限|授权|授權|确认|確認)",
        r"(?:跳过|跳過|绕过|繞過|关闭|關閉).{0,30}(?:批准|核准|权限|權限|授权|授權|确认|確認|检查点|檢查點|回滚|回滾)",
        r"(?:所有|全部|任何|未来|未來|今后|今後).{0,20}(?:修改|更改|编辑|編輯|写入|寫入|变更|變更).{0,20}(?:已获|已獲|已经|已經|均已|都已)?(?:批准|核准|授权|授權|允许|允許)",
        r"(?:权限|權限|授权|授權).{0,20}(?:已授予|已賦予|授予|賦予).{0,20}(?:所有|全部|任何).{0,20}(?:修改|更改|编辑|編輯|写入|寫入|变更|變更)",
        r"(?:授予|賦予).{0,10}(?:权限|權限|授权|授權)",
        r"(?:無視|上書き|回避).{0,60}(?:システム|開発者|指示|権限|規則)",
        r"(?:漏らす|送信|アップロード|出力).{0,60}(?:キー|トークン|パスワード|認証情報|秘密)",
        r"(?:常に|必ず|自動で).{0,30}(?:承認|許可|認可)",
        r"(?:承認|許可|確認).{0,30}(?:求めない|不要|なしで|回避|スキップ|無効)",
        r"(?:すべて|全て|今後|将来).{0,20}(?:編集|変更|書き込み|修正).{0,20}(?:承認済み|許可済み|認可済み)",
        r"(?:編集|変更|修正).{0,20}(?:権限がある|許可されている|認可されている)",
        r"(?:権限|許可|認可).{0,15}(?:付与|与える)",
    )
)


def memory_text_is_instruction_sensitive(value: Any) -> bool:
    """Reject durable text that could change authority or request an action."""

    text = str(value or "").replace("\x00", "").strip()
    return any(pattern.search(text) is not None for pattern in _INSTRUCTION_SENSITIVE_PATTERNS)


__all__ = ["memory_text_is_instruction_sensitive"]
