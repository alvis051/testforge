"""The TestForge runner worker.

Deliberately a separate distribution from ``testforge``: a build machine should not
install FastAPI, SQLAlchemy, and Alembic to run tests. Because this package cannot
import the server, it cannot reach the database even by accident — the only thing it
can do is what the HTTP protocol allows.
"""
