# Fixture: session.add() outside registry-state — VIOLATION (SW001).
user = User(name="alice")
session.add(user)
