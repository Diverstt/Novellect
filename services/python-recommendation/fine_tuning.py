import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from runtime_config import get_compute_profile
from search_engine import (
    FINE_TUNED_MODEL_PATH,
    clear_cache,
    get_text_analyzer,
    load_index,
    load_vector_db,
    reset_runtime_state,
    tokenize_smart,
)

BASE_DIR = Path(__file__).resolve().parent
ADAPTATION_MANIFEST_PATH = BASE_DIR / 'fine_tuned_literary_model_manifest.json'
BASE_EMBEDDING_MODEL = 'paraphrase-multilingual-MiniLM-L12-v2'
SUPPORTED_ADAPTATION_STRATEGIES = ('full', 'lora')


def _safe_remove_path(path):
    path_obj = Path(path)
    if not path_obj.exists():
        return
    if path_obj.is_dir():
        shutil.rmtree(path_obj, ignore_errors=True)
    else:
        try:
            path_obj.unlink()
        except OSError:
            pass


def _book_chunks(metadata) -> Dict[str, List[str]]:
    grouped = defaultdict(list)
    for item in metadata or []:
        book_id = item.get('book_id')
        chunk = item.get('chunk')
        if book_id and chunk:
            grouped[book_id].append(chunk)
    return grouped


def _dominant_terms(text: str, limit: int = 5) -> List[str]:
    counts = Counter(tokenize_smart(text))
    terms = []
    for term, _count in counts.most_common(limit * 3):
        if len(term) < 4:
            continue
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _feature_words(book: dict) -> List[str]:
    analyzer = get_text_analyzer()
    dominant = analyzer.dominant_features(book.get('features', {}) or {}, threshold=0.1)
    words = []
    for _category, values in dominant.items():
        for name, _score in values[:2]:
            if name not in words:
                words.append(name)
    return words[:4]


def build_corpus_adaptation_pairs(max_pairs: int = 128) -> List[Tuple[str, str]]:
    index = load_index(force=True)
    _embeddings, metadata = load_vector_db(sync_with_index=True)
    grouped_chunks = _book_chunks(metadata)
    pairs: List[Tuple[str, str]] = []
    seen = set()

    for book in index:
        book_id = book.get('id')
        title = (book.get('title') or '').strip()
        chunks = grouped_chunks.get(book_id, [])[:3]
        if not title or not chunks:
            continue
        features = _feature_words(book)

        for chunk in chunks:
            if len((chunk or '').strip()) < 80:
                continue
            keywords = _dominant_terms(chunk, limit=5)
            candidates = [
                title,
                f"{title} {' '.join(keywords[:2])}".strip(),
                f"книга про {' '.join(keywords[:3])}".strip(),
                f"{' '.join(features[:2])} {' '.join(keywords[:2])}".strip(),
            ]
            for query in candidates:
                normalized_key = (query.strip().lower(), chunk[:180].strip().lower())
                if not query.strip() or normalized_key in seen:
                    continue
                seen.add(normalized_key)
                pairs.append((query.strip(), chunk.strip()))
                if len(pairs) >= max_pairs:
                    return pairs
    return pairs


def preview_corpus_adaptation(max_pairs: int = 24) -> dict:
    pairs = build_corpus_adaptation_pairs(max_pairs=max_pairs)
    return {
        'total_pairs': len(pairs),
        'preview': [{'query': query, 'chunk_preview': chunk[:180].replace('\n', ' ')} for query, chunk in pairs[:5]],
        'supported_strategies': list(SUPPORTED_ADAPTATION_STRATEGIES),
    }


def adaptation_status() -> dict:
    model_path = Path(FINE_TUNED_MODEL_PATH)
    manifest = None
    if ADAPTATION_MANIFEST_PATH.exists():
        try:
            with open(ADAPTATION_MANIFEST_PATH, 'r', encoding='utf-8') as file_obj:
                manifest = json.load(file_obj)
        except Exception:
            manifest = None
    return {
        'available': model_path.exists(),
        'model_path': str(model_path),
        'manifest_path': str(ADAPTATION_MANIFEST_PATH),
        'manifest': manifest,
        'strategy': (manifest or {}).get('strategy', 'full'),
    }


def _normalize_strategy(strategy: str) -> str:
    normalized = str(strategy or 'full').strip().lower()
    return normalized if normalized in SUPPORTED_ADAPTATION_STRATEGIES else 'full'


def _build_manifest(sample_count: int, epochs: int, batch_size: int, device: str, strategy: str, extra: dict | None = None) -> dict:
    manifest = {
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'sample_count': int(sample_count),
        'epochs': int(epochs),
        'batch_size': int(batch_size),
        'base_model': BASE_EMBEDDING_MODEL,
        'device': device,
        'strategy': strategy,
        'preview_queries': [query for query, _chunk in build_corpus_adaptation_pairs(max_pairs=min(10, sample_count))[:10]],
    }
    if extra:
        manifest.update(extra)
    return manifest


def _persist_manifest(manifest: dict):
    with open(ADAPTATION_MANIFEST_PATH, 'w', encoding='utf-8') as file_obj:
        json.dump(manifest, file_obj, ensure_ascii=False, indent=2)


def _activate_trained_model(temp_output: Path, model_path: Path):
    backup_output = BASE_DIR / 'fine_tuned_literary_model_backup'
    if model_path.exists():
        _safe_remove_path(backup_output)
        shutil.copytree(model_path, backup_output)
        _safe_remove_path(model_path)
    shutil.move(str(temp_output), str(model_path))
    reset_runtime_state()
    clear_cache()


def _train_full_model(pairs: List[Tuple[str, str]], epochs: int, batch_size: int, device: str, model_path: Path, temp_output: Path) -> dict:
    from sentence_transformers import InputExample, SentenceTransformer, losses
    from torch.utils.data import DataLoader

    examples = [InputExample(texts=[query, chunk]) for query, chunk in pairs]
    dataloader = DataLoader(examples, shuffle=True, batch_size=max(2, min(batch_size, len(examples))))

    model = SentenceTransformer(BASE_EMBEDDING_MODEL, device=device)
    train_loss = losses.MultipleNegativesRankingLoss(model)
    warmup_steps = max(1, len(dataloader) // 10)
    model.fit(
        train_objectives=[(dataloader, train_loss)],
        epochs=max(1, int(epochs)),
        warmup_steps=warmup_steps,
        output_path=str(temp_output),
        show_progress_bar=False,
    )
    _activate_trained_model(temp_output, model_path)
    manifest = _build_manifest(len(pairs), epochs, batch_size, device, 'full')
    _persist_manifest(manifest)
    return {
        'status': 'trained',
        'message': 'Адаптированная embedding-модель сохранена после полного fine-tuning.',
        'sample_count': len(pairs),
        'manifest': manifest,
    }


def _train_lora_model(pairs: List[Tuple[str, str]], epochs: int, batch_size: int, device: str, model_path: Path, temp_output: Path) -> dict:
    from sentence_transformers import InputExample, SentenceTransformer, losses
    from torch.utils.data import DataLoader
    from peft import LoraConfig, TaskType, get_peft_model

    examples = [InputExample(texts=[query, chunk]) for query, chunk in pairs]
    dataloader = DataLoader(examples, shuffle=True, batch_size=max(2, min(batch_size, len(examples))))

    model = SentenceTransformer(BASE_EMBEDDING_MODEL, device=device)
    transformer_module = model._first_module()
    auto_model = getattr(transformer_module, 'auto_model', None)
    if auto_model is None:
        raise RuntimeError('Не удалось получить базовый transformer для LoRA-адаптации.')

    target_modules = []
    module_names = [name for name, _module in auto_model.named_modules()]
    for candidate in ('query', 'key', 'value', 'dense', 'q_proj', 'k_proj', 'v_proj', 'o_proj'):
        if any(name.endswith(candidate) for name in module_names):
            target_modules.append(candidate)
    if not target_modules:
        target_modules = ['query', 'key', 'value']

    lora_config = LoraConfig(
        task_type=TaskType.FEATURE_EXTRACTION,
        inference_mode=False,
        r=8,
        lora_alpha=16,
        lora_dropout=0.05,
        target_modules=sorted(set(target_modules)),
    )
    auto_model = get_peft_model(auto_model, lora_config)
    transformer_module.auto_model = auto_model

    train_loss = losses.MultipleNegativesRankingLoss(model)
    warmup_steps = max(1, len(dataloader) // 10)
    model.fit(
        train_objectives=[(dataloader, train_loss)],
        epochs=max(1, int(epochs)),
        warmup_steps=warmup_steps,
        output_path=None,
        show_progress_bar=False,
    )

    merged_model = auto_model.merge_and_unload()
    transformer_module.auto_model = merged_model
    model.save(str(temp_output))
    _activate_trained_model(temp_output, model_path)
    manifest = _build_manifest(
        len(pairs),
        epochs,
        batch_size,
        device,
        'lora',
        extra={
            'lora': {
                'r': 8,
                'alpha': 16,
                'dropout': 0.05,
                'target_modules': sorted(set(target_modules)),
                'merged_for_inference': True,
            }
        },
    )
    _persist_manifest(manifest)
    return {
        'status': 'trained',
        'message': 'Embedding-модель адаптирована через LoRA и сохранена в merged-виде для стабильного инференса.',
        'sample_count': len(pairs),
        'manifest': manifest,
    }


def run_corpus_adaptation(max_pairs: int = 128, epochs: int = 1, batch_size: int = 8, strategy: str = 'full') -> dict:
    try:
        import sentence_transformers  # noqa: F401
    except Exception as exc:
        return {
            'status': 'failed',
            'message': f'sentence-transformers недоступен: {exc}',
            'sample_count': 0,
        }

    strategy = _normalize_strategy(strategy)
    pairs = build_corpus_adaptation_pairs(max_pairs=max_pairs)
    if len(pairs) < 8:
        return {
            'status': 'failed',
            'message': 'Недостаточно данных для безопасной адаптации. Сначала проиндексируйте несколько книг.',
            'sample_count': len(pairs),
        }

    profile = get_compute_profile()
    device = profile['model_device']
    model_path = Path(FINE_TUNED_MODEL_PATH)
    temp_output = BASE_DIR / 'fine_tuned_literary_model_tmp'
    _safe_remove_path(temp_output)

    try:
        if strategy == 'lora':
            try:
                return _train_lora_model(pairs, epochs, batch_size, device, model_path, temp_output)
            except Exception as exc:
                _safe_remove_path(temp_output)
                return {
                    'status': 'failed',
                    'message': f'LoRA-адаптация не удалась: {exc}',
                    'sample_count': len(pairs),
                }
        return _train_full_model(pairs, epochs, batch_size, device, model_path, temp_output)
    except Exception as exc:
        _safe_remove_path(temp_output)
        return {
            'status': 'failed',
            'message': f'Не удалось завершить адаптацию: {exc}',
            'sample_count': len(pairs),
        }


def remove_corpus_adaptation() -> dict:
    removed = False
    for path in [FINE_TUNED_MODEL_PATH, ADAPTATION_MANIFEST_PATH, BASE_DIR / 'fine_tuned_literary_model_tmp']:
        path_obj = Path(path)
        if path_obj.exists():
            removed = True
            _safe_remove_path(path_obj)
    reset_runtime_state()
    clear_cache()
    return {
        'status': 'removed' if removed else 'noop',
        'message': 'Адаптированная embedding-модель удалена.' if removed else 'Адаптированной embedding-модели не было.',
    }
