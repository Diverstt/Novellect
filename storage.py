import json
from pathlib import Path

# Константы путей
BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / 'uploads'
STORAGE_FILE = BASE_DIR / 'storage.json'
MAX_SIZE_GB = 1

if not UPLOADS_DIR.exists():
    UPLOADS_DIR.mkdir(parents=True)


def get_library_size():
    """Считает общий размер всех загруженных файлов в байтах."""
    return sum(file_path.stat().st_size for file_path in UPLOADS_DIR.glob('**/*') if file_path.is_file())


def is_limit_exceeded(new_file_size_bytes=0):
    """Проверяет, не будет ли превышен лимит в 1 ГБ."""
    limit_bytes = MAX_SIZE_GB * 1024 * 1024 * 1024
    return (get_library_size() + new_file_size_bytes) > limit_bytes


def load_index():
    if not STORAGE_FILE.exists():
        return []
    try:
        with open(STORAGE_FILE, 'r', encoding='utf-8') as file_obj:
            data = json.load(file_obj)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_index(index):
    with open(STORAGE_FILE, 'w', encoding='utf-8') as file_obj:
        json.dump(index, file_obj, ensure_ascii=False, indent=2)


def get_book_by_hash(file_hash):
    """Ищет книгу в индексе по её хэшу."""
    for book in load_index():
        if book.get('file_hash') == file_hash:
            return book
    return None


def delete_book_physically(book_id):
    """Обратнос совместимый wrapper: удаляет книгу из storage, vector DB и файловой системы."""
    from search_engine import delete_from_index

    return delete_from_index(book_id)
