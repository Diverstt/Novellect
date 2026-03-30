from __future__ import annotations

import argparse
import time
from typing import Any, Callable

from recommendation_service import RecommendationService

DEFAULT_LOOP_INTERVAL_SEC = 120
MIN_SYNC_INTERVAL_SEC = 10
MIN_INGEST_INTERVAL_SEC = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Novellect background worker')
    parser.add_argument(
        'command',
        choices=[
            'sync-qdrant',
            'sync-loop',
            'ingest-once',
            'ingest-loop',
            'retry-failed',
            'reindex-file',
            'rebuild-passports',
        ],
    )
    parser.add_argument('arg', nargs='?', default=None)
    return parser


def parse_int_arg(raw_value: str | None, *, default: int | None = None, label: str) -> int | None:
    if raw_value in (None, ''):
        return default

    try:
        return int(raw_value)
    except ValueError as error:
        raise SystemExit(f'{label} must be an integer') from error


def require_arg(raw_value: str | None, *, message: str) -> str:
    value = (raw_value or '').strip()
    if not value:
        raise SystemExit(message)
    return value


def print_result(result: Any) -> None:
    print(result)


def run_loop(action: Callable[[], Any], *, interval_sec: int, min_interval_sec: int) -> None:
    sleep_sec = max(min_interval_sec, interval_sec)
    while True:
        print_result(action())
        time.sleep(sleep_sec)


def run_command(command: str, raw_arg: str | None) -> int:
    service = RecommendationService()

    if command == 'sync-qdrant':
        print_result(service.sync_catalog_to_qdrant())
        return 0

    if command == 'sync-loop':
        interval = parse_int_arg(raw_arg, default=DEFAULT_LOOP_INTERVAL_SEC, label='sync interval')
        run_loop(
            service.sync_catalog_to_qdrant,
            interval_sec=interval or DEFAULT_LOOP_INTERVAL_SEC,
            min_interval_sec=MIN_SYNC_INTERVAL_SEC,
        )
        return 0

    if command == 'ingest-once':
        print_result(service.run_ingestion_scan_once())
        return 0

    if command == 'ingest-loop':
        interval = parse_int_arg(raw_arg, default=DEFAULT_LOOP_INTERVAL_SEC, label='ingest interval')
        run_loop(
            service.run_ingestion_scan_once,
            interval_sec=interval or DEFAULT_LOOP_INTERVAL_SEC,
            min_interval_sec=MIN_INGEST_INTERVAL_SEC,
        )
        return 0

    if command == 'retry-failed':
        print_result(service.retry_failed_ingestion())
        return 0

    if command == 'rebuild-passports':
        limit = parse_int_arg(raw_arg, default=None, label='rebuild limit')
        print_result(service.rebuild_semantic_catalog(limit=limit))
        return 0

    dataset_path = require_arg(raw_arg, message='reindex-file requires a file path')
    print_result(service.reindex_dataset_path(dataset_path))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    return run_command(args.command, args.arg)


if __name__ == '__main__':
    raise SystemExit(main())
