"""Original V3 schema and retained cohort, in a newly created disposable database."""
from contextlib import contextmanager
import os
from pathlib import Path
from uuid import uuid4

from tests.helpers import db_isolation


@contextmanager
def canonical_database(*, prepare=None, connection_class=None):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from domain.ratings_v4_canonical import load_source_fixture

    dsn = os.environ.get('RATINGS_V4_VALIDATION_DATABASE_URL')
    if not dsn:
        db_isolation.database_unavailable(
            'isolated PostgreSQL validation URL not set (RATINGS_V4_VALIDATION_DATABASE_URL)')
    settings = conninfo_to_dict(dsn)
    if (settings.get('host') not in {'127.0.0.1', 'localhost'} or
            settings.get('dbname') != 'ratings_v4_validation'):
        raise ValueError('canonical fixture requires the local ratings_v4_validation service')
    database = 'v4_test_' + uuid4().hex
    root = Path(__file__).resolve().parents[1]
    canonical, metadata, payloads = load_source_fixture(root / 'tests/fixtures/ratings_v4_sources')
    additional_sources = prepare(canonical, metadata) if prepare else ()
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        try:
            connection_class = connection_class or psycopg.Connection
            with connection_class.connect(make_conninfo(dsn, dbname=database), autocommit=True) as connection:
                connection.execute((root / 'sql/v3/migrations/001_v3_baseline.sql').read_text())

                # Shared with scripts/dev/seed_cypress_v3_generation.py so the
                # test fixture and the Cypress seed use ONE canonical-bootstrap
                # path (plan D4). Behaviour is unchanged: this is the former
                # inline transaction block, lifted verbatim.
                from scripts.dev.v3_canonical_bootstrap import bootstrap_published_canonical
                bootstrap_published_canonical(connection, canonical, metadata,
                                              additional_sources=additional_sources)
                yield connection, canonical, metadata, payloads
        finally:
            # Only the random database created by this context can be removed.
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database)))
