import hashlib
import json
import os
import pickle
import re
import time
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from runtime_config import get_compute_profile

BASE_DIR = Path(__file__).resolve().parent
STORAGE_FILE = str(BASE_DIR / 'storage.json')
VECTOR_DB_FILE = str(BASE_DIR / 'vector_db.npz')
CACHE_FILE = str(BASE_DIR / 'search_cache.pkl')
FINE_TUNED_MODEL_PATH = str(BASE_DIR / 'fine_tuned_literary_model')

SentenceTransformer = None
CrossEncoder = None
_SENTENCE_TRANSFORMERS_ERROR = None


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

_INDEX_CACHE = {'mtime': None, 'data': []}
_VECTOR_CACHE = {'mtime': None, 'embeddings': None, 'metadata': None}
_RUNTIME_CACHE = {'signature': None, 'runtime': None}
_LOOKUP_CACHE = {'by_id': {}, 'title_candidates': [], 'title_keys_by_id': {}}


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
            with open(self.cache_file, 'wb') as file_obj:
                pickle.dump(self.cache, file_obj)
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
        model_paths = [FINE_TUNED_MODEL_PATH, 'paraphrase-multilingual-MiniLM-L12-v2']
        errors = []
        for model_path in model_paths:
            if model_path == FINE_TUNED_MODEL_PATH and not os.path.exists(model_path):
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
                if model_path == FINE_TUNED_MODEL_PATH and not os.path.exists(model_path):
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
    _RUNTIME_CACHE.update({'signature': None, 'runtime': None})
    _VECTOR_CACHE.update({'mtime': None, 'embeddings': None, 'metadata': None})



def encode_with_model(texts: Sequence[str]):
    model = get_model()
    if model is None:
        return None
    try:
        batch_size = max(1, int(get_compute_profile()['embedding_batch_size']))
        embeddings = model.encode(list(texts), show_progress_bar=False, batch_size=batch_size)
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
    with open(STORAGE_FILE, 'w', encoding='utf-8') as file_obj:
        json.dump(index, file_obj, ensure_ascii=False, indent=2)
    _INDEX_CACHE['mtime'] = _safe_mtime(STORAGE_FILE)
    _INDEX_CACHE['data'] = index
    _rebuild_lookup_cache(index)
    _RUNTIME_CACHE.update({'signature': None, 'runtime': None})



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



def split_text_semantic(text: str, model=None, threshold: float = 0.3, batch_size: Optional[int] = None):
    # Сохраняем старое имя функции, но используем более стабильный paragraph-aware chunking.
    cleaned = _clean_text(text)
    if not cleaned:
        return [], np.zeros((0, 1), dtype=np.float32)

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

    if not chunks:
        chunks = [cleaned]

    embeddings = encode_with_model(chunks)
    if embeddings is None:
        embeddings = np.zeros((len(chunks), 1), dtype=np.float32)
    return chunks, embeddings


# -------- vector DB --------
def load_vector_db(sync_with_index: bool = True):
    mtime = _safe_mtime(VECTOR_DB_FILE)
    if _VECTOR_CACHE['mtime'] == mtime:
        embeddings = _VECTOR_CACHE['embeddings']
        metadata = _VECTOR_CACHE['metadata']
    else:
        if not os.path.exists(VECTOR_DB_FILE):
            _VECTOR_CACHE.update({'mtime': mtime, 'embeddings': None, 'metadata': None})
            return None, None
        try:
            data = np.load(VECTOR_DB_FILE, allow_pickle=True)
            embeddings = np.asarray(data['embeddings'], dtype=np.float32) if 'embeddings' in data else None
            raw_meta = data['metadata'] if 'metadata' in data else None
            if raw_meta is None:
                metadata = None
            else:
                meta_str = raw_meta.item() if getattr(raw_meta, 'size', 0) > 0 else '[]'
                metadata = json.loads(meta_str) if meta_str else []
            if embeddings is not None and metadata is not None and len(embeddings) != len(metadata):
                size = min(len(embeddings), len(metadata))
                embeddings = embeddings[:size]
                metadata = metadata[:size]
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
        if os.path.exists(VECTOR_DB_FILE):
            os.remove(VECTOR_DB_FILE)
        _VECTOR_CACHE.update({'mtime': None, 'embeddings': None, 'metadata': None})
        _RUNTIME_CACHE.update({'signature': None, 'runtime': None})
        return
    np.savez(VECTOR_DB_FILE, embeddings=np.asarray(embeddings, dtype=np.float32), metadata=json.dumps(metadata, ensure_ascii=False))
    _VECTOR_CACHE.update({'mtime': _safe_mtime(VECTOR_DB_FILE), 'embeddings': np.asarray(embeddings, dtype=np.float32), 'metadata': metadata})
    _RUNTIME_CACHE.update({'signature': None, 'runtime': None})


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



def add_to_index(book_data: dict, file_id: str):
    title = book_data.get('title') or 'Без названия'
    content = book_data.get('content') or ''
    if not content.strip():
        return None

    old_embeddings, old_metadata = load_vector_db(sync_with_index=False)
    index = [book for book in load_index(force=True) if book.get('id') != file_id]
    if old_metadata is not None and len(old_metadata) > 0:
        old_embeddings, old_metadata = _filter_book_from_vector_db(file_id, old_embeddings, old_metadata)

    chunks, chunk_embeddings = split_text_semantic(content, get_model())
    if not chunks:
        return None

    features = _text_analyzer.analyze_book(chunks)
    title_variants = _title_variants_from_record(book_data)

    new_metadata = []
    for idx, chunk in enumerate(chunks):
        new_metadata.append(
            {
                'book_id': file_id,
                'book_title': title,
                'format': book_data.get('format', 'unknown'),
                'chunk': chunk,
                'chunk_id': f'{file_id}_{idx}',
            }
        )

    if old_embeddings is not None and old_metadata is not None and len(old_metadata) > 0:
        if old_embeddings.ndim == 1:
            old_embeddings = old_embeddings.reshape(-1, 1)
        if chunk_embeddings.ndim == 1:
            chunk_embeddings = chunk_embeddings.reshape(-1, 1)
        if old_embeddings.shape[1] != chunk_embeddings.shape[1]:
            if old_embeddings.shape[1] == 1:
                old_embeddings = np.zeros((len(old_embeddings), chunk_embeddings.shape[1]), dtype=np.float32)
            elif chunk_embeddings.shape[1] == 1:
                chunk_embeddings = np.zeros((len(chunk_embeddings), old_embeddings.shape[1]), dtype=np.float32)
            else:
                max_dim = max(old_embeddings.shape[1], chunk_embeddings.shape[1])
                old_fixed = np.zeros((len(old_embeddings), max_dim), dtype=np.float32)
                new_fixed = np.zeros((len(chunk_embeddings), max_dim), dtype=np.float32)
                old_fixed[:, : old_embeddings.shape[1]] = old_embeddings
                new_fixed[:, : chunk_embeddings.shape[1]] = chunk_embeddings
                old_embeddings, chunk_embeddings = old_fixed, new_fixed
        combined_embeddings = np.vstack([old_embeddings, chunk_embeddings])
        combined_metadata = old_metadata + new_metadata
    else:
        combined_embeddings = chunk_embeddings
        combined_metadata = new_metadata

    persist_vector_db(combined_embeddings, combined_metadata)

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

    embeddings, metadata = load_vector_db(sync_with_index=False)
    removed_chunks = 0
    if embeddings is not None and metadata:
        removed_chunks = sum(1 for item in metadata if item.get('book_id') == book_id)
        filtered_embeddings, filtered_metadata = _filter_book_from_vector_db(book_id, embeddings, metadata)
        persist_vector_db(filtered_embeddings, filtered_metadata)

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
    for book in index:
        variants = _title_lookup_variants(book)
        title_keys_by_id[book.get('id')] = variants
        title_candidates.append({'book_id': book.get('id'), 'title': book.get('title', 'Без названия'), 'format': book.get('format', 'unknown'), 'variants': variants})
    _LOOKUP_CACHE['by_id'] = by_id
    _LOOKUP_CACHE['title_candidates'] = title_candidates
    _LOOKUP_CACHE['title_keys_by_id'] = title_keys_by_id



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
    title_scores = {}

    index_by_id = {book.get('id'): book for book in index}

    for book in index:
        book_id = book.get('id')
        indices = book_to_indices.get(book_id, [])
        if not indices:
            continue
        book_ids.append(book_id)

        sample_chunks = [metadata[idx].get('chunk', '') for idx in indices[:10]]
        dominant = _text_analyzer.dominant_features(book.get('features', {}))
        feature_words = []
        for category, values in dominant.items():
            feature_words.extend(name for name, _score in values[:2])
        title = book.get('title', '')
        source = Path(str(book.get('source_filename', ''))).stem
        representative = '\n'.join([title, source, ' '.join(feature_words)] + sample_chunks)
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
    return runtime


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
        return None
    vector = np.asarray(encoded[0], dtype=np.float32)
    norm = np.linalg.norm(vector)
    if norm > 0:
        vector = vector / norm
    return vector



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
    snippet = compact[start:end].strip()
    if start > 0:
        snippet = '...' + snippet
    if end < len(compact):
        snippet = snippet + '...'
    return snippet



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



def _collect_candidate_books(runtime, query: str, query_profile: dict, top_k: int, title_boosts: Optional[dict]):
    bm25_scores = runtime['bm25'].score_query(query)
    candidate_chunk_count = min(max(top_k * 24, 120), len(runtime['metadata']))
    if candidate_chunk_count >= len(bm25_scores):
        top_chunk_indices = np.argsort(bm25_scores)[::-1]
    else:
        top_chunk_indices = np.argpartition(bm25_scores, -candidate_chunk_count)[-candidate_chunk_count:]
        top_chunk_indices = top_chunk_indices[np.argsort(bm25_scores[top_chunk_indices])[::-1]]

    book_semantic_scores = _book_semantic_scores(runtime, query)
    candidate_books = set()
    for idx in top_chunk_indices:
        if bm25_scores[int(idx)] > 0:
            candidate_books.add(runtime['metadata'][int(idx)].get('book_id'))

    for book_id, score in sorted(book_semantic_scores.items(), key=lambda item: item[1], reverse=True)[: max(top_k * 5, 15)]:
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

        qtype = (query_profile or {}).get('type', 'keyword')
        if qtype == 'title':
            final_score = title_score * 2.8 + lexical_max * 0.9 + best_coverage * 0.7 + semantic_score * 0.25 + phrase_bonus * 0.4
        elif qtype in {'entity', 'specific'}:
            final_score = lexical_max * 1.25 + best_coverage * 1.1 + phrase_bonus * 0.8 + hit_ratio * 0.35 + semantic_score * 0.18 + title_score * 0.45 + best_density * 0.2
        elif qtype == 'recommendation':
            final_score = semantic_score * 1.55 + lexical_max * 0.35 + best_coverage * 0.2 + feature_score * 0.45 + title_score * 0.12
        else:
            final_score = semantic_score * 0.75 + lexical_max * 0.85 + best_coverage * 0.55 + phrase_bonus * 0.25 + feature_score * 0.18 + title_score * 0.2

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

    qtype = (query_profile or {}).get('type', 'keyword')
    if qtype in {'title', 'entity', 'specific'}:
        ranked = [item for item in ranked if item['lexical_evidence'] > 0 or item['title_match_score'] >= TITLE_SOFT_THRESHOLD]

    return ranked[: max(top_k * 3, 15)]



def _maybe_rerank(query: str, results: List[dict], query_profile: Optional[dict]):
    qtype = (query_profile or {}).get('type', 'keyword')
    if qtype in {'title', 'entity', 'specific'}:
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
    profile = query_profile or {'type': 'keyword', 'search_text': query, 'original_query': query, 'priorities': {}, 'has_features': {}}
    search_text = profile.get('search_text') or query
    qtype = profile.get('type', 'keyword')

    if use_cache and not title_boosts:
        cached = _search_cache.get(search_text, qtype, top_k)
        if cached is not None:
            return cached

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
    'update_last_opened',
    'clear_cache',
    'get_text_analyzer',
    'get_query_analyzer',
    'get_model',
    'model_status',
    'load_vector_db',
    'delete_from_index',
    'reset_runtime_state',
    'delete_book_from_index',
    'persist_vector_db',
    'STORAGE_FILE',
    'VECTOR_DB_FILE',
    'CACHE_FILE',
    'find_title_matches',
    'QueryProfile',
    'tokenize_smart',
    '_sanitize_search_text',
]
