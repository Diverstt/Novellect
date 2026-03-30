import io
import os
import re
import zipfile
from pathlib import PurePosixPath

SUPPORTED_BOOK_EXTENSIONS = {'.txt', '.fb2', '.pdf', '.epub'}
MAX_ARCHIVE_BOOKS = 1000
MAX_TOTAL_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024  # 1 ГБ


class ArchiveProcessingError(Exception):
    """Ошибка обработки архива."""


def normalize_archive_member_path(member_name):
    """Нормализует путь файла внутри архива и отсекает небезопасные пути."""
    raw_name = str(member_name or '').strip().replace('\\', '/')
    if not raw_name or raw_name.startswith('/'):
        return None

    parts = []
    for part in PurePosixPath(raw_name).parts:
        if part in ('', '.', '/'):
            continue
        if part == '..':
            return None
        parts.append(part)

    if not parts:
        return None

    return '/'.join(parts)


def build_safe_storage_name(source_name, max_length=180):
    """Строит безопасное имя файла для хранения в uploads/."""
    normalized = normalize_archive_member_path(source_name) or str(source_name or '').replace('\\', '/')
    parts = [part for part in PurePosixPath(normalized).parts if part not in ('', '.', '..', '/')]
    if not parts:
        parts = ['book']

    flattened = '__'.join(parts[-3:])
    safe_name = re.sub(r'[^\w.\- ()]+', '_', flattened, flags=re.UNICODE).strip(' ._')
    if not safe_name:
        safe_name = 'book'

    base_name, extension = os.path.splitext(safe_name)
    if len(base_name) > max_length - len(extension):
        base_name = base_name[: max_length - len(extension)].rstrip(' ._')

    return f"{base_name or 'book'}{extension}"


def iter_supported_files_from_zip(
    zip_bytes,
    archive_name,
    max_books=MAX_ARCHIVE_BOOKS,
    max_total_uncompressed_bytes=MAX_TOTAL_UNCOMPRESSED_BYTES,
):
    """Итерирует поддерживаемые книги внутри ZIP без небезопасной распаковки."""
    if not zip_bytes:
        raise ArchiveProcessingError('Архив пустой.')

    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise ArchiveProcessingError('Файл не является корректным ZIP-архивом.') from exc

    supported_count = 0
    total_uncompressed = 0
    seen_paths = set()

    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue

            member_path = normalize_archive_member_path(info.filename)
            if not member_path or member_path in seen_paths:
                continue
            seen_paths.add(member_path)

            extension = PurePosixPath(member_path).suffix.lower()
            if extension not in SUPPORTED_BOOK_EXTENSIONS:
                continue

            supported_count += 1
            if supported_count > max_books:
                raise ArchiveProcessingError(
                    f'В архиве слишком много поддерживаемых файлов: максимум {max_books}.'
                )

            total_uncompressed += max(info.file_size, 0)
            if total_uncompressed > max_total_uncompressed_bytes:
                raise ArchiveProcessingError(
                    'Архив слишком большой после распаковки. Допустимый предел — 1 ГБ.'
                )

            try:
                with archive.open(info) as file_obj:
                    data = file_obj.read()
            except RuntimeError as exc:
                raise ArchiveProcessingError(
                    f'Не удалось распаковать {member_path}: {exc}'
                ) from exc

            yield {
                'source_filename': member_path,
                'display_name': PurePosixPath(member_path).name,
                'bytes': data,
                'size': len(data),
                'metadata': {
                    'archive_name': archive_name,
                    'archive_member_path': member_path,
                    'source_archive_type': 'zip',
                    'extracted_from_archive': True,
                },
            }
