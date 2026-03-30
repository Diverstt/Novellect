import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from runtime_config import get_compute_profile

BASE_DIR = Path(__file__).resolve().parent
LOCAL_LLM_DIR = BASE_DIR / 'local_llm'
FINE_TUNED_LLM_DIR = BASE_DIR / 'fine_tuned_llm'

AutoConfig = None
AutoTokenizer = None
AutoModelForCausalLM = None
AutoModelForSeq2SeqLM = None
_TRANSFORMERS_ERROR = None



def _safe_import_torch():
    try:
        import torch

        return torch
    except Exception:
        return None



def _ensure_transformers():
    global AutoConfig, AutoTokenizer, AutoModelForCausalLM, AutoModelForSeq2SeqLM, _TRANSFORMERS_ERROR
    if AutoConfig is not None:
        return True
    if _TRANSFORMERS_ERROR is not None:
        return False
    try:
        from transformers import AutoConfig as _AutoConfig
        from transformers import AutoModelForCausalLM as _AutoModelForCausalLM
        from transformers import AutoModelForSeq2SeqLM as _AutoModelForSeq2SeqLM
        from transformers import AutoTokenizer as _AutoTokenizer

        AutoConfig = _AutoConfig
        AutoTokenizer = _AutoTokenizer
        AutoModelForCausalLM = _AutoModelForCausalLM
        AutoModelForSeq2SeqLM = _AutoModelForSeq2SeqLM
        return True
    except Exception as exc:
        _TRANSFORMERS_ERROR = exc
        return False



def safe_json_loads(text: str) -> Optional[dict]:
    if not text:
        return None
    stripped = text.strip()
    candidates = [stripped]
    match = re.search(r'\{.*\}', stripped, flags=re.DOTALL)
    if match:
        candidates.insert(0, match.group(0))
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            continue
    return None



def _extract_tag_content(text: str, tag: str) -> str:
    pattern = rf'<{tag}>(.*?)</{tag}>'
    match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return ''
    return match.group(1).strip()



class StubLocalLLM:
    backend = 'stub'

    def __init__(self):
        self.model_id = 'stub'
        self.device = 'cpu'
        self.is_encoder_decoder = False

    @staticmethod
    def _keywords(query: str) -> List[str]:
        words = [word for word in re.findall(r'\w+', (query or '').lower()) if len(word) > 3]
        unique = []
        seen = set()
        for word in words:
            if word not in seen:
                seen.add(word)
                unique.append(word)
        return unique

    def _plan_search(self, prompt: str) -> str:
        query = _extract_tag_content(prompt, 'user_query')
        base_type = _extract_tag_content(prompt, 'base_type') or 'keyword'
        keywords = self._keywords(query)
        search_queries = []
        for candidate in [query.strip(), ' '.join(keywords[:4]).strip(), ' '.join(keywords[:2]).strip()]:
            if candidate and candidate not in search_queries:
                search_queries.append(candidate)

        lowered = query.lower()
        query_type = base_type
        if any(marker in lowered for marker in ('хочу', 'посоветуй', 'что почитать', 'подбери')):
            query_type = 'recommendation'
        elif len(query.split()) <= 3 and query[:1].isupper():
            query_type = 'title'

        needs_clarification = len(keywords) <= 1 and len(query.split()) <= 2
        plan = {
            'strategy': 'stub-planner',
            'query_type': query_type,
            'search_queries': search_queries[:3] or [query],
            'needs_clarification': needs_clarification,
            'clarification_question': 'Уточните жанр, героя или атмосферу.' if needs_clarification else '',
            'answer_style': 'grounded_summary',
        }
        return json.dumps(plan, ensure_ascii=False)

    def _grounded_answer(self, prompt: str) -> str:
        sources = []
        for line in prompt.splitlines():
            line = line.strip()
            if not line.startswith('SOURCE '):
                continue
            match = re.match(r'^SOURCE \[(\d+)\] TITLE::(.*?) FORMAT::(.*?) SNIPPET::(.*)$', line)
            if not match:
                continue
            source_id, title, fmt, snippet = match.groups()
            sources.append({'id': source_id, 'title': title.strip(), 'format': fmt.strip(), 'snippet': snippet.strip()})

        if not sources:
            return 'По локальной библиотеке не найдено подтверждённых источников.'

        top = sources[0]
        snippet = top['snippet']
        if len(snippet) > 180:
            snippet = snippet[:177].rstrip() + '...'

        if len(sources) == 1:
            return f"Лучшее совпадение — **{top['title']}** [{top['id']}]. Основание: {snippet}"

        rest = ', '.join(f"**{item['title']}** [{item['id']}]" for item in sources[1:3])
        return (
            f"Лучшее совпадение — **{top['title']}** [{top['id']}]. "
            f"По фрагменту видно релевантное совпадение: {snippet} "
            f"Дополнительно стоит посмотреть {rest}."
        )

    def generate(self, prompt: str, max_new_tokens: int = 220, temperature: float = 0.1) -> str:
        if '### TASK: PLAN_SEARCH ###' in prompt:
            return self._plan_search(prompt)
        return self._grounded_answer(prompt)



class LocalLLMManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        self._model = None
        self._tokenizer = None
        self._device = None
        self._model_id = None
        self._is_encoder_decoder = False
        self._status = {
            'available': False,
            'backend': 'none',
            'message': 'LLM не загружена',
            'model_id': None,
            'device': None,
            'loaded': False,
        }

    def _configured_status(self) -> dict:
        profile = get_compute_profile()
        return {
            'available': False,
            'backend': 'none',
            'message': f"Модель будет загружена при первом запросе ({profile['llm_model_id']}).",
            'model_id': profile['llm_model_id'],
            'device': profile['llm_device'],
            'loaded': False,
        }

    def _candidate_model_ids(self, configured_model_id: str) -> List[str]:
        explicit = str(configured_model_id or '').strip()
        candidates = []
        if explicit:
            candidates.append(explicit)
        for path in (str(FINE_TUNED_LLM_DIR), str(LOCAL_LLM_DIR)):
            if os.path.exists(path) and path not in candidates:
                candidates.insert(0, path)
        return candidates

    @staticmethod
    def _use_stub(configured_model_id: str) -> bool:
        if os.getenv('NOVELLECT_USE_STUB_LLM', '').strip().lower() in {'1', 'true', 'yes'}:
            return True
        return str(configured_model_id or '').strip().lower() == 'stub'

    def _load_stub(self):
        self._model = StubLocalLLM()
        self._tokenizer = None
        self._device = 'cpu'
        self._model_id = 'stub'
        self._is_encoder_decoder = False
        self._status = {
            'available': True,
            'backend': 'stub',
            'message': 'Используется встроенная stub-LLM для тестирования.',
            'model_id': 'stub',
            'device': 'cpu',
            'loaded': True,
        }
        return self._model

    def _load_real_model(self, model_id: str, target_device: str):
        torch = _safe_import_torch()
        config = AutoConfig.from_pretrained(model_id, trust_remote_code=True)
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token or tokenizer.unk_token
        model_cls = AutoModelForSeq2SeqLM if getattr(config, 'is_encoder_decoder', False) else AutoModelForCausalLM

        load_kwargs = {'trust_remote_code': True}
        try:
            load_kwargs['low_cpu_mem_usage'] = True
            model = model_cls.from_pretrained(model_id, **load_kwargs)
        except TypeError:
            load_kwargs.pop('low_cpu_mem_usage', None)
            model = model_cls.from_pretrained(model_id, **load_kwargs)

        if torch is not None and target_device in {'cuda', 'mps'}:
            model = model.to(target_device)
        model.eval()
        self._model = model
        self._tokenizer = tokenizer
        self._device = target_device
        self._model_id = model_id
        self._is_encoder_decoder = bool(getattr(config, 'is_encoder_decoder', False))
        self._status = {
            'available': True,
            'backend': 'transformers',
            'message': f'{model_id} on {target_device}',
            'model_id': model_id,
            'device': target_device,
            'loaded': True,
        }
        return model

    def get_model(self, load: bool = True):
        profile = get_compute_profile()
        configured_model_id = profile['llm_model_id']
        target_device = profile['llm_device']

        if self._model is not None and self._model_id == configured_model_id and self._device == target_device:
            return self._model
        if self._model is not None and isinstance(self._model, StubLocalLLM) and self._use_stub(configured_model_id):
            return self._model
        if not load:
            return None

        if self._use_stub(configured_model_id):
            return self._load_stub()

        if not _ensure_transformers():
            message = str(_TRANSFORMERS_ERROR) if _TRANSFORMERS_ERROR else 'transformers недоступен'
            self._status = {
                'available': False,
                'backend': 'none',
                'message': message,
                'model_id': configured_model_id,
                'device': target_device,
                'loaded': False,
            }
            self._model = None
            self._tokenizer = None
            self._device = None
            self._model_id = configured_model_id
            return None

        errors: List[str] = []
        for candidate in self._candidate_model_ids(configured_model_id):
            for device in [target_device] + ([] if target_device == 'cpu' else ['cpu']):
                try:
                    return self._load_real_model(candidate, device)
                except Exception as exc:
                    errors.append(f'{candidate}@{device}: {exc}')

        self._model = None
        self._tokenizer = None
        self._device = None
        self._model_id = configured_model_id
        self._status = {
            'available': False,
            'backend': 'none',
            'message': ' | '.join(errors) if errors else 'Не удалось загрузить локальную LLM.',
            'model_id': configured_model_id,
            'device': target_device,
            'loaded': False,
        }
        return None

    def generate(self, prompt: str, max_new_tokens: Optional[int] = None, temperature: Optional[float] = None) -> Optional[str]:
        profile = get_compute_profile()
        max_new_tokens = max_new_tokens or int(profile['llm_max_new_tokens'])
        temperature = profile['llm_temperature'] if temperature is None else float(temperature)

        model = self.get_model(load=True)
        if model is None:
            return None
        if isinstance(model, StubLocalLLM):
            return model.generate(prompt, max_new_tokens=max_new_tokens, temperature=temperature)

        tokenizer = self._tokenizer
        torch = _safe_import_torch()
        if tokenizer is None or torch is None:
            return None

        try:
            encoded = tokenizer(prompt, return_tensors='pt', truncation=True, max_length=2048)
            if self._device in {'cuda', 'mps'}:
                encoded = {key: value.to(self._device) for key, value in encoded.items()}

            generation_kwargs = {
                'max_new_tokens': int(max_new_tokens),
                'do_sample': bool(temperature and temperature > 0.05),
                'temperature': max(float(temperature), 0.05),
                'top_p': 0.92,
                'repetition_penalty': 1.06,
                'pad_token_id': tokenizer.pad_token_id,
                'eos_token_id': tokenizer.eos_token_id,
            }
            with torch.no_grad():
                output = model.generate(**encoded, **generation_kwargs)

            if self._is_encoder_decoder:
                text = tokenizer.decode(output[0], skip_special_tokens=True)
            else:
                input_length = encoded['input_ids'].shape[1]
                text = tokenizer.decode(output[0][input_length:], skip_special_tokens=True)
            return text.strip()
        except Exception as exc:
            self._status = {
                'available': False,
                'backend': 'none',
                'message': f'Ошибка генерации: {exc}',
                'model_id': self._model_id,
                'device': self._device,
                'loaded': True,
            }
            return None

    def reset(self):
        self._model = None
        self._tokenizer = None
        self._device = None
        self._model_id = None
        self._is_encoder_decoder = False
        self._status = {
            'available': False,
            'backend': 'none',
            'message': 'LLM сброшена',
            'model_id': None,
            'device': None,
            'loaded': False,
        }

    def status(self, load: bool = False):
        if load:
            self.get_model(load=True)
        if self._model is None and not load:
            if self._status.get('model_id') or self._status.get('loaded') or self._status.get('backend') != 'none':
                return dict(self._status)
            if self._status.get('message') not in {'LLM не загружена', 'LLM сброшена'}:
                return dict(self._status)
            return self._configured_status()
        return dict(self._status)



_llm_manager = LocalLLMManager()



def generate_text(prompt: str, max_new_tokens: Optional[int] = None, temperature: Optional[float] = None) -> Optional[str]:
    return _llm_manager.generate(prompt, max_new_tokens=max_new_tokens, temperature=temperature)



def llm_status(load: bool = False) -> dict:
    return _llm_manager.status(load=load)



def reset_llm_state():
    _llm_manager.reset()


__all__ = ['generate_text', 'llm_status', 'reset_llm_state', 'safe_json_loads']
