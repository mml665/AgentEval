"""Safe recovery hints for execution failures; never include remote bodies or headers."""
HINTS = {
    "HTTP_AUTH_REJECTED": "Agent 拒绝凭据。请由管理员核对凭据版本，并为新配置发布 Agent 版本。",
    "HTTP_PERMISSION_DENIED": "Agent 返回权限不足。请检查该凭据对测试接口的业务权限。",
    "HTTP_REDIRECT_REJECTED": "Agent 返回重定向。请登记允许清单内的最终地址；平台不会转发凭据。",
    "HTTP_INVALID_JSON": "Agent 返回无效 JSON。请检查响应编码与协议。",
    "HTTP_PROTOCOL_ERROR": "Agent 响应缺少 output。请修正接口协议后重新评测。",
    "HTTP_BUSINESS_FAILURE": "Agent 报告业务失败。请核对业务错误和选定测试场景。",
    "HTTP_SENSITIVE_RESPONSE_REJECTED": "Agent 回显了鉴权秘密，平台已丢弃响应。请从服务响应中移除秘密后重新评测。",
    "HTTP_CONNECTION_FAILED": "无法连接 Agent。请检查服务状态、网络与允许清单。",
    "HTTP_TRANSPORT_ERROR": "HTTP 交互中断，远端结果未知。请独立核验业务状态，避免重复副作用。",
    "AGENT_TIMEOUT": "调用超时，远端结果未知。请先核验业务状态，再决定是否显式重跑。",
    "CREDENTIAL_REVOKED": "凭据版本已撤销，调用未开始。请登记新凭据版本并发布新的 Agent 配置。",
    "CREDENTIAL_UNAVAILABLE": "服务端凭据引用不可用，调用未开始。请检查部署配置。",
    "CREDENTIAL_VERSION_CHANGED": "秘密已轮换，旧引用不会自动采用新值。请登记新版本。",
    "CREDENTIAL_PROVIDER_NOT_CONFIGURED": "凭据提供方尚未配置，调用未开始。请联系部署管理员。",
}


def describe(error):
    if not error: return None
    code = error.get("code", "EXECUTION_FAILED")
    return {"code": code, "recovery": HINTS.get(code, "请核对执行状态、Agent 日志和当前固定批次的评分证据。")}
