"""Who is acting: a verified subject, the one organization they act in, and their role.

Roles are ordered; a route names the lowest role it accepts (deny by default).
"""

from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID


class Role(IntEnum):
    VIEWER = 1
    ANALYST = 2
    ADMIN = 3
    OWNER = 4

    @classmethod
    def parse(cls, value: str) -> "Role | None":
        try:
            return cls[value.strip().upper()]
        except KeyError:
            return None


@dataclass(frozen=True)
class Principal:
    subject: str
    organization_id: UUID
    role: Role

    def allows(self, minimum: Role) -> bool:
        return self.role >= minimum
