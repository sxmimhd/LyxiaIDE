"""One broker / policy store / audit log shared by the tools API and the agent."""
from functools import lru_cache

from app.config import get_settings
from app.tools.permissions import ApprovalBroker, AuditLog, PolicyStore

broker = ApprovalBroker()


@lru_cache
def get_policies() -> PolicyStore:
    return PolicyStore(get_settings().data_dir / "permissions.json")


@lru_cache
def get_audit() -> AuditLog:
    return AuditLog(get_settings().data_dir / "tool_audit.jsonl")