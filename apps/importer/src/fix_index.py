#!/usr/bin/env python3
import logging
import os
import re
import sys

import psycopg

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('fix_index')

# Database connection
_raw_url = os.getenv('DATABASE_URL', 'postgresql://edfinder:edfinder@postgres:5432/edfinder')
TIMEOUT_PATTERN = re.compile(r'^[0-9]+(?:ms|s|min|h)?$')
ZERO_TIMEOUT_PATTERN = re.compile(r'^0+(?:ms|s|min|h)?$')


def migration_timeouts():
    statement_timeout = os.getenv('MIGRATION_STATEMENT_TIMEOUT', '3h')
    lock_timeout = os.getenv('MIGRATION_LOCK_TIMEOUT', '30s')

    for name, value in (
        ('MIGRATION_STATEMENT_TIMEOUT', statement_timeout),
        ('MIGRATION_LOCK_TIMEOUT', lock_timeout),
    ):
        if not TIMEOUT_PATTERN.fullmatch(value):
            raise ValueError(
                f'{name} must be a non-negative PostgreSQL duration using ms, s, min, or h'
            )

    has_zero_timeout = any(
        ZERO_TIMEOUT_PATTERN.fullmatch(value)
        for value in (statement_timeout, lock_timeout)
    )
    if has_zero_timeout:
        if os.getenv('EDFINDER_ALLOW_UNBOUNDED_MIGRATION_TIMEOUTS', 'no') != 'yes':
            raise ValueError(
                'zero migration timeouts require '
                'EDFINDER_ALLOW_UNBOUNDED_MIGRATION_TIMEOUTS=yes'
            )
        log.warning('Unbounded migration timeout explicitly enabled for this reviewed run')

    return statement_timeout, lock_timeout


def fix_index():
    log.info("Starting automated index fix...")

    try:
        statement_timeout, lock_timeout = migration_timeouts()
        connection_options = (
            f'-c statement_timeout={statement_timeout} '
            f'-c lock_timeout={lock_timeout}'
        )

        # Connect to the database
        conn = psycopg.connect(_raw_url, options=connection_options)
        conn.autocommit = True
        cur = conn.cursor()

        # Check if index exists
        cur.execute("""
            SELECT COUNT(*) FROM pg_indexes 
            WHERE tablename = 'systems' AND indexname = 'idx_sys_grid_null'
        """)
        if cur.fetchone()[0] > 0:
            log.info("Index 'idx_sys_grid_null' already exists. Nothing to do.")
            return True

        # Create the index
        log.info("Creating index 'idx_sys_grid_null' CONCURRENTLY...")
        log.info("This may take a few minutes depending on table size, but it won't block the database.")
        
        # Note: CONCURRENTLY cannot be run inside a transaction block in some drivers, 
        # but with autocommit=True it should work.
        cur.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_sys_grid_null 
            ON systems(id64) 
            WHERE grid_cell_id IS NULL
        """)
        
        log.info("✓ Index created successfully!")
        return True

    except Exception as e:
        log.error(f"Failed to create index: {e}")
        return False
    finally:
        if 'conn' in locals() and conn:
            conn.close()


if __name__ == "__main__":
    if fix_index():
        sys.exit(0)
    else:
        sys.exit(1)
