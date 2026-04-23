# Fixture: read-only SQLAlchemy call — clean (no violation).
from sqlalchemy import select

result = session.execute(select(User))
rows = result.fetchall()
