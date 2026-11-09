"""One transactional startup entry; domain modules own all business DDL."""

def execute(conn, script):
    """Execute DDL without executescript's implicit COMMIT."""
    import sqlite3
    statement = ''
    for character in script:
        statement += character
        if character == ';' and sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ''
    if statement.strip():
        conn.execute(statement)


def migrate(db):
    from ..identity import migrate as identity
    from ..payments.migrations import migrate as payments
    from ..registry.migrations import migrate as registry
    from ..delegation.migrations import migrate as delegation
    from ..messaging.migrations import migrate as messaging
    from ..observation.migrations import migrate as observation
    from ..storage import initialize_adapter
    from ..events import migrate as events
    from ..files import migrate as files
    with db.tx() as conn:
        initialize_adapter(conn)
        for component in (identity, payments, registry, delegation, messaging, observation, events, files):
            component(conn)
