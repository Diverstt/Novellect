import mimetypes
import os
import posixpath
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

try:
    from google.cloud import storage as gcs_storage
except Exception:
    gcs_storage = None

SUPPORTED_EXTENSIONS = {'.txt', '.fb2', '.pdf', '.epub', '.zip'}
DEFAULT_USER_AGENT = 'Novellect/1.0 (+cloud-import)'
GOOGLE_DRIVE_API_URL = 'https://www.googleapis.com/drive/v3/files/{file_id}'
GOOGLE_DRIVE_DIRECT_URL = 'https://drive.google.com/uc'


class CloudSourceError(Exception):
    """Base cloud import error."""


class UnsupportedCloudSourceError(CloudSourceError):
    """Raised when a URL/provider is unsupported."""


class CloudDownloadError(CloudSourceError):
    """Raised when a cloud download fails."""


class CloudSourceTooLargeError(CloudSourceError):
    """Raised when the remote file exceeds configured limits."""


class CloudAuthRequiredError(CloudSourceError):
    """Raised when authenticated access is required."""


GOOGLE_DRIVE_FILE_PATTERNS = [
    re.compile(r'/file/d/([a-zA-Z0-9_-]{10,})'),
    re.compile(r'/document/d/([a-zA-Z0-9_-]{10,})'),
    re.compile(r'/spreadsheets/d/([a-zA-Z0-9_-]{10,})'),
    re.compile(r'/presentation/d/([a-zA-Z0-9_-]{10,})'),
]


def normalize_source_lines(raw_text):
    sources = []
    seen = set()
    for line in (raw_text or '').splitlines():
        value = line.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        sources.append(value)
    return sources


def _requests_session():
    session = requests.Session()
    session.headers.update({'User-Agent': DEFAULT_USER_AGENT})
    return session


def detect_provider(source):
    if not source:
        return 'unknown'

    if source.startswith('gs://'):
        return 'gcs'

    parsed = urlparse(source)
    host = parsed.netloc.lower()
    if host.endswith('drive.google.com') or host.endswith('docs.google.com'):
        return 'gdrive'
    if host == 'storage.googleapis.com' or host.endswith('.storage.googleapis.com') or host == 'storage.cloud.google.com':
        return 'gcs'
    if parsed.scheme in {'http', 'https'}:
        return 'http'
    return 'unknown'


def _content_disposition_filename(headers):
    value = headers.get('content-disposition') or headers.get('Content-Disposition')
    if not value:
        return None

    match = re.search(r"filename\*=UTF-8''([^;]+)", value)
    if match:
        return unquote(match.group(1)).strip('"')

    match = re.search(r'filename="?([^";]+)"?', value)
    if match:
        return match.group(1).strip()
    return None


def _filename_from_url(url):
    parsed = urlparse(url)
    name = Path(unquote(parsed.path)).name
    return name or None


def _guess_extension(content_type):
    if not content_type:
        return ''
    guessed = mimetypes.guess_extension(content_type.split(';', 1)[0].strip())
    return guessed or ''


def _ensure_supported_name(name, content_type=None, fallback='cloud_asset'):
    name = (name or '').strip() or fallback
    suffix = Path(name).suffix.lower()
    if suffix in SUPPORTED_EXTENSIONS:
        return name
    guessed = _guess_extension(content_type)
    if guessed in SUPPORTED_EXTENSIONS:
        return f'{name}{guessed}' if not suffix else f'{Path(name).stem}{guessed}'
    return name


def _read_response_bytes(response, max_bytes):
    content_length = response.headers.get('Content-Length') or response.headers.get('content-length')
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise CloudSourceTooLargeError(
                    f'Удаленный файл больше лимита ({int(content_length) / (1024 * 1024):.1f} МБ).'
                )
        except ValueError:
            pass

    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise CloudSourceTooLargeError(
                f'Удаленный файл превысил лимит {max_bytes / (1024 * 1024):.0f} МБ во время загрузки.'
            )
        chunks.append(chunk)
    return b''.join(chunks)


def _extract_google_drive_file_id(source):
    parsed = urlparse(source)
    query = parse_qs(parsed.query)
    if 'id' in query and query['id']:
        return query['id'][0]

    for pattern in GOOGLE_DRIVE_FILE_PATTERNS:
        match = pattern.search(source)
        if match:
            return match.group(1)
    return None


def _download_google_drive_via_api(file_id, session, timeout, max_bytes):
    bearer_token = os.getenv('GOOGLE_DRIVE_BEARER_TOKEN')
    api_key = os.getenv('GOOGLE_DRIVE_API_KEY')
    if not bearer_token and not api_key:
        return None

    headers = {}
    params = {'alt': 'media'}
    if bearer_token:
        headers['Authorization'] = f'Bearer {bearer_token}'
    if api_key:
        params['key'] = api_key

    response = session.get(
        GOOGLE_DRIVE_API_URL.format(file_id=file_id),
        params=params,
        headers=headers,
        stream=True,
        timeout=timeout,
    )
    if response.status_code in {401, 403}:
        raise CloudAuthRequiredError('Google Drive требует доступ к файлу или действительный токен.')
    if response.status_code >= 400:
        return None

    content = _read_response_bytes(response, max_bytes)
    filename = _content_disposition_filename(response.headers) or f'gdrive_{file_id}'
    return {
        'source_name': _ensure_supported_name(filename, response.headers.get('Content-Type')),
        'bytes': content,
        'metadata': {
            'cloud_source': 'google_drive_api',
            'cloud_reference': file_id,
            'content_type': response.headers.get('Content-Type'),
        },
    }


def _download_google_drive_public(file_id, session, timeout, max_bytes):
    response = session.get(
        GOOGLE_DRIVE_DIRECT_URL,
        params={'export': 'download', 'id': file_id},
        stream=True,
        allow_redirects=True,
        timeout=timeout,
    )

    if response.status_code in {401, 403}:
        raise CloudAuthRequiredError('Google Drive-ссылка недоступна без авторизации.')

    content_type = (response.headers.get('Content-Type') or '').lower()
    if 'text/html' in content_type:
        token = None
        for key, value in response.cookies.items():
            if key.startswith('download_warning'):
                token = value
                break
        if token:
            response = session.get(
                GOOGLE_DRIVE_DIRECT_URL,
                params={'export': 'download', 'id': file_id, 'confirm': token},
                stream=True,
                allow_redirects=True,
                timeout=timeout,
            )
            content_type = (response.headers.get('Content-Type') or '').lower()

    if response.status_code in {401, 403}:
        raise CloudAuthRequiredError('Google Drive-ссылка недоступна без авторизации.')
    if response.status_code >= 400:
        raise CloudDownloadError(f'Google Drive вернул HTTP {response.status_code}.')

    if 'text/html' in content_type and 'google docs' in response.text.lower():
        raise UnsupportedCloudSourceError(
            'Google Docs/Sheets/Slides через share-link не поддерживаются напрямую; используйте экспорт в PDF/TXT или blob-файл.'
        )

    content = _read_response_bytes(response, max_bytes)
    filename = _content_disposition_filename(response.headers) or f'gdrive_{file_id}'
    return {
        'source_name': _ensure_supported_name(filename, response.headers.get('Content-Type')),
        'bytes': content,
        'metadata': {
            'cloud_source': 'google_drive_public',
            'cloud_reference': file_id,
            'content_type': response.headers.get('Content-Type'),
        },
    }


def _parse_gcs_reference(source):
    if source.startswith('gs://'):
        raw = source[5:]
        bucket, _, blob_name = raw.partition('/')
        if not bucket or not blob_name:
            raise UnsupportedCloudSourceError('Ожидался путь вида gs://bucket/path/to/file.txt')
        return bucket, blob_name

    parsed = urlparse(source)
    host = parsed.netloc.lower()
    path = parsed.path.lstrip('/')

    if host == 'storage.googleapis.com':
        bucket, _, blob_name = path.partition('/')
        if not bucket or not blob_name:
            raise UnsupportedCloudSourceError('Для storage.googleapis.com нужен путь /bucket/object')
        return bucket, blob_name

    if host.endswith('.storage.googleapis.com'):
        bucket = host.split('.storage.googleapis.com', 1)[0]
        blob_name = path
        if not bucket or not blob_name:
            raise UnsupportedCloudSourceError('Некорректный GCS URL.')
        return bucket, blob_name

    if host == 'storage.cloud.google.com':
        bucket, _, blob_name = path.partition('/')
        if not bucket or not blob_name:
            raise UnsupportedCloudSourceError('Некорректный storage.cloud.google.com URL.')
        return bucket, blob_name

    raise UnsupportedCloudSourceError('Не удалось распознать ссылку Google Cloud Storage.')


def _download_gcs_via_sdk(bucket_name, blob_name, timeout, max_bytes):
    if gcs_storage is None:
        return None

    try:
        client = gcs_storage.Client()
        blob = client.bucket(bucket_name).blob(blob_name)
        content = blob.download_as_bytes(timeout=timeout)
        if len(content) > max_bytes:
            raise CloudSourceTooLargeError(
                f'Объект GCS больше лимита ({len(content) / (1024 * 1024):.1f} МБ).'
            )
        return {
            'source_name': _ensure_supported_name(Path(blob_name).name or 'gcs_asset'),
            'bytes': content,
            'metadata': {
                'cloud_source': 'google_cloud_storage_sdk',
                'cloud_reference': f'gs://{bucket_name}/{blob_name}',
                'content_type': getattr(blob, 'content_type', None),
            },
        }
    except CloudSourceTooLargeError:
        raise
    except Exception:
        return None


def _download_http(url, session, timeout, max_bytes, provider='http'):
    response = session.get(url, stream=True, allow_redirects=True, timeout=timeout)
    if response.status_code in {401, 403}:
        raise CloudAuthRequiredError(f'Удаленный источник {url} требует авторизацию.')
    if response.status_code >= 400:
        raise CloudDownloadError(f'Удаленный источник {url} вернул HTTP {response.status_code}.')

    content = _read_response_bytes(response, max_bytes)
    filename = (
        _content_disposition_filename(response.headers)
        or _filename_from_url(response.url)
        or _filename_from_url(url)
        or 'remote_asset'
    )
    filename = _ensure_supported_name(filename, response.headers.get('Content-Type'), fallback='remote_asset')
    return {
        'source_name': filename,
        'bytes': content,
        'metadata': {
            'cloud_source': provider,
            'cloud_reference': url,
            'content_type': response.headers.get('Content-Type'),
        },
    }


def download_cloud_source(source, timeout=90, max_file_mb=1024):
    provider = detect_provider(source)
    if provider == 'unknown':
        raise UnsupportedCloudSourceError(f'Источник не распознан: {source}')

    max_bytes = int(max_file_mb) * 1024 * 1024
    session = _requests_session()

    if provider == 'gdrive':
        file_id = _extract_google_drive_file_id(source)
        if not file_id:
            raise UnsupportedCloudSourceError('Не удалось извлечь fileId из Google Drive ссылки.')
        api_payload = _download_google_drive_via_api(file_id, session, timeout, max_bytes)
        if api_payload is not None:
            return api_payload
        return _download_google_drive_public(file_id, session, timeout, max_bytes)

    if provider == 'gcs':
        bucket_name, blob_name = _parse_gcs_reference(source)
        if source.startswith('gs://'):
            payload = _download_gcs_via_sdk(bucket_name, blob_name, timeout, max_bytes)
            if payload is not None:
                return payload
            public_url = f'https://storage.googleapis.com/{bucket_name}/{quote(blob_name, safe="/")}'
            return _download_http(public_url, session, timeout, max_bytes, provider='google_cloud_storage_public')

        if source.startswith('http://') or source.startswith('https://'):
            http_payload = _download_http(source, session, timeout, max_bytes, provider='google_cloud_storage_http')
            if Path(http_payload['source_name']).suffix.lower() not in SUPPORTED_EXTENSIONS:
                http_payload['source_name'] = _ensure_supported_name(
                    Path(blob_name).name,
                    http_payload['metadata'].get('content_type'),
                    fallback=Path(blob_name).name or 'gcs_asset',
                )
            return http_payload

    if provider == 'http':
        return _download_http(source, session, timeout, max_bytes, provider='http')

    raise UnsupportedCloudSourceError(f'Провайдер {provider} пока не поддерживается.')
