"""baseline

Revision ID: b9a87a3039e8
Revises:
Create Date: 2026-08-02 22:29:49.378374

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "b9a87a3039e8"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
