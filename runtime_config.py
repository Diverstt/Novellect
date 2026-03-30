import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_CONFIG_FILE = BASE_DIR / 'runtime_config.json'

DEFAULT_RUNTIME_CONFIG = {
    'model_device': 'auto',
    'reranker_device': 'auto',
    'batch_size_cpu': 16,
    'batch_size_gpu': 64,
    'cloud_timeout_sec': 90,
    'cloud_max_file_mb': 1024,
    'search_mode': 'lite',
    'llm_device': 'auto',
    'llm_model_id': 'Qwen/Qwen2.5-0.5B-Instruct',
    'llm_max_new_tokens': 220,
    'llm_temperature': 0.1,
}

_DEVICE_ALIASES = {
    'auto': 'auto',
    'cpu': 'cpu',
    'gpu': 'gpu',
    'cuda': 'cuda',
    'mps': 'mps',
}

_SEARCH_MODE_ALIASES = {
    'lite': 'lite',
    'light': 'lite',
    'pipeline': 'lite',
    'weak': 'lite',
    'full': 'full',
    'llm': 'full',
    'agent': 'full',
    'llm_agent': 'full',
}


def _safe_import_torch():
    try:
        import torch

        return torch
    except Exception:
        return None



def detect_hardware():
    torch = _safe_import_torch()
    info = {
        'torch_available': torch is not None,
        'cuda_available': False,
        'cuda_name': None,
        'mps_available': False,
        'gpu_available': False,
        'preferred_gpu_device': None,
    }

    if torch is None:
        info['preferred_gpu_device'] = 'cpu'
        return info

    try:
        info['cuda_available'] = bool(torch.cuda.is_available())
    except Exception:
        info['cuda_available'] = False

    if info['cuda_available']:
        try:
            info['cuda_name'] = torch.cuda.get_device_name(0)
        except Exception:
            info['cuda_name'] = 'CUDA GPU'

    try:
        mps_backend = getattr(torch.backends, 'mps', None)
        info['mps_available'] = bool(mps_backend and mps_backend.is_available())
    except Exception:
        info['mps_available'] = False

    info['gpu_available'] = info['cuda_available'] or info['mps_available']
    if info['cuda_available']:
        info['preferred_gpu_device'] = 'cuda'
    elif info['mps_available']:
        info['preferred_gpu_device'] = 'mps'
    else:
        info['preferred_gpu_device'] = 'cpu'

    return info



def _normalize_device(value):
    normalized = (value or 'auto').strip().lower()
    return _DEVICE_ALIASES.get(normalized, 'auto')



def _normalize_search_mode(value):
    normalized = (value or 'lite').strip().lower()
    return _SEARCH_MODE_ALIASES.get(normalized, 'lite')



def _apply_env_overrides(config):
    env_to_key = {
        'NOVELLECT_MODEL_DEVICE': ('model_device', str),
        'NOVELLECT_RERANKER_DEVICE': ('reranker_device', str),
        'NOVELLECT_BATCH_SIZE_CPU': ('batch_size_cpu', int),
        'NOVELLECT_BATCH_SIZE_GPU': ('batch_size_gpu', int),
        'NOVELLECT_CLOUD_TIMEOUT_SEC': ('cloud_timeout_sec', int),
        'NOVELLECT_CLOUD_MAX_FILE_MB': ('cloud_max_file_mb', int),
        'NOVELLECT_SEARCH_MODE': ('search_mode', str),
        'NOVELLECT_LLM_DEVICE': ('llm_device', str),
        'NOVELLECT_LLM_MODEL_ID': ('llm_model_id', str),
        'NOVELLECT_LLM_MAX_NEW_TOKENS': ('llm_max_new_tokens', int),
        'NOVELLECT_LLM_TEMPERATURE': ('llm_temperature', float),
    }

    for env_name, (key, caster) in env_to_key.items():
        raw_value = os.getenv(env_name)
        if raw_value in (None, ''):
            continue
        try:
            config[key] = caster(raw_value)
        except Exception:
            pass
    return config



def _normalize_config(config):
    normalized = dict(DEFAULT_RUNTIME_CONFIG)
    normalized.update(config or {})
    normalized['model_device'] = _normalize_device(normalized.get('model_device', 'auto'))
    normalized['reranker_device'] = _normalize_device(normalized.get('reranker_device', 'auto'))
    normalized['llm_device'] = _normalize_device(normalized.get('llm_device', 'auto'))
    normalized['search_mode'] = _normalize_search_mode(normalized.get('search_mode', 'lite'))
    normalized['batch_size_cpu'] = max(1, int(normalized.get('batch_size_cpu', DEFAULT_RUNTIME_CONFIG['batch_size_cpu'])))
    normalized['batch_size_gpu'] = max(1, int(normalized.get('batch_size_gpu', DEFAULT_RUNTIME_CONFIG['batch_size_gpu'])))
    normalized['cloud_timeout_sec'] = max(5, int(normalized.get('cloud_timeout_sec', DEFAULT_RUNTIME_CONFIG['cloud_timeout_sec'])))
    normalized['cloud_max_file_mb'] = max(1, int(normalized.get('cloud_max_file_mb', DEFAULT_RUNTIME_CONFIG['cloud_max_file_mb'])))
    normalized['llm_max_new_tokens'] = max(32, int(normalized.get('llm_max_new_tokens', DEFAULT_RUNTIME_CONFIG['llm_max_new_tokens'])))
    normalized['llm_temperature'] = min(1.5, max(0.0, float(normalized.get('llm_temperature', DEFAULT_RUNTIME_CONFIG['llm_temperature']))))
    normalized['llm_model_id'] = str(normalized.get('llm_model_id', DEFAULT_RUNTIME_CONFIG['llm_model_id']) or DEFAULT_RUNTIME_CONFIG['llm_model_id']).strip()
    return normalized



def load_runtime_config():
    config = dict(DEFAULT_RUNTIME_CONFIG)
    if RUNTIME_CONFIG_FILE.exists():
        try:
            with open(RUNTIME_CONFIG_FILE, 'r', encoding='utf-8') as file_obj:
                stored = json.load(file_obj)
            if isinstance(stored, dict):
                config.update(stored)
        except Exception:
            pass
    config = _apply_env_overrides(config)
    return _normalize_config(config)



def save_runtime_config(config):
    merged = _normalize_config(config)
    with open(RUNTIME_CONFIG_FILE, 'w', encoding='utf-8') as file_obj:
        json.dump(merged, file_obj, ensure_ascii=False, indent=2)
    return merged



def resolve_device(preference='auto'):
    hardware = detect_hardware()
    preference = _normalize_device(preference)

    if preference == 'auto':
        return hardware['preferred_gpu_device'] or 'cpu'
    if preference == 'gpu':
        return hardware['preferred_gpu_device'] or 'cpu'
    if preference == 'cuda':
        return 'cuda' if hardware['cuda_available'] else 'cpu'
    if preference == 'mps':
        return 'mps' if hardware['mps_available'] else 'cpu'
    return 'cpu'



def estimate_runtime_requirements(config=None):
    config = _normalize_config(config or load_runtime_config())
    mode = config['search_mode']
    model_hint = str(config.get('llm_model_id') or '').lower()

    full_ram = '6–10 ГБ RAM'
    full_vram = '4+ ГБ VRAM желательно'
    if any(tag in model_hint for tag in ('7b', '8b', '9b', '13b')):
        full_ram = '16–32 ГБ RAM'
        full_vram = '12+ ГБ VRAM желательно'
    elif any(tag in model_hint for tag in ('1.5b', '1.7b', '1.8b', '2b', '3b')):
        full_ram = '10–16 ГБ RAM'
        full_vram = '8+ ГБ VRAM желательно'
    elif any(tag in model_hint for tag in ('0.5b', '360m', 'small')):
        full_ram = '4–8 ГБ RAM'
        full_vram = '3+ ГБ VRAM желательно'

    profiles = {
        'lite': {
            'name': 'Lite / слабое железо',
            'ram': '2–4 ГБ RAM',
            'vram': 'не требуется',
            'notes': 'Текущий агентный пайплайн без локальной генерации LLM. Подходит для CPU-only, мини-ПК и Raspberry-подобных устройств.',
        },
        'full': {
            'name': 'Full / LLM + агент',
            'ram': full_ram,
            'vram': full_vram,
            'notes': 'Дополнительно загружается локальная LLM для планирования поиска и синтеза ответа. Рекомендуется для ноутбуков/ПК с запасом памяти.',
        },
    }
    return {'active_mode': mode, 'profiles': profiles, 'active': profiles[mode]}



def get_compute_profile():
    config = load_runtime_config()
    hardware = detect_hardware()
    model_device = resolve_device(config['model_device'])
    reranker_device = resolve_device(config['reranker_device'])
    llm_device = resolve_device(config['llm_device'])
    is_gpu = model_device in {'cuda', 'mps'}
    return {
        'config': config,
        'hardware': hardware,
        'model_device': model_device,
        'reranker_device': reranker_device,
        'llm_device': llm_device,
        'model_device_requested': config['model_device'],
        'reranker_device_requested': config['reranker_device'],
        'llm_device_requested': config['llm_device'],
        'embedding_batch_size': config['batch_size_gpu'] if is_gpu else config['batch_size_cpu'],
        'cloud_timeout_sec': config['cloud_timeout_sec'],
        'cloud_max_file_mb': config['cloud_max_file_mb'],
        'search_mode': config['search_mode'],
        'llm_model_id': config['llm_model_id'],
        'llm_max_new_tokens': config['llm_max_new_tokens'],
        'llm_temperature': config['llm_temperature'],
        'requirements': estimate_runtime_requirements(config),
    }
