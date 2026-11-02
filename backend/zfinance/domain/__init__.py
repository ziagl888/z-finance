"""Feature packages. One subpackage per feature, e.g. ``zfinance.domain.accounts``.

Layout of a feature package (see ARCHITECTURE.md, "Adding a feature"):

    domain/<feature>/__init__.py   docstring: what the feature owns
    domain/<feature>/service.py    functions taking a sqlite3.Connection; pure logic where possible
    domain/<feature>/api.py        ``router = APIRouter(prefix="/api/<feature>", tags=["<feature>"])``

``zfinance.app`` imports ``domain.<feature>.api`` for every subpackage that has one and includes
its ``router``; no central registry needs editing, so parallel feature branches do not collide.
"""
