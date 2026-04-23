"""SQLAlchemy declarative base for klai-connector models.

The legacy ``Connector`` ORM class and its ``connector.connectors`` table
were removed in SPEC-CONNECTOR-CLEANUP-001. Connector configuration is
owned by ``portal_connectors`` in the portal database; klai-connector
fetches it at sync time via ``PortalClient.get_connector_config``.

This module is kept (rather than collapsed into ``__init__.py`` or
renamed to ``base.py``) so that ``from app.models.connector import Base``
imports in ``sync_run.py`` and ``alembic/env.py`` keep working without
churn. Renaming is cosmetic and intentionally deferred.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all models."""
