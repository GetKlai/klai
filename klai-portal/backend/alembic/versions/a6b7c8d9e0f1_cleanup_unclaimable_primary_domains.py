"""cleanup unclaimable primary domains

Revision ID: a6b7c8d9e0f1
Revises: fc5d6e7f8a9b
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "fc5d6e7f8a9b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UNCLAIMABLE_DOMAINS: tuple[str, ...] = (
    "126.com",
    "163.com",
    "aol.com",
    "bk.ru",
    "casema.nl",
    "chello.nl",
    "duck.com",
    "email.com",
    "fastmail.com",
    "free.fr",
    "gmail.com",
    "gmx.com",
    "gmx.de",
    "googlemail.com",
    "hetnet.nl",
    "home.nl",
    "hotmail.co.uk",
    "hotmail.com",
    "hotmail.nl",
    "hushmail.com",
    "icloud.com",
    "inbox.com",
    "kpnmail.nl",
    "laposte.net",
    "libero.it",
    "list.ru",
    "live.com",
    "live.nl",
    "mac.com",
    "mail.com",
    "mail.ru",
    "mailbox.org",
    "mailfence.com",
    "me.com",
    "msn.com",
    "orange.fr",
    "outlook.com",
    "outlook.nl",
    "planet.nl",
    "pm.me",
    "posteo.de",
    "proton.me",
    "protonmail.ch",
    "protonmail.com",
    "qq.com",
    "quicknet.nl",
    "rambler.ru",
    "rediffmail.com",
    "seznam.cz",
    "sina.com",
    "t-online.de",
    "telfort.nl",
    "tuta.com",
    "tutanota.com",
    "upcmail.nl",
    "virgilio.it",
    "wanadoo.fr",
    "wanadoo.nl",
    "web.de",
    "xs4all.nl",
    "yandex.com",
    "yandex.ru",
    "yahoo.com",
    "yahoo.co.uk",
    "yahoo.nl",
    "zeelandnet.nl",
    "ziggo.nl",
    "zoho.com",
    "zohomail.com",
)


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE portal_orgs
            SET primary_domain = '',
                auto_accept_same_domain = false
            WHERE lower(primary_domain) IN :domains
            """
        ).bindparams(sa.bindparam("domains", value=UNCLAIMABLE_DOMAINS, expanding=True))
    )


def downgrade() -> None:
    # Destructive cleanup is intentionally not reversible: restoring prior
    # unclaimable primary_domain values would reintroduce unsafe metadata.
    pass
