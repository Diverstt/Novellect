import hashlib
import os
import pickle
import re
from pathlib import Path

import chardet
from bs4 import BeautifulSoup

try:
    import PyPDF2
except Exception:
    PyPDF2 = None

try:
    from ebooklib import epub
except Exception:
    epub = None

BASE_DIR = Path(__file__).resolve().parent
TXT_CACHE_DIR = str(BASE_DIR / 'txt_cache')
os.makedirs(TXT_CACHE_DIR, exist_ok=True)


def get_cache_path(original_file_path):
    with open(original_file_path, 'rb') as file_obj:
        file_hash = hashlib.md5(file_obj.read()).hexdigest()
    cache_filename = f"{file_hash}_{os.path.basename(original_file_path)}.txt"
    return os.path.join(TXT_CACHE_DIR, cache_filename)


def save_txt_cache(original_path, content, metadata):
    cache_path = get_cache_path(original_path)
    with open(cache_path + '.meta', 'wb') as file_obj:
        pickle.dump(metadata, file_obj)
    with open(cache_path, 'w', encoding='utf-8') as file_obj:
        file_obj.write(content)
    return cache_path


def load_txt_cache(original_path):
    cache_path = get_cache_path(original_path)
    meta_path = cache_path + '.meta'
    if not (os.path.exists(cache_path) and os.path.exists(meta_path)):
        return None, None
    try:
        with open(cache_path, 'r', encoding='utf-8') as file_obj:
            content = file_obj.read()
        with open(meta_path, 'rb') as file_obj:
            metadata = pickle.load(file_obj)
        return content, metadata
    except Exception:
        return None, None


def _normalize_source_name(source_name):
    if not source_name:
        return None
    return str(source_name).strip().replace('\\', '/')


def _source_basename(source_name, fallback_path):
    normalized = _normalize_source_name(source_name)
    if normalized:
        return os.path.basename(normalized)
    return os.path.basename(fallback_path)


def _title_from_source(source_name, fallback_path):
    return os.path.splitext(_source_basename(source_name, fallback_path))[0]


def _build_book_payload(file_path, title, content, file_format, metadata=None, source_filename=None, **extra):
    payload = {
        'title': title,
        'content': content,
        'format': file_format,
        'metadata': metadata or {},
        'file_path': file_path,
        'source_filename': _normalize_source_name(source_filename) or os.path.basename(file_path),
        'cache_path': get_cache_path(file_path),
    }
    payload.update(extra)
    return payload


def _basic_cleanup(text):
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\r', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def read_txt(file_path, source_name=None):
    try:
        with open(file_path, 'rb') as file_obj:
            raw = file_obj.read()
        detection = chardet.detect(raw)
        encoding = detection['encoding'] if detection.get('encoding') else 'utf-8'
        with open(file_path, 'r', encoding=encoding, errors='ignore') as file_obj:
            text = _basic_cleanup(file_obj.read())
        title = _title_from_source(source_name, file_path)
        metadata = {'title': title, 'format': 'txt'}
        return _build_book_payload(file_path, title, text, 'txt', metadata=metadata, source_filename=source_name)
    except Exception as exc:
        return {'title': _source_basename(source_name, file_path), 'content': '', 'format': 'txt', 'error': str(exc)}


def read_fb2(file_path, source_name=None):
    try:
        cached_content, cached_meta = load_txt_cache(file_path)
        if cached_content and cached_meta:
            return _build_book_payload(
                file_path,
                cached_meta.get('title', _title_from_source(source_name, file_path)),
                cached_content,
                'fb2',
                metadata=cached_meta,
                source_filename=source_name,
                from_cache=True,
            )
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as file_obj:
            content = file_obj.read()
        soup = BeautifulSoup(content, 'xml')
        title = _title_from_source(source_name, file_path)
        title_info = soup.find('title-info')
        if title_info:
            tag = title_info.find('book-title')
            if tag and tag.get_text().strip():
                title = tag.get_text().strip()
        body = soup.find('body')
        text_parts = []
        if body:
            for tag in body.find_all(['p', 'title', 'subtitle', 'poem', 'stanza']):
                value = tag.get_text().strip()
                if value:
                    text_parts.append(value)
        text_content = _basic_cleanup('\n\n'.join(text_parts))
        metadata = {'title': title, 'format': 'fb2', 'original': file_path}
        save_txt_cache(file_path, text_content, metadata)
        return _build_book_payload(file_path, title, text_content, 'fb2', metadata=metadata, source_filename=source_name)
    except Exception as exc:
        return {'title': _source_basename(source_name, file_path), 'content': '', 'format': 'fb2', 'error': str(exc)}


def read_pdf(file_path, source_name=None):
    if PyPDF2 is None:
        return {'title': _source_basename(source_name, file_path), 'content': '', 'format': 'pdf', 'error': 'PyPDF2 не установлен'}
    try:
        cached_content, cached_meta = load_txt_cache(file_path)
        if cached_content and cached_meta:
            return _build_book_payload(
                file_path,
                cached_meta.get('title', _title_from_source(source_name, file_path)),
                cached_content,
                'pdf',
                metadata=cached_meta,
                source_filename=source_name,
                from_cache=True,
            )
        text = ''
        title = _title_from_source(source_name, file_path)
        with open(file_path, 'rb') as file_obj:
            reader = PyPDF2.PdfReader(file_obj)
            for page in reader.pages:
                try:
                    page_text = page.extract_text()
                except Exception:
                    page_text = None
                if page_text:
                    text += page_text + '\n'
            metadata_title = None
            if reader.metadata:
                metadata_title = reader.metadata.get('/Title')
            if metadata_title and str(metadata_title).strip():
                title = str(metadata_title).strip()
        text = _basic_cleanup(text)
        metadata = {'title': title, 'format': 'pdf', 'original': file_path}
        save_txt_cache(file_path, text, metadata)
        return _build_book_payload(file_path, title, text, 'pdf', metadata=metadata, source_filename=source_name)
    except Exception as exc:
        return {'title': _source_basename(source_name, file_path), 'content': '', 'format': 'pdf', 'error': str(exc)}


def read_epub(file_path, source_name=None):
    if epub is None:
        return {'title': _source_basename(source_name, file_path), 'content': '', 'format': 'epub', 'error': 'ebooklib не установлен'}
    try:
        cached_content, cached_meta = load_txt_cache(file_path)
        if cached_content and cached_meta:
            return _build_book_payload(
                file_path,
                cached_meta.get('title', _title_from_source(source_name, file_path)),
                cached_content,
                'epub',
                metadata=cached_meta,
                source_filename=source_name,
                from_cache=True,
            )
        book = epub.read_epub(file_path)
        title = _title_from_source(source_name, file_path)
        if book.get_metadata('DC', 'title'):
            metadata_title = book.get_metadata('DC', 'title')[0][0]
            if metadata_title:
                title = metadata_title
        text_parts = []
        for item in book.get_items():
            if item.get_type() == 9:
                soup = BeautifulSoup(item.get_content(), 'html.parser')
                for script in soup(['script', 'style']):
                    script.decompose()
                value = soup.get_text(separator=' ', strip=True)
                if value:
                    text_parts.append(value)
                soup.decompose()
        text = _basic_cleanup('\n\n'.join(text_parts))
        metadata = {'title': title, 'format': 'epub', 'original': file_path}
        save_txt_cache(file_path, text, metadata)
        return _build_book_payload(file_path, title, text, 'epub', metadata=metadata, source_filename=source_name)
    except Exception as exc:
        return {'title': _source_basename(source_name, file_path), 'content': '', 'format': 'epub', 'error': str(exc)}


def process_file(file_path, original_name=None):
    if not os.path.exists(file_path):
        return {'error': 'Файл не найден'}
    ext = os.path.splitext(file_path)[1].lower()
    readers = {'.txt': read_txt, '.fb2': read_fb2, '.pdf': read_pdf, '.epub': read_epub}
    reader = readers.get(ext)
    if not reader:
        return {'title': os.path.basename(file_path), 'content': '', 'format': ext, 'error': f'Неподдерживаемый формат: {ext}'}
    book_data = reader(file_path, source_name=original_name)
    if book_data.get('from_cache'):
        print(f'[INFO] Загружено из кэша: {file_path}')
    content = book_data.get('content', '')
    if not content or len(content.strip()) < 10:
        return {'error': 'Файл пуст или содержит нечитаемый текст. Загрузка отклонена.'}
    book_data.setdefault('file_path', file_path)
    book_data.setdefault('source_filename', _source_basename(original_name, file_path))
    book_data.setdefault('cache_path', get_cache_path(file_path))
    return book_data


def clear_txt_cache(max_age_days=30):
    import time

    now = time.time()
    for file_name in os.listdir(TXT_CACHE_DIR):
        file_path = os.path.join(TXT_CACHE_DIR, file_name)
        if os.path.isfile(file_path) and os.stat(file_path).st_mtime < now - (max_age_days * 86400):
            try:
                os.remove(file_path)
            except OSError:
                pass
