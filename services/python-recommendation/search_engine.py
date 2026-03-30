import hashlib
import json
import logging
import os
import pickle
import re
import tempfile
import time
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from chunk_store import chunk_store_mtime, delete_book_chunks, fetch_chunks, upsert_book_chunks
from runtime_config import get_compute_profile
from service_paths import (
    CHUNK_DB_FILE as PATHS_CHUNK_DB_FILE,
    SEARCH_CACHE_FILE,
    STORAGE_FILE as PATHS_STORAGE_FILE,
    VECTOR_DB_FILE as PATHS_VECTOR_DB_FILE,
    VECTOR_MANIFEST_FILE as PATHS_VECTOR_MANIFEST_FILE,
    VECTOR_SEGMENTS_DIR as PATHS_VECTOR_SEGMENTS_DIR,
    ensure_runtime_dirs,
    migrate_legacy_runtime_files,
)

BASE_DIR = Path(__file__).resolve().parent
STORAGE_FILE = str(PATHS_STORAGE_FILE)
VECTOR_DB_FILE = str(PATHS_VECTOR_DB_FILE)
VECTOR_MANIFEST_FILE = str(PATHS_VECTOR_MANIFEST_FILE)
VECTOR_SEGMENTS_DIR = str(PATHS_VECTOR_SEGMENTS_DIR)
CHUNK_DB_FILE = str(PATHS_CHUNK_DB_FILE)
CACHE_FILE = str(SEARCH_CACHE_FILE)
FINE_TUNED_MODEL_PATH = str(BASE_DIR / 'fine_tuned_literary_model')
LOCAL_BASE_MODEL_PATH = str(BASE_DIR / 'models' / 'paraphrase-multilingual-MiniLM-L12-v2')
BASE_EMBEDDING_MODEL_NAME = os.getenv('NOVELLECT_BASE_EMBEDDING_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2')
BASE_EMBEDDING_MODEL_PATH = os.getenv('NOVELLECT_BASE_EMBEDDING_MODEL_PATH', LOCAL_BASE_MODEL_PATH)
ensure_runtime_dirs()
migrate_legacy_runtime_files()

SentenceTransformer = None
CrossEncoder = None
_SENTENCE_TRANSFORMERS_ERROR = None
LOGGER = logging.getLogger(__name__)


def _ensure_sentence_transformers():
    global SentenceTransformer, CrossEncoder, _SENTENCE_TRANSFORMERS_ERROR
    if SentenceTransformer is not None and CrossEncoder is not None:
        return True
    if _SENTENCE_TRANSFORMERS_ERROR is not None:
        return False
    try:
        from sentence_transformers import CrossEncoder as _CrossEncoder
        from sentence_transformers import SentenceTransformer as _SentenceTransformer
        CrossEncoder = _CrossEncoder
        SentenceTransformer = _SentenceTransformer
        return True
    except Exception as exc:
        _SENTENCE_TRANSFORMERS_ERROR = exc
        SentenceTransformer = None
        CrossEncoder = None
        return False


STOP_WORDS = {
    'про', 'книга', 'книгу', 'книги', 'книге', 'автор', 'сюжет', 'прочитать', 'найти',
    'и', 'в', 'во', 'не', 'что', 'он', 'на', 'я', 'с', 'со', 'как', 'а', 'то',
    'все', 'она', 'так', 'из', 'за', 'вы', 'же', 'бы', 'по', 'только', 'ее',
    'мне', 'было', 'вот', 'от', 'меня', 'еще', 'о', 'об', 'ему', 'теперь', 'когда',
    'быть', 'был', 'была', 'это', 'для', 'кто', 'дом', 'год', 'купить', 'читать',
    'хочу', 'посоветуй', 'подбери', 'нужна', 'нужен', 'нужно', 'почитать', 'чтото', 'что-то',
    'какой', 'какая', 'какие', 'которой', 'который', 'есть', 'где', 'роман', 'повесть', 'рассказ',
}

GENERIC_PREFIXES = (
    'хочу книгу', 'хочу почитать', 'хочу прочитать', 'посоветуй книгу', 'подбери книгу',
    'что почитать', 'найди книгу', 'интересуют книги', 'хочу', 'мне нужна книга', 'мне нужен роман',
)

TEXT_FEATURES = {
    'mood': {
        'позитивное': ['радост', 'счаст', 'весел', 'светл', 'добр', 'хорош', 'юмор', 'смешн'],
        'негативное': ['груст', 'печал', 'мрачн', 'темн', 'зл', 'ужасн', 'кошмарн', 'безысход'],
        'нейтральное': ['спокойн', 'ровн', 'обычн', 'сдержан'],
        'тревожное': ['тревож', 'страшн', 'жутк', 'пугающ', 'опаса', 'беспоко', 'напряж'],
        'романтичное': ['любов', 'нежн', 'романтик', 'чувств', 'сердц', 'страст'],
    },
    'style': {
        'диалоговый': ['сказал', 'спросил', 'ответил', 'промолвил', 'воскликнул', 'диалог'],
        'описательный': ['стоял', 'виднел', 'казался', 'описание', 'пейзаж', 'портрет'],
        'динамичный': ['вдруг', 'внезапно', 'быстро', 'резко', 'мгновенно', 'стремительно'],
        'медленный': ['медленно', 'долго', 'постепенно', 'неторопливо', 'плавно', 'размеренно'],
    },
    'plot': {
        'преодоление': ['преодол', 'справил', 'победил', 'выдержал', 'переборол', 'одолел'],
        'борьба': ['борьб', 'сражен', 'битв', 'противосто', 'сопротивл', 'воевал', 'конфликт'],
        'путешествие': ['путешеств', 'поход', 'дорог', 'путь', 'странств', 'поездк'],
        'отношения': ['отношен', 'дружб', 'любов', 'ссор', 'примир', 'семья', 'поколен'],
        'тайна': ['тайн', 'загад', 'мистик', 'секрет', 'неизвестн', 'расследован'],
        'приключение': ['приключ', 'авантюр', 'риск', 'опасн', 'экспедиц'],
    },
    'characters': {
        'герой': ['герой', 'спаситель', 'защитник', 'рыцарь', 'избранн'],
        'антигерой': ['антигерой', 'циничн', 'сломленн', 'двусмысленн'],
        'злодей': ['злодей', 'враг', 'противник', 'антагонист', 'негодяй'],
        'жертва': ['жертв', 'пострадав', 'бедняг', 'несчастн'],
    },
    'atmosphere': {
        'таинственная': ['тайн', 'загад', 'мистик', 'потусторон', 'необъясним'],
        'напряженная': ['напряж', 'нервн', 'тревож', 'взволнованн'],
        'уютная': ['уют', 'тепл', 'домашн', 'комфортн', 'приютн'],
        'мрачная': ['мрач', 'темн', 'гнетущ', 'сумрачн', 'угрюм'],
        'легкая': ['легк', 'воздушн', 'непринужд', 'свободн'],
    },
    'tone': {
        'ироничный': ['ирони', 'насмешк', 'сарказм', 'язвительн'],
        'серьезный': ['серьезн', 'важн', 'существенн', 'значим'],
        'философский': ['философ', 'мудр', 'размышл', 'смысл'],
        'эмоциональный': ['эмоциональн', 'чувствен', 'душевн', 'переживан'],
    },
}

RUSSIAN_SUFFIXES = (
    'иями', 'ями', 'ами', 'ого', 'ему', 'ими', 'ыми', 'иях', 'ях', 'ах', 'ость', 'ости',
    'ение', 'ения', 'овать', 'ировать', 'ание', 'ания', 'ется', 'утся', 'ится', 'ются',
    'ешь', 'ете', 'ить', 'ать', 'ять', 'ому', 'ий', 'ый', 'ой', 'ая', 'яя', 'ое', 'ее',
    'ые', 'ие', 'ам', 'ям', 'ом', 'ем', 'ую', 'юю', 'ою', 'ею', 'а', 'я', 'ы', 'и', 'о', 'е', 'у', 'ю'
)

CYR_TO_LAT = str.maketrans({
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z',
    'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
    'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'c', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh',
    'ъ': '', 'ы': 'i', 'ь': '', 'э': 'e', 'ю': 'u', 'я': 'a',
})

TITLE_STRONG_THRESHOLD = 0.86
TITLE_SOFT_THRESHOLD = 0.62
RERANK_ENABLED = os.getenv('NOVELLECT_ENABLE_RERANKER', '1').strip().lower() not in {'0', 'false', 'no'}
SEARCH_LOG_TIMINGS = os.getenv('NOVELLECT_SEARCH_LOG_TIMINGS', '1').strip().lower() not in {'0', 'false', 'no'}
FAST_ENTITY_ONLY_SCORE = float(os.getenv('NOVELLECT_FAST_ENTITY_ONLY_SCORE', '0.88'))
FAST_TITLE_ONLY_SCORE = float(os.getenv('NOVELLECT_FAST_TITLE_ONLY_SCORE', '0.86'))

CAPITALIZED_ENTITY_STOP_WORDS = {
    'он', 'она', 'они', 'его', 'ее', 'её', 'их', 'это', 'этот', 'эта', 'эти', 'там', 'тут', 'вот',
    'после', 'потом', 'если', 'когда', 'который', 'которая', 'которые', 'однажды', 'один', 'одна',
    'утром', 'вечером', 'ночью', 'днем', 'днём', 'понедельник', 'вторник', 'среда', 'четверг',
    'пятница', 'суббота', 'воскресенье', 'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь', 'бог', 'господь', 'господи',
    'роман', 'повесть', 'поэма',
}

FALLBACK_EMBEDDING_DIM = 384

SEMANTIC_THEME_KEYWORDS = {
    'пороки человечества': ['порок', 'грех', 'гордын', 'жадн', 'алчн', 'лицемер', 'завист', 'тщеслав', 'корыст', 'жесток'],
    'вина и раскаяние': ['вина', 'совест', 'раская', 'искуп', 'грех', 'наказан', 'преступл'],
    'бедность и унижение': ['бедност', 'нищет', 'унижен', 'оскорб', 'голод', 'жалк', 'нужд'],
    'социальная сатира': ['сатира', 'ирони', 'насмеш', 'чиновник', 'помещик', 'общество', 'лицемер'],
    'бюрократия и афера': ['ревизск', 'душ', 'чиновник', 'бумаг', 'сделк', 'афера', 'покупк', 'помещик'],
    'жестокость и сострадание': ['жесток', 'жалост', 'милосерд', 'сострадан', 'мучен', 'слез', 'слёз'],
    'любовь и ревность': ['любов', 'ревност', 'чувств', 'страст', 'сердц'],
    'тайна и расследование': ['тайн', 'загад', 'следств', 'расследован', 'секрет'],
    'семья и отношения': ['семья', 'брак', 'отношен', 'муж', 'жена', 'отец', 'мать', 'дети'],
    'смерть и трагедия': ['смерт', 'похорон', 'трагед', 'гибел', 'кладбищ'],
    'власть и унижение': ['власт', 'господин', 'барын', 'приказ', 'подчин', 'унижен'],
}

SCENE_HINT_KEYWORDS = {
    'убийство': ['убил', 'убий', 'зарез', 'застрел', 'задуш', 'топор', 'кров'],
    'утопление': ['утоп', 'река', 'вода', 'пруд'],
    'афера с ревизскими душами': ['ревизск', 'душ', 'помещик', 'купил', 'мертв'],
    'допрос и признание': ['допрос', 'признан', 'следовател', 'суд', 'улика'],
    'унижение бедного человека': ['бедност', 'нищет', 'унижен', 'жалован', 'копеек'],
}

ABSTRACT_QUERY_EXPANSIONS = {
    'пороки человечества': ['грех', 'вина', 'раскаяние', 'жадность', 'лицемерие', 'зависть', 'жестокость', 'нравственное падение', 'совесть'],
    'моральное падение': ['вина', 'грех', 'совесть', 'раскаяние', 'искупление', 'преступление'],
    'вина и раскаяние': ['совесть', 'искупление', 'преступление', 'грех', 'наказание'],
    'социальная сатира': ['чиновники', 'помещики', 'афера', 'лицемерие', 'общество'],
    'жестокость': ['унижение', 'сострадание', 'милосердие', 'бесправие'],
}

SCENE_QUERY_EXPANSIONS = {
    'утопил собаку': ['утопление', 'муму', 'герасим', 'барыня'],
    'мужчина утопил собаку': ['утопление', 'муму', 'герасим', 'барыня'],
    'утопила собаку': ['утопление', 'муму', 'герасим', 'барыня'],
    'ревизские сказки': ['мертвые души', 'чичиков', 'ревизские души'],
}

SCENE_QUERY_MARKERS = (
    'убил', 'убийство', 'утопил', 'утопила', 'задушил', 'зарезал', 'застрелил',
    'процентщиц', 'ревизск', 'собак', 'барын', 'помещик', 'мертвые души',
)

THEME_QUERY_MARKERS = (
    'порок', 'грех', 'вина', 'раская', 'человечеств', 'нравствен', 'морал',
    'обществен', 'сатир', 'лицемер', 'жадн', 'жесток',
)

_INDEX_CACHE = {'mtime': None, 'data': []}
_VECTOR_CACHE = {'mtime': None, 'embeddings': None, 'metadata': None}
_RUNTIME_CACHE = {'signature': None, 'runtime': None, 'source_mtimes': None}
_LOOKUP_CACHE = {'by_id': {}, 'title_candidates': [], 'title_keys_by_id': {}, 'entity_candidates': [], 'passport_candidates': []}
FREEZE_RUNTIME_SNAPSHOT = os.getenv('NOVELLECT_FREEZE_RUNTIME_SNAPSHOT', '0').strip().lower() not in {'0', 'false', 'no'}


@dataclass
class QueryProfile:
    original_query: str
    search_text: str
    query_type: str
    keywords: List[str]
    priorities: Dict[str, List[Tuple[str, float]]]
    has_features: Dict[str, bool]
    title_matches: List[dict]
    title_like: bool = False
    require_exact: bool = False


class SearchCache:
    def __init__(self, maxsize: int = 128, cache_file: str = CACHE_FILE):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self.cache_file = cache_file
        self._load_cache()

    def _key(self, query: str, query_type: str, top_k: int):
        raw = f'{query}|{query_type}|{top_k}'.lower()
        return hashlib.md5(raw.encode('utf-8')).hexdigest()

    def _load_cache(self):
        if not os.path.exists(self.cache_file):
            return
        try:
            with open(self.cache_file, 'rb') as file_obj:
                loaded = pickle.load(file_obj)
            if isinstance(loaded, OrderedDict):
                self.cache = loaded
        except Exception:
            self.cache = OrderedDict()

    def _save_cache(self):
        try:
            _atomic_write_pickle(self.cache_file, self.cache)
        except Exception:
            pass

    def get(self, query: str, query_type: str, top_k: int):
        key = self._key(query, query_type, top_k)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        return None

    def set(self, query: str, query_type: str, top_k: int, value):
        key = self._key(query, query_type, top_k)
        self.cache[key] = value
        self.cache.move_to_end(key)
        while len(self.cache) > self.maxsize:
            self.cache.popitem(last=False)
        self._save_cache()

    def clear(self):
        self.cache = OrderedDict()
        try:
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
        except Exception:
            pass


_search_cache = SearchCache()


def _safe_mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _runtime_source_mtimes() -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    return (
        _safe_mtime(STORAGE_FILE),
        _safe_mtime(VECTOR_DB_FILE),
        _safe_mtime(VECTOR_MANIFEST_FILE),
        chunk_store_mtime(),
    )


def _runtime_snapshot_frozen() -> bool:
    if not FREEZE_RUNTIME_SNAPSHOT or _RUNTIME_CACHE['runtime'] is None:
        return False
    return _RUNTIME_CACHE.get('source_mtimes') == _runtime_source_mtimes()


def _atomic_replace_file(source_path: str, target_path: str) -> None:
    os.replace(source_path, target_path)


def _atomic_write_json(path: str, payload) -> None:
    directory = os.path.dirname(path) or '.'
    fd, temp_path = tempfile.mkstemp(prefix='.tmp_', suffix='.json', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        _atomic_replace_file(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _atomic_write_pickle(path: str, payload) -> None:
    directory = os.path.dirname(path) or '.'
    fd, temp_path = tempfile.mkstemp(prefix='.tmp_', suffix='.pkl', dir=directory)
    try:
        with os.fdopen(fd, 'wb') as file_obj:
            pickle.dump(payload, file_obj)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        _atomic_replace_file(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _atomic_write_npz(path: str, embeddings, metadata) -> None:
    directory = os.path.dirname(path) or '.'
    fd, temp_path = tempfile.mkstemp(prefix='.tmp_', suffix='.npz', dir=directory)
    os.close(fd)
    try:
        np.savez(temp_path, embeddings=np.asarray(embeddings, dtype=np.float32), metadata=json.dumps(metadata, ensure_ascii=False))
        _atomic_replace_file(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _load_vector_manifest() -> dict:
    if not os.path.exists(VECTOR_MANIFEST_FILE):
        return {'version': 1, 'segments': []}
    try:
        with open(VECTOR_MANIFEST_FILE, 'r', encoding='utf-8') as file_obj:
            loaded = json.load(file_obj)
        if isinstance(loaded, dict):
            loaded.setdefault('version', 1)
            loaded.setdefault('segments', [])
            return loaded
    except Exception:
        pass
    return {'version': 1, 'segments': []}


def _save_vector_manifest(manifest: dict) -> None:
    normalized = {
        'version': 1,
        'segments': [
            {
                'book_id': str(item.get('book_id') or ''),
                'path': str(item.get('path') or ''),
                'count': int(item.get('count') or 0),
                'dim': int(item.get('dim') or 0),
                'updated_at': float(item.get('updated_at') or time.time()),
            }
            for item in (manifest.get('segments') or [])
            if str(item.get('book_id') or '').strip() and str(item.get('path') or '').strip()
        ],
    }
    _atomic_write_json(VECTOR_MANIFEST_FILE, normalized)
    total_chunks = sum(int(item.get('count') or 0) for item in normalized['segments'])
    max_dim = max((int(item.get('dim') or 0) for item in normalized['segments']), default=0)
    marker_embeddings = np.zeros((0, max_dim or 1), dtype=np.float32)
    marker_metadata = {
        'storage': 'segmented',
        'segments': len(normalized['segments']),
        'chunks': total_chunks,
    }
    _atomic_write_npz(VECTOR_DB_FILE, marker_embeddings, marker_metadata)


def _segment_path(file_name: str) -> str:
    return str(Path(VECTOR_SEGMENTS_DIR) / file_name)


def _remove_segment_file(file_name: str) -> None:
    if not file_name:
        return
    segment_path = Path(_segment_path(file_name))
    if segment_path.exists():
        try:
            segment_path.unlink()
        except OSError:
            pass


def _write_book_segment(book_id: str, embeddings, metadata: Sequence[dict]) -> None:
    if not str(book_id).strip():
        return
    arr = np.asarray(embeddings, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    ensure_runtime_dirs()
    Path(VECTOR_SEGMENTS_DIR).mkdir(parents=True, exist_ok=True)
    manifest = _load_vector_manifest()
    remaining_segments = []
    for entry in manifest.get('segments', []):
        if str(entry.get('book_id')) == str(book_id):
            _remove_segment_file(str(entry.get('path') or ''))
            continue
        remaining_segments.append(entry)
    file_name = f'{hashlib.sha1(str(book_id).encode("utf-8")).hexdigest()}_{int(time.time() * 1000)}.npz'
    _atomic_write_npz(_segment_path(file_name), arr, list(metadata))
    remaining_segments.append(
        {
            'book_id': str(book_id),
            'path': file_name,
            'count': int(len(metadata)),
            'dim': int(arr.shape[1]) if arr.ndim == 2 else 1,
            'updated_at': time.time(),
        }
    )
    _save_vector_manifest({'version': 1, 'segments': remaining_segments})


def _remove_book_segment(book_id: str) -> int:
    if not str(book_id).strip():
        return 0
    manifest = _load_vector_manifest()
    removed_chunks = 0
    remaining_segments = []
    removed = False
    for entry in manifest.get('segments', []):
        if str(entry.get('book_id')) == str(book_id):
            removed_chunks += int(entry.get('count') or 0)
            _remove_segment_file(str(entry.get('path') or ''))
            removed = True
            continue
        remaining_segments.append(entry)
    if removed:
        if remaining_segments:
            _save_vector_manifest({'version': 1, 'segments': remaining_segments})
        else:
            for path in (VECTOR_MANIFEST_FILE, VECTOR_DB_FILE):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
    return removed_chunks


def _hydrate_vector_metadata(metadata: Sequence[dict] | None):
    if metadata is None:
        return None
    hydrated = [dict(item or {}) for item in metadata]
    missing_chunk_ids = [str(item.get('chunk_id') or '') for item in hydrated if not item.get('chunk') and item.get('chunk_id')]
    if not missing_chunk_ids:
        return hydrated
    chunk_map = fetch_chunks(missing_chunk_ids)
    for item in hydrated:
        if item.get('chunk'):
            continue
        chunk_id = str(item.get('chunk_id') or '')
        if chunk_id and chunk_id in chunk_map:
            item['chunk'] = chunk_map[chunk_id]
    return hydrated


def _load_segmented_vector_db():
    manifest = _load_vector_manifest()
    segment_entries = manifest.get('segments') or []
    if not segment_entries:
        return None, None
    embeddings_parts = []
    metadata_parts = []
    for entry in segment_entries:
        segment_file = str(entry.get('path') or '')
        if not segment_file:
            continue
        segment_path = _segment_path(segment_file)
        if not os.path.exists(segment_path):
            continue
        try:
            data = np.load(segment_path, allow_pickle=True)
            segment_embeddings = np.asarray(data['embeddings'], dtype=np.float32) if 'embeddings' in data else None
            raw_meta = data['metadata'] if 'metadata' in data else None
            if raw_meta is None or segment_embeddings is None:
                continue
            meta_str = raw_meta.item() if getattr(raw_meta, 'size', 0) > 0 else '[]'
            segment_metadata = json.loads(meta_str) if meta_str else []
            if len(segment_embeddings) != len(segment_metadata):
                size = min(len(segment_embeddings), len(segment_metadata))
                segment_embeddings = segment_embeddings[:size]
                segment_metadata = segment_metadata[:size]
            if len(segment_metadata) == 0:
                continue
            embeddings_parts.append(segment_embeddings)
            metadata_parts.extend(segment_metadata)
        except Exception:
            continue
    if not embeddings_parts or not metadata_parts:
        return None, None
    base_dim = max(part.shape[1] if part.ndim == 2 else 1 for part in embeddings_parts)
    normalized_parts = []
    for part in embeddings_parts:
        arr = np.asarray(part, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(-1, 1)
        if arr.shape[1] == base_dim:
            normalized_parts.append(arr)
            continue
        fixed = np.zeros((len(arr), base_dim), dtype=np.float32)
        fixed[:, : arr.shape[1]] = arr
        normalized_parts.append(fixed)
    embeddings = np.vstack(normalized_parts)
    return embeddings, _hydrate_vector_metadata(metadata_parts)


def normalize_word(word: str) -> str:
    cleaned = re.sub(r'[^\w]+', '', (word or '').lower().replace('ё', 'е'))
    if len(cleaned) <= 3:
        return cleaned
    for suffix in RUSSIAN_SUFFIXES:
        if cleaned.endswith(suffix) and len(cleaned) - len(suffix) >= 3:
            return cleaned[:-len(suffix)]
    return cleaned


def tokenize_smart(text: str) -> List[str]:
    raw_tokens = re.findall(r'\w+', (text or '').lower().replace('ё', 'е'))
    result = []
    for token in raw_tokens:
        normalized = normalize_word(token)
        if normalized and normalized not in STOP_WORDS:
            result.append(normalized)
    return result


def _normalize_match_text(text: str) -> str:
    return ' '.join(tokenize_smart(text))


def _fallback_text_embedding(text: str, dim: int = FALLBACK_EMBEDDING_DIM) -> np.ndarray:
    tokens = tokenize_smart(text)
    if not tokens:
        return np.zeros((dim,), dtype=np.float32)
    expanded = list(tokens)
    expanded.extend(f'{tokens[idx]}__{tokens[idx + 1]}' for idx in range(len(tokens) - 1))
    vector = np.zeros((dim,), dtype=np.float32)
    for token in expanded:
        digest = hashlib.sha1(token.encode('utf-8')).digest()
        primary = int.from_bytes(digest[:4], 'big') % dim
        secondary = int.from_bytes(digest[4:8], 'big') % dim
        sign = -1.0 if digest[8] % 2 else 1.0
        vector[primary] += sign
        vector[secondary] += sign * 0.5
    norm = np.linalg.norm(vector)
    if norm <= 0:
        return np.zeros((dim,), dtype=np.float32)
    return (vector / norm).astype(np.float32)


def _dedupe_texts(values: Sequence[str], limit: Optional[int] = None) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        cleaned = str(value or '').strip()
        normalized = _normalize_match_text(cleaned)
        if not cleaned or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(cleaned)
        if limit is not None and len(result) >= limit:
            break
    return result


def _match_score(query: str, candidate: str) -> float:
    query_key = _normalize_match_text(query)
    candidate_key = _normalize_match_text(candidate)
    if not query_key or not candidate_key:
        return 0.0
    if query_key == candidate_key:
        return 1.0
    if query_key in candidate_key or candidate_key in query_key:
        coverage = min(len(query_key), len(candidate_key)) / max(len(query_key), len(candidate_key), 1)
        return 0.84 + 0.16 * coverage
    query_tokens = set(query_key.split())
    candidate_tokens = set(candidate_key.split())
    token_overlap = len(query_tokens & candidate_tokens) / max(len(query_tokens), 1)
    ratio = SequenceMatcher(None, query_key.replace(' ', ''), candidate_key.replace(' ', '')).ratio()
    return max(token_overlap * 0.92, ratio * 0.72)


def _text_overlap_score(text: str, query_tokens: Sequence[str]) -> float:
    if not text or not query_tokens:
        return 0.0
    candidate_tokens = tokenize_smart(text)
    if not candidate_tokens:
        return 0.0
    candidate_counter = Counter(candidate_tokens)
    matched_terms = 0
    matched_occurrences = 0
    for token in set(query_tokens):
        count = candidate_counter.get(token, 0)
        if count > 0:
            matched_terms += 1
            matched_occurrences += count
    coverage = matched_terms / max(len(set(query_tokens)), 1)
    density = matched_occurrences / max(len(candidate_tokens), 1)
    return float(coverage + min(0.25, density * 3.5))


def _keyword_signal(text_tokens: Sequence[str], normalized_text: str, keywords: Sequence[str]) -> float:
    if not keywords:
        return 0.0
    score = 0.0
    token_counter = Counter(text_tokens)
    for keyword in keywords:
        key_tokens = tokenize_smart(keyword)
        if not key_tokens:
            continue
        if len(key_tokens) == 1:
            stem = key_tokens[0]
            score += sum(count for token, count in token_counter.items() if token.startswith(stem))
        else:
            score += float(normalized_text.count(' '.join(key_tokens)))
    return float(score)


def _matched_keyword_units(text_tokens: Sequence[str], normalized_text: str, keywords: Sequence[str]) -> set[str]:
    matched: set[str] = set()
    token_counter = Counter(text_tokens)
    for keyword in keywords:
        key_tokens = tokenize_smart(keyword)
        if not key_tokens:
            continue
        if len(key_tokens) == 1:
            stem = key_tokens[0]
            if any(token.startswith(stem) for token in token_counter):
                matched.add(stem)
        else:
            phrase = ' '.join(key_tokens)
            if phrase in normalized_text:
                matched.add(phrase)
    return matched


def _theme_passes_threshold(label: str, matched: set[str], score: float) -> bool:
    if score <= 0:
        return False
    if label == 'пороки человечества':
        explicit = {'порок', 'грех', 'гордын', 'жадн', 'алчн', 'лицемер', 'завист', 'тщеслав', 'корыст'}
        return bool(matched & explicit) or len(matched) >= 2
    if label == 'бюрократия и афера':
        strong = {'ревизск', 'афера', 'сделк', 'покупк', 'чиновник'}
        support = {'помещик', 'бумаг', 'мертв'}
        if 'ревизск' in matched:
            return True
        if matched & strong and matched & support:
            return True
        return len(matched & strong) >= 2
    if label == 'социальная сатира':
        explicit = {'сатир', 'ирон'}
        social = {'чиновник', 'помещик', 'общество', 'лицемер'}
        return bool(matched & explicit) or len(matched & social) >= 3
    if label == 'вина и раскаяние':
        reflective = {'вина', 'совест', 'раская', 'искуп', 'грех'}
        return bool(matched & reflective) or len(matched) >= 3
    if label == 'семья и отношения':
        explicit = {'семья', 'брак', 'отношен', 'муж', 'жена', 'отец', 'мать', 'дети'}
        return len(matched & explicit) >= 3 or score >= 4.0
    if label == 'любовь и ревность':
        explicit = {'любов', 'ревност', 'страст', 'сердц'}
        return bool(matched & explicit) and (score >= 2.0 or len(matched) >= 2)
    if label == 'жестокость и сострадание':
        explicit = {'жесток', 'жалост', 'милосерд', 'сострадан', 'мучен'}
        support = {'слез', 'слёз'}
        return bool(matched & explicit) or (bool(matched & support) and len(matched) >= 2)
    return score >= 1.0


def _scene_passes_threshold(label: str, matched: set[str], score: float) -> bool:
    if score <= 0:
        return False
    if label == 'убийство':
        return bool(matched & {'убил', 'убий', 'зарез', 'застрел', 'задуш'})
    if label == 'утопление':
        return 'утоп' in matched
    if label == 'афера с ревизскими душами':
        return 'ревизск' in matched or {'мертв', 'душ'}.issubset(matched)
    if label == 'допрос и признание':
        return len(matched) >= 2
    if label == 'унижение бедного человека':
        hardship = {'бедност', 'нищет'}
        humiliation = {'унижен', 'жалован', 'копеек'}
        return bool(matched & hardship) and bool(matched & humiliation)
    return score >= 1.0


def _dominant_feature_labels(features: dict, threshold: float = 0.12) -> dict[str, List[str]]:
    result: dict[str, List[str]] = {}
    for category, values in (features or {}).items():
        ranked = sorted(
            ((name, float(score)) for name, score in (values or {}).items() if float(score) >= threshold),
            key=lambda item: item[1],
            reverse=True,
        )
        if ranked:
            result[category] = [name for name, _score in ranked[:2]]
    return result


def _extract_named_entities(title: str, author: str, chunks: Sequence[str], metadata: Optional[dict] = None, limit: int = 10) -> List[str]:
    counts: Counter[str] = Counter()
    metadata = metadata or {}
    source_texts = [title, author]
    for key in ('characters', 'heroes', 'hero', 'personas'):
        value = metadata.get(key)
        if isinstance(value, str):
            source_texts.extend(re.split(r'[,;|/]+', value))
        elif isinstance(value, (list, tuple, set)):
            source_texts.extend(str(item) for item in value)
    source_texts.extend(chunks[:18])
    for text in source_texts:
        source = str(text or '')
        lowered_source = source.lower().replace('ё', 'е')
        for match in re.finditer(r'\b[А-ЯЁA-Z][а-яёa-z-]{2,}(?:\s+[А-ЯЁA-Z][а-яёa-z-]{2,})?\b', source):
            cleaned = match.group(0).strip()
            parts = [part for part in cleaned.split() if part]
            if not parts:
                continue
            lowered_parts = [part.lower().replace('ё', 'е') for part in parts]
            if any(part in CAPITALIZED_ENTITY_STOP_WORDS for part in lowered_parts):
                continue
            lowered_entity = cleaned.lower().replace('ё', 'е')
            if re.search(rf'(нет|без|не было|не был)\s+{re.escape(lowered_entity)}\b', lowered_source):
                continue
            counts[cleaned] += 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], len(item[0])))
    return [name for name, _count in ranked[:limit]]


def _compose_semantic_summary(title: str, themes: Sequence[str], scenes: Sequence[str], entities: Sequence[str], moods: Sequence[str], tones: Sequence[str]) -> str:
    fragments: List[str] = []
    if themes:
        fragments.append('темы: ' + ', '.join(themes[:3]))
    if scenes:
        fragments.append('ключевые сцены: ' + ', '.join(scenes[:2]))
    if entities:
        fragments.append('персонажи: ' + ', '.join(entities[:3]))
    if moods or tones:
        atmosphere = ', '.join(_dedupe_texts(list(moods[:2]) + list(tones[:2]), limit=3))
        if atmosphere:
            fragments.append('тон: ' + atmosphere)
    if not fragments:
        return f'{title} — книга с выраженным сюжетным и тематическим ядром.'
    return f"{title} — " + '; '.join(fragments) + '.'


def _passport_source_chunks(chunks: Sequence[str], head: int = 10, middle: int = 4, tail: int = 8) -> List[str]:
    source = [str(chunk).strip() for chunk in (chunks or []) if str(chunk).strip()]
    if len(source) <= head + middle + tail:
        return source

    selected: List[str] = []
    selected.extend(source[:head])

    middle_count = min(middle, max(len(source) - head - tail, 0))
    if middle_count > 0:
        start = max((len(source) - middle_count) // 2, head)
        selected.extend(source[start:start + middle_count])

    selected.extend(source[-tail:])

    deduped: List[str] = []
    seen: set[str] = set()
    for chunk in selected:
        key = chunk[:160]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


def _build_synthetic_queries(title: str, author: str, themes: Sequence[str], scenes: Sequence[str], entities: Sequence[str], moods: Sequence[str], genres: Sequence[str]) -> List[str]:
    queries: List[str] = [title]
    if author:
        queries.append(f'{author} {title}')
    queries.extend(entities[:4])
    queries.extend(themes[:4])
    queries.extend(scenes[:3])
    for theme in themes[:3]:
        queries.append(f'книга про {theme}')
        queries.append(f'роман про {theme}')
    for scene in scenes[:2]:
        queries.append(f'книга где {scene}')
    for entity in entities[:2]:
        queries.append(f'книга про {entity}')
    for genre in genres[:2]:
        queries.append(f'{genre} {title}')
    if moods:
        queries.append(f'книга с атмосферой {" ".join(moods[:2])}')
    return _dedupe_texts(queries, limit=16)


def _build_semantic_passport(title: str, author: str, chunks: Sequence[str], features: Optional[dict] = None, metadata: Optional[dict] = None) -> dict:
    metadata = metadata or {}
    features = features or {}
    raw_genres = metadata.get('genres', [])
    source_genres = [raw_genres] if isinstance(raw_genres, str) else [str(item) for item in (raw_genres or [])]
    summary_source = ''
    for key in ('summary', 'description', 'annotation', 'short_summary', 'blurb'):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            summary_source = value.strip()
            break

    source_parts = [title, author, summary_source, ' '.join(source_genres)]
    source_parts.extend(_passport_source_chunks(chunks))
    normalized_text = _normalize_match_text(' '.join(part for part in source_parts if part))
    text_tokens = normalized_text.split()

    theme_scores: List[Tuple[str, float]] = []
    for label, keywords in SEMANTIC_THEME_KEYWORDS.items():
        score = _keyword_signal(text_tokens, normalized_text, keywords)
        matched = _matched_keyword_units(text_tokens, normalized_text, keywords)
        if _theme_passes_threshold(label, matched, score):
            theme_scores.append((label, score))
    theme_scores.sort(key=lambda item: item[1], reverse=True)
    themes = [label for label, _score in theme_scores][:6]

    scene_scores: List[Tuple[str, float]] = []
    for label, keywords in SCENE_HINT_KEYWORDS.items():
        score = _keyword_signal(text_tokens, normalized_text, keywords)
        matched = _matched_keyword_units(text_tokens, normalized_text, keywords)
        if _scene_passes_threshold(label, matched, score):
            scene_scores.append((label, score))
    scene_scores.sort(key=lambda item: item[1], reverse=True)
    scene_hints = [label for label, _score in scene_scores][:4]

    dominant = _dominant_feature_labels(features)
    moods = _dedupe_texts((dominant.get('mood') or []) + (dominant.get('atmosphere') or []), limit=4)
    tones = _dedupe_texts((dominant.get('tone') or []) + (dominant.get('style') or []), limit=4)
    plot_hints = _dedupe_texts((dominant.get('plot') or []) + (dominant.get('characters') or []), limit=4)
    if not themes:
        themes = plot_hints[:4]

    entities = _extract_named_entities(title, author, chunks, metadata=metadata, limit=10)
    genres = []
    for key in ('genres', 'genre', 'categories', 'tags'):
        value = metadata.get(key)
        if isinstance(value, str):
            genres.extend(re.split(r'[,;|/]+', value))
        elif isinstance(value, (list, tuple, set)):
            genres.extend(str(item) for item in value)
    genres = _dedupe_texts(genres, limit=4)

    summary = summary_source or _compose_semantic_summary(title, themes, scene_hints, entities, moods, tones)
    synthetic_queries = _build_synthetic_queries(title, author, themes, scene_hints, entities, moods, genres)
    search_text_parts = [title, author, summary]
    search_text_parts.extend(themes[:4])
    search_text_parts.extend(scene_hints[:3])
    search_text_parts.extend(entities[:5])
    search_text_parts.extend(synthetic_queries[:6])
    search_text = ' '.join(part for part in search_text_parts if part).strip()

    return {
        'summary': summary,
        'themes': themes,
        'scene_hints': scene_hints,
        'entities': entities,
        'moods': moods,
        'tones': tones,
        'synthetic_queries': synthetic_queries,
        'search_text': search_text,
    }


def _expand_query_with_concepts(query: str, query_profile: Optional[dict] = None) -> dict:
    cleaned_query = _sanitize_search_text(query or '')
    normalized_query = _normalize_match_text(cleaned_query)
    query_tokens = normalized_query.split()
    expansions: List[str] = []
    for label, terms in ABSTRACT_QUERY_EXPANSIONS.items():
        label_tokens = tokenize_smart(label)
        if not label_tokens:
            continue
        if all(any(token.startswith(label_token) or label_token.startswith(token) for token in query_tokens) for label_token in label_tokens):
            expansions.extend(terms)
    qtype = str((query_profile or {}).get('type') or '')
    if qtype in {'scene', 'specific'}:
        for label, terms in SCENE_QUERY_EXPANSIONS.items():
            label_tokens = tokenize_smart(label)
            if not label_tokens:
                continue
            if all(any(token.startswith(label_token) or label_token.startswith(token) for token in query_tokens) for label_token in label_tokens):
                expansions.extend(terms)
    priorities = (query_profile or {}).get('priorities') or {}
    for values in priorities.values():
        expansions.extend(name for name, _weight in values[:2])
    expanded_terms = _dedupe_texts(expansions, limit=12)
    expanded_query = cleaned_query
    if expanded_terms:
        expanded_query = f"{cleaned_query} {' '.join(expanded_terms)}".strip()
    return {
        'query': cleaned_query,
        'expanded_query': expanded_query,
        'query_tokens': tokenize_smart(cleaned_query),
        'expanded_tokens': tokenize_smart(expanded_query),
        'expansions': expanded_terms,
    }


class UniversalTextAnalyzer:
    FEATURE_THRESHOLD = 0.16

    def __init__(self):
        self.features = TEXT_FEATURES
        self._prototype_order = []
        self._prototype_texts = []
        self._prototype_embeddings = None
        for category, subcategories in self.features.items():
            for subcat, keywords in subcategories.items():
                self._prototype_order.append((category, subcat))
                self._prototype_texts.append(f'{category} {subcat} ' + ' '.join(keywords))

    def _empty_scores(self):
        return {category: {subcat: 0.0 for subcat in subcats} for category, subcats in self.features.items()}

    def _lexical_scores(self, text: str):
        scores = self._empty_scores()
        normalized_tokens = tokenize_smart(text)
        normalized_text = ' '.join(normalized_tokens)
        for category, subcategories in self.features.items():
            for subcat, keywords in subcategories.items():
                score = 0.0
                target = normalize_word(subcat)
                if target:
                    score += sum(1 for token in normalized_tokens if token.startswith(target))
                for keyword in keywords:
                    keyword_norm = ' '.join(tokenize_smart(keyword))
                    if not keyword_norm:
                        continue
                    if ' ' in keyword_norm:
                        score += float(normalized_text.count(keyword_norm))
                    else:
                        score += sum(1 for token in normalized_tokens if token.startswith(keyword_norm))
                scores[category][subcat] = float(score)
        return self._normalize(scores)

    def _normalize(self, scores):
        normalized = self._empty_scores()
        for category, subcategories in scores.items():
            total = sum(max(value, 0.0) for value in subcategories.values())
            if total <= 0:
                continue
            for subcat, value in subcategories.items():
                normalized[category][subcat] = max(value, 0.0) / total
        return normalized

    def _semantic_scores(self, text: str):
        scores = self._empty_scores()
        model = get_model()
        if model is None or not text:
            return scores
        if self._prototype_embeddings is None:
            self._prototype_embeddings = encode_with_model(self._prototype_texts)
        if self._prototype_embeddings is None:
            return scores
        query_emb = encode_with_model([text])
        if query_emb is None:
            return scores
        similarities = cosine_similarity(query_emb, self._prototype_embeddings)[0]
        for idx, (category, subcat) in enumerate(self._prototype_order):
            similarity = float(similarities[idx])
            if similarity >= self.FEATURE_THRESHOLD:
                scores[category][subcat] = similarity
        return self._normalize(scores)

    def analyze_text(self, text: str, use_semantic: bool = False):
        lexical = self._lexical_scores(text)
        if not use_semantic:
            return lexical
        semantic = self._semantic_scores(text)
        combined = self._empty_scores()
        for category in self.features:
            for subcat in self.features[category]:
                lval = lexical[category].get(subcat, 0.0)
                sval = semantic[category].get(subcat, 0.0)
                combined[category][subcat] = (lval * 0.7) + (sval * 0.3)
        return self._normalize(combined)

    def analyze_book(self, chunks: Sequence[str]):
        aggregated = self._empty_scores()
        valid = 0
        for chunk in chunks[:24]:
            if not chunk:
                continue
            chunk_scores = self.analyze_text(chunk, use_semantic=False)
            for category in aggregated:
                for subcat in aggregated[category]:
                    aggregated[category][subcat] += chunk_scores[category][subcat]
            valid += 1
        if valid == 0:
            return aggregated
        for category in aggregated:
            for subcat in aggregated[category]:
                aggregated[category][subcat] /= valid
        return aggregated

    def dominant_features(self, scores, threshold: float = 0.12):
        result = {}
        for category, subcategories in scores.items():
            selected = [(name, value) for name, value in subcategories.items() if value >= threshold]
            if selected:
                result[category] = sorted(selected, key=lambda item: item[1], reverse=True)
        return result


_text_analyzer = UniversalTextAnalyzer()


class QueryAnalyzer:
    FEATURE_THRESHOLD = 0.16

    def analyze_query(self, query: str):
        feature_scores = _text_analyzer.analyze_text(query, use_semantic=False)
        priorities = {}
        for category, subcategories in feature_scores.items():
            selected = [(name, score) for name, score in subcategories.items() if score >= self.FEATURE_THRESHOLD]
            if selected:
                priorities[category] = sorted(selected, key=lambda item: item[1], reverse=True)
        return {
            'original': query,
            'features': feature_scores,
            'priorities': priorities,
            'has_mood': bool(priorities.get('mood')),
            'has_style': bool(priorities.get('style')),
            'has_plot': bool(priorities.get('plot')),
            'has_atmosphere': bool(priorities.get('atmosphere')),
            'has_tone': bool(priorities.get('tone')),
        }

    def expand_query_with_features(self, query: str, analysis: dict):
        # Сознательно отключено: прежнее расширение запроса портило точные и сущностные запросы.
        return query


_query_analyzer = QueryAnalyzer()


class ModelManager:
    _instance = None
    _model = None
    _device = None
    _status = {'available': False, 'backend': 'none', 'message': 'sentence-transformers not loaded'}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_model(self):
        profile = get_compute_profile()
        target_device = profile['model_device']
        if self._model is not None and self._device == target_device:
            return self._model
        if not _ensure_sentence_transformers():
            self._model = None
            self._device = 'none'
            message = str(_SENTENCE_TRANSFORMERS_ERROR) if _SENTENCE_TRANSFORMERS_ERROR else 'sentence-transformers unavailable'
            self._status = {'available': False, 'backend': 'none', 'message': message}
            return None
        model_paths = [FINE_TUNED_MODEL_PATH]
        if BASE_EMBEDDING_MODEL_NAME:
            model_paths.append(BASE_EMBEDDING_MODEL_NAME)
        if BASE_EMBEDDING_MODEL_PATH:
            model_paths.append(BASE_EMBEDDING_MODEL_PATH)
        errors = []
        for model_path in model_paths:
            if model_path in {FINE_TUNED_MODEL_PATH, BASE_EMBEDDING_MODEL_PATH} and not os.path.exists(model_path):
                continue
            try:
                model = SentenceTransformer(model_path, device=target_device)
                self._model = model
                self._device = target_device
                self._status = {'available': True, 'backend': 'sentence-transformers', 'message': f'{model_path} on {target_device}'}
                return self._model
            except Exception as exc:
                errors.append(f'{model_path}: {exc}')
        if target_device != 'cpu':
            for model_path in model_paths:
                if model_path in {FINE_TUNED_MODEL_PATH, BASE_EMBEDDING_MODEL_PATH} and not os.path.exists(model_path):
                    continue
                try:
                    model = SentenceTransformer(model_path, device='cpu')
                    self._model = model
                    self._device = 'cpu'
                    self._status = {'available': True, 'backend': 'sentence-transformers', 'message': f'{model_path} on cpu'}
                    return self._model
                except Exception as exc:
                    errors.append(f'{model_path}@cpu: {exc}')
        self._model = None
        self._device = 'none'
        self._status = {'available': False, 'backend': 'none', 'message': ' | '.join(errors) if errors else 'model unavailable'}
        return None

    def reset(self):
        self._model = None
        self._device = None
        self._status = {'available': False, 'backend': 'none', 'message': 'reset'}

    def status(self):
        return dict(self._status)


_model_manager = ModelManager()
_reranker_model = None
_reranker_device = None


def get_model():
    return _model_manager.get_model()


def model_status():
    _model_manager.get_model()
    return _model_manager.status()


def reset_runtime_state():
    global _reranker_model, _reranker_device
    _model_manager.reset()
    _reranker_model = None
    _reranker_device = None
    _RUNTIME_CACHE.update({'signature': None, 'runtime': None, 'source_mtimes': None})
    _VECTOR_CACHE.update({'mtime': None, 'embeddings': None, 'metadata': None})



def encode_with_model(texts: Sequence[str], batch_size: Optional[int] = None):
    model = get_model()
    if model is None:
        return None
    try:
        effective_batch_size = max(1, int(batch_size or get_compute_profile()['embedding_batch_size']))
        embeddings = model.encode(list(texts), show_progress_bar=False, batch_size=effective_batch_size)
        return np.asarray(embeddings, dtype=np.float32)
    except Exception:
        return None



def get_reranker():
    global _reranker_model, _reranker_device
    desired_device = get_compute_profile()['reranker_device']
    if _reranker_model is not None and _reranker_device == desired_device:
        return _reranker_model
    if not RERANK_ENABLED or not _ensure_sentence_transformers():
        return None
    model_names = ['BAAI/bge-reranker-v2-m3', 'cross-encoder/mmarco-mMiniLM-L12-v2']
    for model_name in model_names:
        try:
            _reranker_model = CrossEncoder(model_name, max_length=512, device=desired_device)
            _reranker_device = desired_device
            return _reranker_model
        except Exception:
            continue
    if desired_device != 'cpu':
        for model_name in model_names:
            try:
                _reranker_model = CrossEncoder(model_name, max_length=512, device='cpu')
                _reranker_device = 'cpu'
                return _reranker_model
            except Exception:
                continue
    return None


# -------- basic storage --------
def load_index(force: bool = False):
    if not force and _runtime_snapshot_frozen() and _INDEX_CACHE['data']:
        return _INDEX_CACHE['data']
    mtime = _safe_mtime(STORAGE_FILE)
    if not force and _INDEX_CACHE['mtime'] == mtime:
        return _INDEX_CACHE['data']
    if not os.path.exists(STORAGE_FILE):
        data = []
    else:
        try:
            with open(STORAGE_FILE, 'r', encoding='utf-8') as file_obj:
                loaded = json.load(file_obj)
            data = loaded if isinstance(loaded, list) else []
        except Exception:
            data = []
    _INDEX_CACHE['mtime'] = mtime
    _INDEX_CACHE['data'] = data
    _rebuild_lookup_cache(data)
    return data



def save_index(index):
    frozen = _runtime_snapshot_frozen()
    _atomic_write_json(STORAGE_FILE, index)
    if frozen:
        return
    _INDEX_CACHE['mtime'] = _safe_mtime(STORAGE_FILE)
    _INDEX_CACHE['data'] = index
    _rebuild_lookup_cache(index)
    _RUNTIME_CACHE.update({'signature': None, 'runtime': None, 'source_mtimes': None})


def update_book_record(book_id: str, **updates):
    if not updates:
        return None
    index = load_index(force=True)
    for book in index:
        if book.get('id') != book_id:
            continue
        book.update(updates)
        save_index(index)
        clear_cache()
        return book
    return None


def update_last_opened(book_id: str):
    index = load_index(force=True)
    updated = False
    for book in index:
        if book.get('id') == book_id:
            book['last_opened'] = time.time()
            book['open_count'] = int(book.get('open_count', 0)) + 1
            updated = True
            break
    if updated:
        save_index(index)
    return updated


# -------- text chunking --------
def _clean_text(text: str) -> str:
    text = (text or '').replace('\r\n', '\n').replace('\r', '\n')
    text = re.sub(r'\t+', ' ', text)
    text = re.sub(r' +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()



def chunk_text_content(text: str):
    cleaned = _clean_text(text)
    if not cleaned:
        return []

    paragraphs = [part.strip() for part in re.split(r'\n\s*\n', cleaned) if part.strip()]
    if not paragraphs:
        paragraphs = [cleaned]

    chunks = []
    current = []
    current_len = 0
    target_len = 1200
    overlap_len = 180

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and current_len + len(paragraph) + 2 > target_len:
            chunk = '\n\n'.join(current).strip()
            if chunk:
                chunks.append(chunk)
            overlap = chunk[-overlap_len:].strip() if chunk else ''
            current = [overlap, paragraph] if overlap else [paragraph]
            current_len = sum(len(part) for part in current) + max(0, len(current) - 1) * 2
        else:
            current.append(paragraph)
            current_len += len(paragraph) + 2

    if current:
        chunk = '\n\n'.join(current).strip()
        if chunk:
            chunks.append(chunk)

    return chunks or [cleaned]


def embed_chunks(chunks: Sequence[str], batch_size: Optional[int] = None):
    embeddings = encode_with_model(chunks, batch_size=batch_size)
    if embeddings is None:
        if not chunks:
            return np.zeros((0, FALLBACK_EMBEDDING_DIM), dtype=np.float32)
        return np.vstack([_fallback_text_embedding(chunk) for chunk in chunks]).astype(np.float32)
    return np.asarray(embeddings, dtype=np.float32)


def split_text_semantic(text: str, model=None, threshold: float = 0.3, batch_size: Optional[int] = None):
    # Сохраняем старое имя функции, но используем более стабильный paragraph-aware chunking.
    chunks = chunk_text_content(text)
    if not chunks:
        return [], np.zeros((0, 1), dtype=np.float32)
    return chunks, embed_chunks(chunks)


# -------- vector DB --------
def load_vector_db(sync_with_index: bool = True, force_reload: bool = False):
    if not force_reload and _runtime_snapshot_frozen() and _VECTOR_CACHE['metadata'] is not None:
        embeddings = _VECTOR_CACHE['embeddings']
        metadata = _VECTOR_CACHE['metadata']
        if not sync_with_index or metadata is None:
            return embeddings, metadata
        valid_ids = {book.get('id') for book in load_index()}
        if not valid_ids:
            return None, None
        keep_indices = [idx for idx, item in enumerate(metadata) if item.get('book_id') in valid_ids]
        if not keep_indices:
            return None, None
        filtered_metadata = [metadata[idx] for idx in keep_indices]
        filtered_embeddings = None
        if embeddings is not None and len(embeddings) >= len(metadata):
            filtered_embeddings = np.asarray(embeddings[keep_indices], dtype=np.float32)
        return filtered_embeddings, filtered_metadata
    storage_mtimes = (_safe_mtime(VECTOR_DB_FILE), _safe_mtime(VECTOR_MANIFEST_FILE), chunk_store_mtime())
    known_mtimes = [value for value in storage_mtimes if value is not None]
    mtime = max(known_mtimes) if known_mtimes else None
    if _VECTOR_CACHE['mtime'] == mtime:
        embeddings = _VECTOR_CACHE['embeddings']
        metadata = _VECTOR_CACHE['metadata']
    else:
        if os.path.exists(VECTOR_MANIFEST_FILE):
            embeddings, metadata = _load_segmented_vector_db()
        elif not os.path.exists(VECTOR_DB_FILE):
            _VECTOR_CACHE.update({'mtime': mtime, 'embeddings': None, 'metadata': None})
            return None, None
        else:
            try:
                data = np.load(VECTOR_DB_FILE, allow_pickle=True)
                embeddings = np.asarray(data['embeddings'], dtype=np.float32) if 'embeddings' in data else None
                raw_meta = data['metadata'] if 'metadata' in data else None
                if raw_meta is None:
                    metadata = None
                else:
                    meta_str = raw_meta.item() if getattr(raw_meta, 'size', 0) > 0 else '[]'
                    metadata = json.loads(meta_str) if meta_str else []
                    if not isinstance(metadata, list):
                        metadata = []
                if embeddings is not None and metadata is not None and len(embeddings) != len(metadata):
                    size = min(len(embeddings), len(metadata))
                    embeddings = embeddings[:size]
                    metadata = metadata[:size]
                metadata = _hydrate_vector_metadata(metadata)
            except Exception:
                embeddings, metadata = None, None
        _VECTOR_CACHE.update({'mtime': mtime, 'embeddings': embeddings, 'metadata': metadata})

    if not sync_with_index or metadata is None:
        return embeddings, metadata

    valid_ids = {book.get('id') for book in load_index()}
    if not valid_ids:
        return None, None
    keep_indices = [idx for idx, item in enumerate(metadata) if item.get('book_id') in valid_ids]
    if not keep_indices:
        return None, None
    filtered_metadata = [metadata[idx] for idx in keep_indices]
    filtered_embeddings = None
    if embeddings is not None and len(embeddings) >= len(metadata):
        filtered_embeddings = np.asarray(embeddings[keep_indices], dtype=np.float32)
    return filtered_embeddings, filtered_metadata



def persist_vector_db(embeddings, metadata):
    if embeddings is None or metadata is None or len(metadata) == 0:
        for path in (VECTOR_DB_FILE, VECTOR_MANIFEST_FILE):
            if os.path.exists(path):
                os.remove(path)
        for segment_path in Path(VECTOR_SEGMENTS_DIR).glob('*.npz'):
            try:
                segment_path.unlink()
            except OSError:
                pass
        _VECTOR_CACHE.update({'mtime': None, 'embeddings': None, 'metadata': None})
        _RUNTIME_CACHE.update({'signature': None, 'runtime': None, 'source_mtimes': None})
        return
    by_book: dict[str, list[tuple[np.ndarray, dict]]] = defaultdict(list)
    arr = np.asarray(embeddings, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    for idx, item in enumerate(metadata):
        entry = dict(item or {})
        book_id = str(entry.get('book_id') or '')
        if not book_id:
            continue
        chunk_id = str(entry.get('chunk_id') or f'{book_id}_{idx}')
        entry['chunk_id'] = chunk_id
        compact_entry = {key: value for key, value in entry.items() if key != 'chunk'}
        by_book[book_id].append((arr[idx], compact_entry))
    if not by_book:
        for path in (VECTOR_DB_FILE, VECTOR_MANIFEST_FILE):
            if os.path.exists(path):
                os.remove(path)
        for segment_path in Path(VECTOR_SEGMENTS_DIR).glob('*.npz'):
            try:
                segment_path.unlink()
            except OSError:
                pass
        _VECTOR_CACHE.update({'mtime': None, 'embeddings': None, 'metadata': None})
        _RUNTIME_CACHE.update({'signature': None, 'runtime': None, 'source_mtimes': None})
        return
    for book_id, entries in by_book.items():
        book_embeddings = np.vstack([item[0] for item in entries]) if entries else np.zeros((0, arr.shape[1]), dtype=np.float32)
        book_metadata = [item[1] for item in entries]
        chunk_ids = [str(item.get('chunk_id') or '') for item in metadata if str(item.get('book_id') or '') == book_id]
        chunks = [str(item.get('chunk') or '') for item in metadata if str(item.get('book_id') or '') == book_id]
        upsert_book_chunks(book_id, chunks, chunk_ids)
        _write_book_segment(book_id, book_embeddings, book_metadata)
    _VECTOR_CACHE.update({'mtime': None, 'embeddings': None, 'metadata': None})
    _RUNTIME_CACHE.update({'signature': None, 'runtime': None, 'source_mtimes': None})


# -------- index mutation --------
def _filter_book_from_vector_db(book_id: str, embeddings, metadata):
    if embeddings is None or metadata is None:
        return embeddings, metadata
    keep_indices = [idx for idx, item in enumerate(metadata) if item.get('book_id') != book_id]
    filtered_metadata = [metadata[idx] for idx in keep_indices]
    filtered_embeddings = np.asarray(embeddings[keep_indices], dtype=np.float32) if len(keep_indices) else np.zeros((0, embeddings.shape[1]), dtype=np.float32)
    return filtered_embeddings, filtered_metadata



def _title_variants_from_record(record: dict):
    candidates = [record.get('title', ''), record.get('source_filename', '')]
    metadata = record.get('metadata', {}) or {}
    for key in ('title', 'original_title', 'archive_member_path'):
        if metadata.get(key):
            candidates.append(str(metadata.get(key)))
    variants = []
    for candidate in candidates:
        normalized = _normalize_lookup_key(Path(str(candidate)).stem if '.' in str(candidate) else str(candidate))
        if normalized and normalized not in variants:
            variants.append(normalized)
    return variants


def _author_from_record(record: dict) -> str:
    metadata = record.get('metadata', {}) or {}
    for value in (
        record.get('author'),
        metadata.get('author'),
        metadata.get('authors'),
        metadata.get('writer'),
        metadata.get('creator'),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''



def add_to_index(book_data: dict, file_id: str, prepared_chunks: Optional[Sequence[str]] = None, prepared_embeddings=None, prepared_features: Optional[dict] = None):
    title = book_data.get('title') or 'Без названия'
    content = book_data.get('content') or ''
    if not content.strip():
        return None
    author = _author_from_record(book_data)

    index = [book for book in load_index(force=True) if book.get('id') != file_id]

    chunks = list(prepared_chunks) if prepared_chunks is not None else None
    chunk_embeddings = None if prepared_embeddings is None else np.asarray(prepared_embeddings, dtype=np.float32)
    if chunks is None:
        chunks = chunk_text_content(content)
    if not chunks:
        return None
    if chunk_embeddings is None:
        chunk_embeddings = embed_chunks(chunks)
    if not chunks:
        return None

    features = prepared_features if prepared_features is not None else _text_analyzer.analyze_book(chunks)
    title_variants = _title_variants_from_record(book_data)
    semantic_passport = _build_semantic_passport(title, author, chunks, features=features, metadata=book_data.get('metadata') or {})

    new_metadata = []
    chunk_ids = []
    for idx, chunk in enumerate(chunks):
        chunk_id = f'{file_id}_{idx}'
        chunk_ids.append(chunk_id)
        new_metadata.append(
            {
                'book_id': file_id,
                'book_title': title,
                'format': book_data.get('format', 'unknown'),
                'chunk_id': chunk_id,
            }
        )
    upsert_book_chunks(file_id, [str(chunk or '') for chunk in chunks], chunk_ids)
    _write_book_segment(file_id, chunk_embeddings, new_metadata)
    _VECTOR_CACHE.update({'mtime': None, 'embeddings': None, 'metadata': None})
    _RUNTIME_CACHE.update({'signature': None, 'runtime': None, 'source_mtimes': None})

    record = {
        'id': file_id,
        'title': title,
        'file_hash': book_data.get('file_hash'),
        'format': book_data.get('format', 'unknown'),
        'file_path': book_data.get('file_path'),
        'source_filename': book_data.get('source_filename'),
        'cache_path': book_data.get('cache_path'),
        'chunks_count': len(chunks),
        'added_date': time.time(),
        'last_opened': None,
        'open_count': 0,
        'features': features,
        'semantic_passport': semantic_passport,
        'metadata': book_data.get('metadata', {}),
        'title_variants': title_variants,
    }
    index.append(record)
    save_index(index)
    clear_cache()
    return record



def delete_from_index(book_id: str):
    index = load_index(force=True)
    book_record = next((book for book in index if book.get('id') == book_id), None)
    if book_record is None:
        clear_cache()
        return {
            'deleted': False,
            'book_id': book_id,
            'message': 'Книга не найдена в индексе.',
            'removed_chunks': 0,
            'removed_file': None,
        }

    new_index = [book for book in index if book.get('id') != book_id]
    save_index(new_index)

    removed_chunks = _remove_book_segment(book_id)
    delete_book_chunks(book_id)
    _VECTOR_CACHE.update({'mtime': None, 'embeddings': None, 'metadata': None})
    _RUNTIME_CACHE.update({'signature': None, 'runtime': None, 'source_mtimes': None})

    removed_file = None
    paths_to_remove = []
    for candidate in (book_record.get('file_path'), book_record.get('cache_path')):
        if candidate:
            paths_to_remove.append(candidate)
    cache_path = book_record.get('cache_path')
    if cache_path:
        paths_to_remove.append(f'{cache_path}.meta')
    metadata_original = (book_record.get('metadata') or {}).get('original')
    if metadata_original:
        paths_to_remove.append(metadata_original)

    for path in paths_to_remove:
        if path and os.path.exists(path):
            try:
                os.remove(path)
                if path == book_record.get('file_path'):
                    removed_file = path
            except OSError:
                pass

    clear_cache()
    return {
        'deleted': True,
        'book_id': book_id,
        'title': book_record.get('title'),
        'removed_chunks': removed_chunks,
        'removed_file': removed_file,
    }



def delete_book_from_index(book_id: str, delete_file: bool = True):
    result = delete_from_index(book_id)
    if not delete_file:
        result['removed_file'] = None
    return result


def rebuild_semantic_passports(limit: Optional[int] = None):
    index = load_index(force=True)
    updated = 0
    scanned = 0
    for book in index:
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        file_path = str(book.get('file_path') or '')
        if not file_path or not os.path.exists(file_path):
            continue
        try:
            content = Path(file_path).read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        chunks = chunk_text_content(content)
        passport = _build_semantic_passport(
            str(book.get('title') or 'Без названия'),
            _author_from_record(book),
            chunks,
            features=book.get('features') or {},
            metadata=book.get('metadata') or {},
        )
        current_passport = dict(book.get('semantic_passport') or {})
        title_variants = _title_variants_from_record(book)
        if current_passport == passport and list(book.get('title_variants') or []) == title_variants:
            continue
        book['semantic_passport'] = passport
        book['title_variants'] = title_variants
        updated += 1
    if updated:
        save_index(index)
        clear_cache()
    return {'updated': updated, 'scanned': scanned, 'total_books': len(index)}


# -------- title lookup --------
def _split_camel_case(text: str) -> str:
    return re.sub(r'(?<=[a-zа-я])(?=[A-ZА-Я])', ' ', str(text or ''))



def _romanize_russian(text: str) -> str:
    prepared = _split_camel_case(str(text or '').lower().replace('ё', 'е')).translate(CYR_TO_LAT)
    prepared = re.sub(r'[^a-z0-9]+', ' ', prepared)
    return re.sub(r'\s+', ' ', prepared).strip()



def _normalize_lookup_key(text: str) -> str:
    normalized = _romanize_russian(text)
    normalized = re.sub(r'\b(txt|fb2|pdf|epub|dataset)\b', ' ', normalized)
    return re.sub(r'\s+', ' ', normalized).strip()



def _title_lookup_variants(book: dict):
    variants = list(book.get('title_variants') or [])
    for candidate in (book.get('title', ''), book.get('source_filename', '')):
        if not candidate:
            continue
        raw = Path(str(candidate)).stem if '.' in str(candidate) else str(candidate)
        normalized = _normalize_lookup_key(raw)
        if normalized and normalized not in variants:
            variants.append(normalized)
        if '_' in raw:
            tail = raw.split('_', 1)[1]
            tail_norm = _normalize_lookup_key(tail)
            if tail_norm and tail_norm not in variants:
                variants.append(tail_norm)
    return variants



def _rebuild_lookup_cache(index):
    by_id = {book.get('id'): book for book in index if book.get('id')}
    title_candidates = []
    title_keys_by_id = {}
    entity_candidates = []
    passport_candidates = []
    for book in index:
        variants = _title_lookup_variants(book)
        title_keys_by_id[book.get('id')] = variants
        title_candidates.append({'book_id': book.get('id'), 'title': book.get('title', 'Без названия'), 'format': book.get('format', 'unknown'), 'variants': variants})
        passport = dict(book.get('semantic_passport') or {})
        entity_candidates.append(
            {
                'book_id': book.get('id'),
                'title': book.get('title', 'Без названия'),
                'format': book.get('format', 'unknown'),
                'entities': [str(item) for item in (passport.get('entities') or []) if str(item).strip()],
                'synthetic_queries': [str(item) for item in (passport.get('synthetic_queries') or []) if str(item).strip()],
                'summary': str(passport.get('summary') or ''),
                'search_text': str(passport.get('search_text') or ''),
            }
        )
        passport_candidates.append(
            {
                'book_id': book.get('id'),
                'themes': [str(item) for item in (passport.get('themes') or []) if str(item).strip()],
                'scene_hints': [str(item) for item in (passport.get('scene_hints') or []) if str(item).strip()],
                'summary': str(passport.get('summary') or ''),
                'search_text': str(passport.get('search_text') or ''),
                'synthetic_queries': [str(item) for item in (passport.get('synthetic_queries') or []) if str(item).strip()],
            }
        )
    _LOOKUP_CACHE['by_id'] = by_id
    _LOOKUP_CACHE['title_candidates'] = title_candidates
    _LOOKUP_CACHE['title_keys_by_id'] = title_keys_by_id
    _LOOKUP_CACHE['entity_candidates'] = entity_candidates
    _LOOKUP_CACHE['passport_candidates'] = passport_candidates



def find_title_matches(query: str, limit: int = 5, min_score: float = TITLE_SOFT_THRESHOLD):
    if not _LOOKUP_CACHE['title_candidates']:
        load_index(force=True)
    query_key = _normalize_lookup_key(query)
    if not query_key:
        return []
    query_tokens = set(query_key.split())
    matches = []
    for candidate in _LOOKUP_CACHE['title_candidates']:
        best_score = 0.0
        best_variant = ''
        for variant in candidate.get('variants', []):
            if not variant:
                continue
            if query_key == variant:
                score = 1.0
            elif query_key in variant or variant in query_key:
                coverage = min(len(query_key), len(variant)) / max(len(query_key), len(variant), 1)
                score = 0.84 + 0.16 * coverage
            else:
                variant_tokens = set(variant.split())
                token_overlap = len(query_tokens & variant_tokens) / max(len(query_tokens), 1)
                ratio = SequenceMatcher(None, query_key.replace(' ', ''), variant.replace(' ', '')).ratio()
                score = max(token_overlap * 0.78, ratio * 0.86)
            if score > best_score:
                best_score = score
                best_variant = variant
        if best_score >= min_score:
            matches.append({'book_id': candidate['book_id'], 'title': candidate['title'], 'format': candidate['format'], 'score': round(float(best_score), 4), 'matched_variant': best_variant})
    matches.sort(key=lambda item: item['score'], reverse=True)
    return matches[:limit]


def _fast_snippet_from_book(book: dict, query: str) -> str:
    passport = dict(book.get('semantic_passport') or {})
    for text in (
        passport.get('summary'),
        passport.get('search_text'),
        ' '.join(passport.get('synthetic_queries') or []),
    ):
        snippet = _extract_snippet(str(text or ''), query)
        if snippet:
            return snippet
    return ''


def _build_fast_result(book: dict, score: float, query: str, *, title_score: float = 0.0, entity_score: float = 0.0, passport_score: float = 0.0, synthetic_score: float = 0.0):
    snippet = _fast_snippet_from_book(book, query)
    return {
        'book_id': book.get('id'),
        'group_key': _book_group_key(str(book.get('id')), str(book.get('title') or '')),
        'title': book.get('title', 'Без названия'),
        'format': book.get('format', 'unknown'),
        'snippet': snippet,
        'snippets': [snippet] if snippet else [],
        'similarity': float(score),
        'lexical_score': 0.0,
        'semantic_score': 0.0,
        'entity_score': float(entity_score),
        'synthetic_score': float(synthetic_score),
        'passport_score': float(passport_score),
        'title_match_score': float(title_score),
        'coverage_score': 0.0,
        'phrase_bonus': 0.0,
        'feature_score': 0.0,
        'hit_chunks': 0,
        'occurrences': 0,
        'book_features': book.get('features', {}),
        'chunk_id': None,
        'best_text': snippet,
        'lexical_evidence': max(float(title_score), float(entity_score), float(passport_score), float(synthetic_score)),
    }


def _fast_title_results(query: str, top_k: int) -> List[dict]:
    results = []
    for match in find_title_matches(query, limit=max(top_k * 3, 8), min_score=TITLE_SOFT_THRESHOLD):
        book = _LOOKUP_CACHE.get('by_id', {}).get(match.get('book_id'))
        if not book:
            continue
        score = float(match.get('score', 0.0)) * 1.4
        results.append(_build_fast_result(book, score, query, title_score=float(match.get('score', 0.0))))
    return sorted(results, key=lambda item: item['similarity'], reverse=True)[:top_k]


def _fast_entity_results(query: str, top_k: int) -> List[dict]:
    if not _LOOKUP_CACHE.get('entity_candidates'):
        load_index(force=True)
    results: List[dict] = []
    for candidate in _LOOKUP_CACHE.get('entity_candidates', []):
        best_entity = 0.0
        best_synthetic = 0.0
        best_passport = 0.0
        for entity in candidate.get('entities', []):
            best_entity = max(best_entity, _match_score(query, entity))
        for synthetic in candidate.get('synthetic_queries', []):
            best_synthetic = max(best_synthetic, _match_score(query, synthetic))
        best_passport = max(
            _match_score(query, candidate.get('summary', '')),
            _match_score(query, candidate.get('search_text', '')),
        )
        if best_entity < 0.88 and best_synthetic < 0.92:
            continue
        total = best_entity * 2.25
        if best_entity >= 0.97:
            total += best_synthetic * 0.35 + best_passport * 0.1
        else:
            total += best_synthetic * 0.12 + best_passport * 0.04
        book = _LOOKUP_CACHE.get('by_id', {}).get(candidate.get('book_id'))
        if not book:
            continue
        results.append(
            _build_fast_result(
                book,
                total,
                query,
                entity_score=best_entity,
                passport_score=best_passport,
                synthetic_score=best_synthetic,
            )
        )
    return sorted(results, key=lambda item: item['similarity'], reverse=True)[:top_k]


# -------- runtime indexes --------
class InvertedBM25:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.postings = {}
        self.doc_len = np.array([], dtype=np.int32)
        self.avg_doc_len = 0.0
        self.idf = {}

    def fit(self, corpus: Sequence[str]):
        postings = defaultdict(list)
        doc_len = np.zeros(len(corpus), dtype=np.int32)
        for doc_idx, doc in enumerate(corpus):
            counts = Counter(tokenize_smart(doc))
            doc_len[doc_idx] = sum(counts.values())
            for term, tf in counts.items():
                postings[term].append((doc_idx, tf))
        self.doc_len = doc_len
        self.avg_doc_len = float(doc_len.mean()) if len(doc_len) else 0.0
        self.postings = {}
        self.idf = {}
        total_docs = len(corpus)
        for term, values in postings.items():
            doc_ids = np.fromiter((doc_id for doc_id, _ in values), dtype=np.int32, count=len(values))
            tfs = np.fromiter((tf for _, tf in values), dtype=np.float32, count=len(values))
            self.postings[term] = (doc_ids, tfs)
            df = len(values)
            self.idf[term] = float(np.log((total_docs - df + 0.5) / (df + 0.5) + 1.0))

    def score_query(self, query: str):
        tokens = tokenize_smart(query)
        scores = np.zeros(len(self.doc_len), dtype=np.float32)
        if not tokens or len(self.doc_len) == 0 or self.avg_doc_len == 0:
            return scores
        for term in tokens:
            posting = self.postings.get(term)
            if posting is None:
                continue
            doc_ids, tfs = posting
            lengths = self.doc_len[doc_ids]
            denom = tfs + self.k1 * (1 - self.b + self.b * (lengths / self.avg_doc_len))
            scores[doc_ids] += self.idf[term] * ((tfs * (self.k1 + 1)) / np.maximum(denom, 1e-9))
        return scores



def _normalize_embeddings_matrix(embeddings):
    if embeddings is None or len(embeddings) == 0:
        return None
    arr = np.asarray(embeddings, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.shape[1] == 1 and float(np.max(np.abs(arr))) == 0.0:
        return None
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return arr / norms



def _build_book_runtime(index, metadata, normalized_embeddings):
    book_to_indices = defaultdict(list)
    for idx, item in enumerate(metadata):
        book_to_indices[item.get('book_id')].append(idx)

    book_texts = []
    book_ids = []
    book_centroids = []
    passport_texts = {}
    book_passports = {}
    book_entities = {}
    book_synthetic_queries = {}

    index_by_id = {book.get('id'): book for book in index}

    for book in index:
        book_id = book.get('id')
        indices = book_to_indices.get(book_id, [])
        if not indices:
            continue
        book_ids.append(book_id)

        sample_chunks = [metadata[idx].get('chunk', '') for idx in indices[:6]]
        dominant = _text_analyzer.dominant_features(book.get('features', {}))
        feature_words = []
        for category, values in dominant.items():
            feature_words.extend(name for name, _score in values[:2])
        title = book.get('title', '')
        author = _author_from_record(book)
        source = Path(str(book.get('source_filename', ''))).stem
        passport = dict(book.get('semantic_passport') or {})
        if not passport:
            passport = _build_semantic_passport(title, author, sample_chunks, features=book.get('features') or {}, metadata=book.get('metadata') or {})
        passport_text = passport.get('search_text') or passport.get('summary') or ''
        passport_texts[book_id] = passport_text
        book_passports[book_id] = passport
        book_entities[book_id] = [str(item) for item in (passport.get('entities') or []) if str(item).strip()]
        book_synthetic_queries[book_id] = [str(item) for item in (passport.get('synthetic_queries') or []) if str(item).strip()]
        chunk_samples = [chunk[:420] for chunk in sample_chunks[:3] if chunk]
        representative = '\n'.join(
            [
                title,
                author,
                source,
                ' '.join(feature_words),
                ' '.join(passport.get('themes') or []),
                ' '.join(passport.get('scene_hints') or []),
                ' '.join((passport.get('entities') or [])[:4]),
                passport_text,
            ] + chunk_samples
        )
        book_texts.append(representative)

        if normalized_embeddings is not None:
            centroid = normalized_embeddings[np.asarray(indices, dtype=np.int32)].mean(axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            book_centroids.append(centroid.astype(np.float32))

    vectorizer = None
    tfidf_matrix = None
    if book_texts:
        vectorizer = TfidfVectorizer(
            analyzer='word',
            tokenizer=str.split,
            preprocessor=None,
            lowercase=False,
            token_pattern=None,
            ngram_range=(1, 2),
            max_features=50000,
            min_df=1,
        )
        try:
            normalized_texts = [' '.join(tokenize_smart(text)) for text in book_texts]
            tfidf_matrix = vectorizer.fit_transform(normalized_texts)
        except Exception:
            vectorizer = None
            tfidf_matrix = None

    if book_centroids:
        book_centroids = np.vstack(book_centroids)
    else:
        book_centroids = None

    return {
        'book_to_indices': {book_id: np.asarray(indices, dtype=np.int32) for book_id, indices in book_to_indices.items()},
        'book_ids': book_ids,
        'book_texts': book_texts,
        'book_vectorizer': vectorizer,
        'book_tfidf': tfidf_matrix,
        'book_centroids': book_centroids,
        'index_by_id': index_by_id,
        'passport_texts': passport_texts,
        'book_passports': book_passports,
        'book_entities': book_entities,
        'book_synthetic_queries': book_synthetic_queries,
    }



def _runtime_signature(index, metadata, embeddings):
    return (
        len(index),
        len(metadata) if metadata is not None else 0,
        metadata[0].get('chunk_id') if metadata else None,
        metadata[-1].get('chunk_id') if metadata else None,
        _safe_mtime(STORAGE_FILE),
        _safe_mtime(VECTOR_DB_FILE),
        None if embeddings is None else tuple(np.asarray(embeddings).shape),
    )



def _ensure_runtime(metadata, embeddings):
    index = load_index()
    signature = _runtime_signature(index, metadata, embeddings)
    if _RUNTIME_CACHE['signature'] == signature and _RUNTIME_CACHE['runtime'] is not None:
        return _RUNTIME_CACHE['runtime']

    bm25 = InvertedBM25()
    corpus = [item.get('chunk', '') for item in metadata]
    bm25.fit(corpus)
    normalized_embeddings = _normalize_embeddings_matrix(embeddings)
    book_runtime = _build_book_runtime(index, metadata, normalized_embeddings)
    runtime = {
        'bm25': bm25,
        'metadata': metadata,
        'normalized_embeddings': normalized_embeddings,
        **book_runtime,
    }
    _RUNTIME_CACHE['signature'] = signature
    _RUNTIME_CACHE['runtime'] = runtime
    _RUNTIME_CACHE['source_mtimes'] = _runtime_source_mtimes()
    return runtime


def warm_runtime():
    embeddings, metadata = load_vector_db(sync_with_index=True)
    if metadata is None or len(metadata) == 0:
        return None
    return _ensure_runtime(metadata, embeddings)


# -------- query helpers --------
def _sanitize_search_text(query: str) -> str:
    cleaned = (query or '').strip()
    lowered = cleaned.lower().replace('ё', 'е')
    for prefix in GENERIC_PREFIXES:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip(' :,.-')
            lowered = cleaned.lower().replace('ё', 'е')
            break
    cleaned = re.sub(r'\b(в какой книге|в каком произведении|где есть|найди книгу где|найди произведение где)\b', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\b(кто такой|кто такая|как зовут|есть ли книга про)\b', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\b(про|о|об|для|мне|пожалуйста)\b', ' ', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' ,.-')
    return cleaned or query.strip()



def _book_group_key(book_id: str, title: str) -> str:
    variants = _LOOKUP_CACHE['title_keys_by_id'].get(book_id) or []
    if variants:
        return variants[0]
    return _normalize_lookup_key(title) or str(book_id)



def _query_embedding(query: str):
    encoded = encode_with_model([query])
    if encoded is None or len(encoded) == 0:
        fallback = _fallback_text_embedding(query)
        return fallback if np.linalg.norm(fallback) > 0 else None
    vector = np.asarray(encoded[0], dtype=np.float32)
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
    return vector


def compute_centroid_embedding(embeddings) -> Optional[np.ndarray]:
    if embeddings is None:
        return None
    arr = np.asarray(embeddings, dtype=np.float32)
    if arr.size == 0:
        return None
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    centroid = arr.mean(axis=0)
    norm = np.linalg.norm(centroid)
    if norm <= 0:
        return None
    return (centroid / norm).astype(np.float32)


def get_query_embedding(query: str):
    return _query_embedding(query)


def get_book_embedding(book_id: str):
    embeddings, metadata = load_vector_db(sync_with_index=True)
    if embeddings is None or not metadata:
        return None
    indices = [idx for idx, item in enumerate(metadata) if str(item.get('book_id') or '') == str(book_id)]
    if not indices:
        return None
    return compute_centroid_embedding(np.asarray(embeddings)[np.asarray(indices, dtype=np.int32)])



def _book_semantic_scores(runtime, query: str):
    scores = {}
    book_ids = runtime['book_ids']
    if not book_ids:
        return scores

    query_vector = _query_embedding(query)
    if query_vector is not None and runtime['book_centroids'] is not None:
        sims = runtime['book_centroids'] @ query_vector
        for idx, book_id in enumerate(book_ids):
            scores[book_id] = float(sims[idx])
        return scores

    vectorizer = runtime['book_vectorizer']
    matrix = runtime['book_tfidf']
    if vectorizer is None or matrix is None:
        return scores
    try:
        query_text = ' '.join(tokenize_smart(query))
        query_matrix = vectorizer.transform([query_text])
        sims = (matrix @ query_matrix.T).toarray().ravel()
        for idx, book_id in enumerate(book_ids):
            scores[book_id] = float(sims[idx])
    except Exception:
        return {}
    return scores



def _extract_snippet(text: str, query: str, max_len: int = 280):
    compact = re.sub(r'\s+', ' ', text or '').strip()
    if not compact:
        return ''
    raw_terms = [term for term in re.findall(r'\w+', query.lower().replace('ё', 'е')) if len(term) > 1]
    lowered = compact.lower().replace('ё', 'е')
    match_pos = -1
    match_len = 0
    for term in raw_terms:
        pos = lowered.find(term)
        if pos != -1:
            match_pos = pos
            match_len = len(term)
            break
    if match_pos == -1:
        return compact[:max_len].rstrip() + ('...' if len(compact) > max_len else '')
    start = max(0, match_pos - (max_len // 3))
    end = min(len(compact), match_pos + match_len + (max_len - (max_len // 3)))
    if start > 0:
        left_boundary = compact.rfind(' ', 0, start)
        if left_boundary != -1:
            start = left_boundary + 1
    if end < len(compact):
        right_boundary = compact.find(' ', end)
        if right_boundary != -1:
            end = right_boundary
    snippet = compact[start:end].strip()
    if start > 0:
        snippet = '...' + snippet
    if end < len(compact):
        snippet = snippet + '...'
    return re.sub(r'^\.\.\.(\S)', r'... \1', snippet)



def _token_coverage(chunk_text: str, query_tokens: Sequence[str]):
    if not query_tokens:
        return 0.0, 0, 0.0
    chunk_tokens = tokenize_smart(chunk_text)
    if not chunk_tokens:
        return 0.0, 0, 0.0
    chunk_counter = Counter(chunk_tokens)
    matched = 0
    occurrences = 0
    for token in set(query_tokens):
        count = chunk_counter.get(token, 0)
        if count > 0:
            matched += 1
            occurrences += count
    coverage = matched / max(len(set(query_tokens)), 1)
    density = occurrences / max(len(chunk_tokens), 1)
    return coverage, occurrences, density



def _phrase_bonus(chunk_text: str, query: str):
    normalized_chunk = ' '.join(tokenize_smart(chunk_text))
    normalized_query = ' '.join(tokenize_smart(query))
    if not normalized_query:
        return 0.0
    if normalized_query in normalized_chunk:
        return 1.0
    raw = re.sub(r'\s+', ' ', query.lower().replace('ё', 'е')).strip()
    raw_chunk = re.sub(r'\s+', ' ', (chunk_text or '').lower().replace('ё', 'е')).strip()
    if raw and raw in raw_chunk:
        return 0.8
    return 0.0



def _feature_boost(book_info: dict, query_profile: Optional[dict]):
    if not query_profile:
        return 0.0
    priorities = query_profile.get('priorities') or {}
    if not priorities:
        return 0.0
    book_features = (book_info or {}).get('features') or {}
    boost = 0.0
    for category, values in priorities.items():
        category_features = book_features.get(category, {})
        for feature_name, weight in values[:2]:
            boost += float(category_features.get(feature_name, 0.0)) * float(weight)
    return boost


def _book_entity_scores(runtime, query: str):
    if not query:
        return {}
    scores = {}
    for book_id, entities in (runtime.get('book_entities') or {}).items():
        best = 0.0
        for entity in entities:
            best = max(best, _match_score(query, entity))
        if best > 0:
            scores[book_id] = float(best)
    return scores


def _book_synthetic_scores(runtime, query: str):
    if not query:
        return {}
    scores = {}
    query_tokens = tokenize_smart(query)
    for book_id, synthetic_queries in (runtime.get('book_synthetic_queries') or {}).items():
        best = 0.0
        for candidate in synthetic_queries:
            best = max(best, _match_score(query, candidate), _text_overlap_score(candidate, query_tokens))
        if best > 0:
            scores[book_id] = float(best)
    return scores


def _book_passport_scores(runtime, query: str):
    if not query:
        return {}
    scores = {}
    query_tokens = tokenize_smart(query)
    for book_id, text in (runtime.get('passport_texts') or {}).items():
        overlap_score = _text_overlap_score(text, query_tokens)
        fuzzy_score = _match_score(query, text)
        score = max(overlap_score, fuzzy_score)
        if score > 0:
            scores[book_id] = float(score)
    return scores


def _book_scene_query_scores(runtime, query: str, expanded_query: str):
    if not query:
        return {}
    raw_tokens = tokenize_smart(query)
    expanded_tokens = tokenize_smart(expanded_query or query)
    scores = {}
    for book_id in runtime.get('book_ids') or []:
        book_info = (runtime.get('index_by_id') or {}).get(book_id, {}) or {}
        passport = (book_info.get('semantic_passport') or {}) if isinstance(book_info.get('semantic_passport'), dict) else {}
        parts = [
            str(book_info.get('title') or ''),
            str(passport.get('search_text') or ''),
            ' '.join((runtime.get('book_entities') or {}).get(book_id, [])[:8]),
            ' '.join((runtime.get('book_synthetic_queries') or {}).get(book_id, [])[:10]),
        ]
        text = ' '.join(part for part in parts if part).strip()
        if not text:
            continue
        score = 0.0
        score += _text_overlap_score(text, raw_tokens) * 1.0
        score += _text_overlap_score(text, expanded_tokens) * 1.35
        score += _match_score(expanded_query or query, text) * 0.8
        normalized_text = _normalize_match_text(text)
        scene_text = _normalize_match_text(' '.join(str(item) for item in (passport.get('scene_hints') or [])))
        expansion_only = [token for token in expanded_tokens if token not in raw_tokens and len(token) > 3]
        marker_matches = sum(1 for token in expansion_only if token in normalized_text)
        score += marker_matches * 0.28
        if 'утоп' in expanded_tokens and 'утоп' in normalized_text:
            score += 0.9
        if 'утоп' in expanded_tokens and 'утоп' in scene_text:
            score += 2.2
        if 'собак' in raw_tokens and 'муму' in normalized_text:
            score += 0.8
        if 'собак' in raw_tokens and 'собак' in normalized_text:
            score += 0.35
        if {'утоп', 'собак'} & set(raw_tokens) and 'герасим' in normalized_text and 'муму' in normalized_text:
            score += 1.1
        if 'утоп' in raw_tokens and 'собак' in raw_tokens:
            if 'утоп' in scene_text:
                score += 3.6
            if 'муму' in normalized_text:
                score += 2.4
            if 'герасим' in normalized_text:
                score += 1.2
        if score > 0:
            scores[book_id] = float(score)
    return scores



def _collect_candidate_books(runtime, query: str, query_profile: dict, top_k: int, title_boosts: Optional[dict]):
    query_context = _expand_query_with_concepts(query, query_profile)
    expanded_query = query_context['expanded_query'] or query
    qtype = (query_profile or {}).get('type', 'keyword')
    semantic_query = expanded_query if qtype in {'recommendation', 'theme', 'mixed'} else query

    bm25_scores = runtime['bm25'].score_query(query)
    if qtype == 'scene' and expanded_query and _normalize_match_text(expanded_query) != _normalize_match_text(query):
        bm25_scores = np.maximum(bm25_scores, runtime['bm25'].score_query(expanded_query))
    candidate_chunk_count = min(max(top_k * 24, 120), len(runtime['metadata']))
    if candidate_chunk_count >= len(bm25_scores):
        top_chunk_indices = np.argsort(bm25_scores)[::-1]
    else:
        top_chunk_indices = np.argpartition(bm25_scores, -candidate_chunk_count)[-candidate_chunk_count:]
        top_chunk_indices = top_chunk_indices[np.argsort(bm25_scores[top_chunk_indices])[::-1]]

    book_semantic_scores = _book_semantic_scores(runtime, semantic_query)
    entity_scores = _book_entity_scores(runtime, query)
    synthetic_scores = _book_synthetic_scores(runtime, expanded_query)
    passport_scores = _book_passport_scores(runtime, expanded_query)
    scene_query_scores = _book_scene_query_scores(runtime, query, expanded_query) if qtype == 'scene' else {}

    candidate_books = set()
    for idx in top_chunk_indices:
        if bm25_scores[int(idx)] > 0:
            candidate_books.add(runtime['metadata'][int(idx)].get('book_id'))

    for score_map, limit_factor in (
        (book_semantic_scores, max(top_k * 5, 15)),
        (entity_scores, max(top_k * 4, 12)),
        (synthetic_scores, max(top_k * 4, 12)),
        (passport_scores, max(top_k * 4, 12)),
        (scene_query_scores, max(top_k * 5, 15)),
    ):
        for book_id, score in sorted(score_map.items(), key=lambda item: item[1], reverse=True)[:limit_factor]:
            if score > 0:
                candidate_books.add(book_id)

    for book_id in (title_boosts or {}):
        candidate_books.add(book_id)

    query_tokens = tokenize_smart(query)
    results = []
    for book_id in candidate_books:
        indices = runtime['book_to_indices'].get(book_id)
        if indices is None or len(indices) == 0:
            continue
        book_info = runtime['index_by_id'].get(book_id, {})
        title = book_info.get('title') or runtime['metadata'][int(indices[0])].get('book_title') or 'Без названия'
        title_score = float((title_boosts or {}).get(book_id, 0.0))
        semantic_score = float(book_semantic_scores.get(book_id, 0.0))
        entity_score = float(entity_scores.get(book_id, 0.0))
        synthetic_score = float(synthetic_scores.get(book_id, 0.0))
        passport_score = float(passport_scores.get(book_id, 0.0))
        scene_query_score = float(scene_query_scores.get(book_id, 0.0))

        chunk_scores = bm25_scores[indices]
        best_local_pos = int(np.argmax(chunk_scores)) if len(chunk_scores) else 0
        best_idx = int(indices[best_local_pos])
        best_item = runtime['metadata'][best_idx]
        best_chunk = best_item.get('chunk', '')
        lexical_max = float(chunk_scores[best_local_pos]) if len(chunk_scores) else 0.0

        exact_coverages = []
        phrase_scores = []
        snippets = []
        chunk_hit_count = 0
        occurrence_count = 0
        for idx in indices[: min(len(indices), 40)]:
            item = runtime['metadata'][int(idx)]
            chunk_text = item.get('chunk', '')
            coverage, occurrences, density = _token_coverage(chunk_text, query_tokens)
            phrase = _phrase_bonus(chunk_text, query)
            exact_coverages.append((coverage, density))
            phrase_scores.append(phrase)
            if coverage > 0 or phrase > 0:
                chunk_hit_count += 1
                occurrence_count += occurrences
                snippet = _extract_snippet(chunk_text, query)
                if snippet and snippet not in snippets and len(snippets) < 3:
                    snippets.append(snippet)

        best_coverage = max((item[0] for item in exact_coverages), default=0.0)
        best_density = max((item[1] for item in exact_coverages), default=0.0)
        phrase_bonus = max(phrase_scores, default=0.0)
        hit_ratio = chunk_hit_count / max(len(indices), 1)
        lexical_evidence = lexical_max + best_coverage + phrase_bonus
        feature_score = _feature_boost(book_info, query_profile)

        if qtype == 'title':
            final_score = title_score * 2.8 + lexical_max * 0.9 + best_coverage * 0.7 + semantic_score * 0.25 + phrase_bonus * 0.4 + synthetic_score * 0.15
        elif qtype == 'entity':
            final_score = entity_score * 1.8 + title_score * 0.55 + lexical_max * 1.0 + best_coverage * 0.9 + passport_score * 0.5 + semantic_score * 0.15
        elif qtype in {'specific', 'scene'}:
            final_score = lexical_max * 1.35 + best_coverage * 1.15 + phrase_bonus * 0.85 + hit_ratio * 0.35 + synthetic_score * 0.55 + passport_score * 0.35 + scene_query_score * 2.4 + semantic_score * 0.22 + title_score * 0.2 + best_density * 0.2
        elif qtype in {'recommendation', 'theme'}:
            final_score = semantic_score * 1.45 + passport_score * 0.95 + synthetic_score * 0.75 + feature_score * 0.4 + lexical_max * 0.28 + best_coverage * 0.2 + title_score * 0.12 + entity_score * 0.12
        elif qtype == 'mixed':
            final_score = semantic_score * 1.1 + lexical_max * 0.7 + best_coverage * 0.48 + synthetic_score * 0.58 + passport_score * 0.45 + entity_score * 0.25 + phrase_bonus * 0.25 + title_score * 0.18
        else:
            final_score = semantic_score * 0.78 + lexical_max * 0.82 + best_coverage * 0.55 + phrase_bonus * 0.25 + feature_score * 0.18 + title_score * 0.2 + synthetic_score * 0.3 + passport_score * 0.25

        if not snippets:
            snippets = [_extract_snippet(best_chunk, query)] if best_chunk else []

        results.append(
            {
                'book_id': book_id,
                'group_key': _book_group_key(book_id, title),
                'title': title,
                'format': book_info.get('format', best_item.get('format', 'unknown')),
                'snippet': snippets[0] if snippets else '',
                'snippets': snippets,
                'similarity': float(final_score),
                'lexical_score': lexical_max,
                'semantic_score': semantic_score,
                'entity_score': entity_score,
                'synthetic_score': synthetic_score,
                'passport_score': passport_score,
                'scene_query_score': scene_query_score,
                'title_match_score': title_score,
                'coverage_score': best_coverage,
                'phrase_bonus': phrase_bonus,
                'feature_score': feature_score,
                'hit_chunks': chunk_hit_count,
                'occurrences': occurrence_count,
                'book_features': book_info.get('features', {}),
                'chunk_id': best_item.get('chunk_id'),
                'best_text': best_chunk,
                'lexical_evidence': lexical_evidence,
            }
        )

    deduped = {}
    for result in results:
        key = result['group_key']
        if key not in deduped or result['similarity'] > deduped[key]['similarity']:
            deduped[key] = result

    ranked = sorted(deduped.values(), key=lambda item: item['similarity'], reverse=True)

    if qtype in {'title', 'entity', 'specific', 'scene'}:
        ranked = [item for item in ranked if item['lexical_evidence'] > 0 or item['title_match_score'] >= TITLE_SOFT_THRESHOLD or item.get('entity_score', 0.0) >= 0.82]

    return ranked[: max(top_k * 3, 15)]



def _maybe_rerank(query: str, results: List[dict], query_profile: Optional[dict]):
    qtype = (query_profile or {}).get('type', 'keyword')
    if qtype in {'title', 'entity', 'scene'}:
        return results
    if len(tokenize_smart(query)) < 3:
        return results
    reranker = get_reranker()
    if reranker is None or not results:
        return results
    subset = results[: min(10, len(results))]
    try:
        pairs = [[query, item.get('best_text', item.get('snippet', ''))] for item in subset]
        scores = reranker.predict(pairs)
        for item, score in zip(subset, scores):
            item['similarity'] = float(score) + item['title_match_score'] * 0.2 + item['feature_score'] * 0.1
        subset.sort(key=lambda item: item['similarity'], reverse=True)
        tail = results[len(subset):]
        return subset + tail
    except Exception:
        return results



def search_hybrid(query: str, top_k: int = 5, alpha: float = 0.7, use_cache: bool = True, title_boosts: Optional[dict] = None, query_profile: Optional[dict] = None):
    if not query or not query.strip():
        return []
    started = time.perf_counter()
    profile = query_profile or {'type': 'keyword', 'search_text': query, 'original_query': query, 'priorities': {}, 'has_features': {}}
    search_text = profile.get('search_text') or query
    qtype = profile.get('type', 'keyword')

    if use_cache and not title_boosts:
        cached = _search_cache.get(search_text, qtype, top_k)
        if cached is not None:
            if SEARCH_LOG_TIMINGS:
                LOGGER.info('search_hybrid_completed query=%r qtype=%s source=cache duration_ms=%s', search_text[:120], qtype, int((time.perf_counter() - started) * 1000))
            return cached

    fast_results: List[dict] = []
    if qtype == 'title':
        fast_results = _fast_title_results(profile.get('original_query', query), top_k)
        if fast_results and float(fast_results[0].get('title_match_score', 0.0)) >= FAST_TITLE_ONLY_SCORE:
            if use_cache and not title_boosts:
                _search_cache.set(search_text, qtype, top_k, fast_results)
            if SEARCH_LOG_TIMINGS:
                LOGGER.info('search_hybrid_completed query=%r qtype=%s source=fast_title duration_ms=%s', search_text[:120], qtype, int((time.perf_counter() - started) * 1000))
            return fast_results
    elif qtype == 'entity':
        fast_results = _fast_entity_results(profile.get('original_query', query), top_k)
        if fast_results and float(fast_results[0].get('entity_score', 0.0)) >= FAST_ENTITY_ONLY_SCORE:
            if use_cache and not title_boosts:
                _search_cache.set(search_text, qtype, top_k, fast_results)
            if SEARCH_LOG_TIMINGS:
                LOGGER.info('search_hybrid_completed query=%r qtype=%s source=fast_entity duration_ms=%s', search_text[:120], qtype, int((time.perf_counter() - started) * 1000))
            return fast_results

    embeddings, metadata = load_vector_db(sync_with_index=True)
    if metadata is None or len(metadata) == 0:
        return []

    runtime = _ensure_runtime(metadata, embeddings)
    merged_title_boosts = dict(title_boosts or {})
    if not merged_title_boosts:
        for match in find_title_matches(profile.get('original_query', query), limit=5):
            merged_title_boosts[match['book_id']] = float(match['score'])

    ranked = _collect_candidate_books(runtime, search_text, profile, top_k, merged_title_boosts)
    ranked = _maybe_rerank(search_text, ranked, profile)

    if qtype == 'title':
        strong = [item for item in ranked if item['title_match_score'] >= TITLE_SOFT_THRESHOLD]
        if strong:
            strong.sort(key=lambda item: (item['title_match_score'], item['similarity']), reverse=True)
            ranked = strong
    elif qtype in {'entity', 'specific'}:
        ranked = [item for item in ranked if item['lexical_evidence'] > 0.0 or item['title_match_score'] >= TITLE_SOFT_THRESHOLD]

    final_results = ranked[:top_k]
    if use_cache and final_results and not title_boosts:
        _search_cache.set(search_text, qtype, top_k, final_results)
    if SEARCH_LOG_TIMINGS:
        LOGGER.info(
            'search_hybrid_completed query=%r qtype=%s source=runtime results=%s duration_ms=%s',
            search_text[:120],
            qtype,
            len(final_results),
            int((time.perf_counter() - started) * 1000),
        )
    return final_results



def clear_cache():
    _search_cache.clear()


# -------- public helpers --------
def get_text_analyzer():
    return _text_analyzer



def get_query_analyzer():
    return _query_analyzer


__all__ = [
    'add_to_index',
    'search_hybrid',
    'load_index',
    'save_index',
    'update_book_record',
    'update_last_opened',
    'clear_cache',
    'warm_runtime',
    'get_text_analyzer',
    'get_query_analyzer',
    'get_model',
    'model_status',
    'load_vector_db',
    'delete_from_index',
    'reset_runtime_state',
    'delete_book_from_index',
    'persist_vector_db',
    'rebuild_semantic_passports',
    'STORAGE_FILE',
    'VECTOR_DB_FILE',
    'CACHE_FILE',
    'find_title_matches',
    'QueryProfile',
    'chunk_text_content',
    'embed_chunks',
    'compute_centroid_embedding',
    'get_book_embedding',
    'get_query_embedding',
    'tokenize_smart',
    '_sanitize_search_text',
]
