from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aether.models import AuditEvent


def record(db: AsyncSession, principal, action: str, entity_type: str, entity_id: str | None = None, **detail: Any) -> None:
    db.add(
        AuditEvent(
            workspace_id=principal.workspace.id,
            actor=principal.actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )
