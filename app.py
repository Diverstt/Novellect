import hashlib
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import storage
from agents import AgentOrchestrator
from archive_handler import ArchiveProcessingError, build_safe_storage_name, iter_supported_files_from_zip
from cloud_sources import CloudSourceError, download_cloud_source, normalize_source_lines
from converter import process_file
from fine_tuning import (
    SUPPORTED_ADAPTATION_STRATEGIES,
    adaptation_status,
    preview_corpus_adaptation,
    remove_corpus_adaptation,
    run_corpus_adaptation,
)
from llm_runtime import llm_status, reset_llm_state
from runtime_config import detect_hardware, get_compute_profile, load_runtime_config, save_runtime_config
from search_engine import add_to_index, clear_cache, model_status, reset_runtime_state

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / 'uploads'
TXT_CACHE_DIR = BASE_DIR / 'txt_cache'
SYSTEM_PATHS = [
    BASE_DIR / 'storage.json',
    BASE_DIR / 'vector_db.npz',
    BASE_DIR / 'search_cache.pkl',
    BASE_DIR / 'runtime_config.json',
    BASE_DIR / 'fine_tuned_literary_model',
    BASE_DIR / 'fine_tuned_literary_model_manifest.json',
    BASE_DIR / 'fine_tuned_literary_model_backup',
]
SUPPORTED_UPLOAD_TYPES = ['txt', 'fb2', 'pdf', 'epub', 'zip']
DEVICE_OPTIONS = ['auto', 'cpu', 'gpu']
SEARCH_MODE_OPTIONS = ['lite', 'full']
SEARCH_MODE_LABELS = {
    'lite': 'Lite / слабое железо',
    'full': 'Full / LLM + агент',
}

st.set_page_config(page_title='Novellect', page_icon='📚', layout='wide')
st.title('📚 Поиск по библиотеке')
st.caption('Локальная библиотека книг с двумя режимами: лёгкий агентный пайплайн и полная версия с локальной LLM + агентом')

for directory in [UPLOAD_DIR, TXT_CACHE_DIR]:
    directory.mkdir(exist_ok=True)

if 'orchestrator' not in st.session_state:
    st.session_state.orchestrator = AgentOrchestrator()
if 'history' not in st.session_state:
    st.session_state.history = []



def _remove_path(path):
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



def _cleanup_failed_artifacts(file_path, cache_path=None):
    if file_path:
        _remove_path(file_path)
    if cache_path:
        _remove_path(cache_path)
        _remove_path(f'{cache_path}.meta')



def _store_source_bytes(file_id, source_name, file_bytes):
    safe_name = build_safe_storage_name(source_name)
    file_path = UPLOAD_DIR / f'{file_id}_{safe_name}'
    with open(file_path, 'wb') as file_obj:
        file_obj.write(file_bytes)
    return file_path



def _ingest_source_bytes(source_name, file_bytes, status_text, extra_metadata=None):
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    duplicate = storage.get_book_by_hash(file_hash)
    if duplicate:
        return {'status': 'skipped', 'message': f"Пропуск: книга '{source_name}' уже есть в библиотеке (как '{duplicate['title']}')."}
    if storage.is_limit_exceeded(len(file_bytes)):
        return {'status': 'failed', 'message': f'Лимит 1 ГБ превышен! Файл {source_name} пропущен.'}

    file_id = str(uuid.uuid4())
    status_text.text(f'Обработка: {source_name}')
    file_path = _store_source_bytes(file_id, source_name, file_bytes)
    book_data = process_file(str(file_path), original_name=source_name)
    if book_data.get('error'):
        _cleanup_failed_artifacts(file_path, book_data.get('cache_path'))
        return {'status': 'failed', 'message': f"Ошибка в {source_name}: {book_data['error']}"}

    book_data['file_hash'] = file_hash
    book_data['file_path'] = str(file_path)
    book_data['source_filename'] = source_name
    if extra_metadata:
        book_data.setdefault('metadata', {})
        book_data['metadata'].update(extra_metadata)

    record = add_to_index(book_data, file_id)
    if record is None:
        _cleanup_failed_artifacts(file_path, book_data.get('cache_path'))
        return {'status': 'failed', 'message': f'Не удалось проиндексировать {source_name}.'}
    return {'status': 'indexed', 'record': record}



def _process_zip_bytes(archive_name, zip_bytes, status_text, extra_metadata=None):
    summary = {'indexed': 0, 'failed': 0, 'skipped': 0}
    try:
        found = False
        for member in iter_supported_files_from_zip(zip_bytes, archive_name):
            found = True
            member_metadata = dict(extra_metadata or {})
            member_metadata.update(member.get('metadata', {}))
            result = _ingest_source_bytes(member['source_filename'], member['bytes'], status_text, extra_metadata=member_metadata)
            summary[result['status']] += 1
            if result['status'] == 'skipped':
                st.warning(result['message'])
            elif result['status'] == 'failed':
                st.error(result['message'])
        if not found:
            st.warning(f"Архив '{archive_name}' не содержит поддерживаемых книг (.txt, .fb2, .pdf, .epub).")
            summary['skipped'] += 1
    except ArchiveProcessingError as exc:
        summary['failed'] += 1
        st.error(f"Ошибка архива '{archive_name}': {exc}")
    return summary



def _process_source_payload(source_name, file_bytes, status_text, extra_metadata=None):
    if source_name.lower().endswith('.zip'):
        return _process_zip_bytes(source_name, file_bytes, status_text, extra_metadata=extra_metadata)
    result = _ingest_source_bytes(source_name, file_bytes, status_text, extra_metadata=extra_metadata)
    if result['status'] == 'skipped':
        st.warning(result['message'])
        return {'indexed': 0, 'failed': 0, 'skipped': 1}
    if result['status'] == 'failed':
        st.error(result['message'])
        return {'indexed': 0, 'failed': 1, 'skipped': 0}
    return {'indexed': 1, 'failed': 0, 'skipped': 0}



def _process_cloud_reference(source_reference, status_text):
    profile = get_compute_profile()
    payload = download_cloud_source(source_reference, timeout=profile['cloud_timeout_sec'], max_file_mb=profile['cloud_max_file_mb'])
    metadata = dict(payload.get('metadata', {}))
    metadata.update({'ingest_origin': 'cloud', 'cloud_url': source_reference})
    return _process_source_payload(payload['source_name'], payload['bytes'], status_text, extra_metadata=metadata)



def _apply_runtime_config(
    model_device,
    reranker_device,
    batch_size_cpu,
    batch_size_gpu,
    cloud_timeout_sec,
    cloud_max_file_mb,
    search_mode,
    llm_device,
    llm_model_id,
    llm_max_new_tokens,
    llm_temperature,
):
    save_runtime_config(
        {
            'model_device': model_device,
            'reranker_device': reranker_device,
            'batch_size_cpu': int(batch_size_cpu),
            'batch_size_gpu': int(batch_size_gpu),
            'cloud_timeout_sec': int(cloud_timeout_sec),
            'cloud_max_file_mb': int(cloud_max_file_mb),
            'search_mode': search_mode,
            'llm_device': llm_device,
            'llm_model_id': llm_model_id,
            'llm_max_new_tokens': int(llm_max_new_tokens),
            'llm_temperature': float(llm_temperature),
        }
    )
    reset_runtime_state()
    reset_llm_state()
    clear_cache()
    st.session_state.orchestrator = AgentOrchestrator()



def _render_lite_response(response):
    if response['type'] == 'empty':
        st.info(response.get('message', 'Ничего не найдено.'))
        return

    if response['type'] == 'recommendation':
        st.success(f"Режим: {response.get('query_type', 'recommendation')} · Lite / слабое железо")
        for recommendation in response.get('recommendations', []):
            expander_title = f"📖 {recommendation['title']} ({recommendation.get('format', 'unknown')})"
            with st.expander(expander_title):
                if recommendation.get('feature_matches'):
                    st.caption('Совпадения: ' + ', '.join(recommendation['feature_matches']))
                for snippet in recommendation.get('snippets', []):
                    st.write(snippet)
        return

    st.success(f"Режим: {response.get('query_type', response['type'])} · Lite / слабое железо")
    for result in response.get('results', []):
        with st.expander(f"📖 {result['title']} ({result.get('format', 'unknown')})"):
            st.write(result.get('snippet', ''))
            st.caption(f"Релевантность: {result.get('relevance', 0):.3f}")



def _render_full_response(response):
    if response.get('message'):
        st.info(response['message'])

    if response['type'] == 'empty':
        st.info(response.get('answer') or response.get('message') or 'Ничего не найдено.')
        return

    llm_badge = 'LLM' if response.get('llm_used') else 'fallback'
    st.success(f"Режим: Full / LLM + агент ({llm_badge})")
    st.markdown(response.get('answer', ''))

    if response.get('sources'):
        st.markdown('#### Источники ответа')
        for source in response['sources']:
            with st.expander(f"[{source['source_id']}] {source['title']} ({source.get('format', 'unknown')})"):
                st.write(source.get('snippet', ''))
                if source.get('matched_queries'):
                    st.caption('Найдено через: ' + ', '.join(source['matched_queries']))
                st.caption(f"Релевантность: {source.get('relevance', 0):.3f}")

    # Детальные результаты поиска скрыты в full-режиме, чтобы не дублировать блок ответа и источники.

    with st.expander('🧭 План и трассировка агента', expanded=False):
        plan = response.get('plan', {})
        st.write(f"Стратегия: {plan.get('strategy', 'n/a')}")
        st.write(f"Тип запроса: {response.get('query_type', 'unknown')}")
        if plan.get('search_queries'):
            st.write('Подзапросы: ' + ', '.join(plan['search_queries']))
        for trace_item in response.get('search_trace', []):
            st.caption(f"{trace_item.get('query', '')} → {trace_item.get('hits', 0)} совпадений")



with st.sidebar:
    st.header('📊 Состояние системы')
    library_size_mb = storage.get_library_size() / (1024 * 1024)
    st.progress(min(library_size_mb / 1024, 1.0))
    st.caption(f'Занято: {library_size_mb:.2f} МБ из 1024 МБ')

    index = storage.load_index()
    total_chunks = sum(book.get('chunks_count', 0) for book in index)
    col_a, col_b = st.columns(2)
    with col_a:
        st.metric('Книг', len(index))
    with col_b:
        st.metric('Чанков', total_chunks)

    st.header('⚙️ Режим вычислений и поиска')
    runtime_config = load_runtime_config()
    compute_profile = get_compute_profile()
    requirements = compute_profile['requirements']
    hardware = detect_hardware()
    embedding_backend = model_status()
    llm_backend = llm_status(load=False)

    if hardware['cuda_available']:
        st.success(f"GPU обнаружен: {hardware['cuda_name'] or 'CUDA'}")
    elif hardware['mps_available']:
        st.success('GPU обнаружен: Apple Metal (MPS)')
    else:
        st.info('GPU не обнаружен, доступен CPU режим.')

    st.caption(
        'Эмбеддинги: '
        f"{compute_profile['model_device_requested']} → {compute_profile['model_device']} · "
        f"Реранкер: {compute_profile['reranker_device_requested']} → {compute_profile['reranker_device']}"
    )
    st.caption(
        'Режим поиска: '
        f"{SEARCH_MODE_LABELS.get(compute_profile['search_mode'], compute_profile['search_mode'])}"
    )

    if embedding_backend['available']:
        st.caption(f"Embedding-модель: {embedding_backend['message']}")
    else:
        st.warning(f"Sentence-transformers недоступен, будет использован lexical fallback. {embedding_backend['message']}")

    st.caption(f"LLM: {llm_backend.get('message', 'не настроена')}")

    with st.expander('Настройки режимов и моделей', expanded=False):
        search_mode = st.selectbox(
            'Режим поиска',
            SEARCH_MODE_OPTIONS,
            index=SEARCH_MODE_OPTIONS.index(runtime_config.get('search_mode', 'lite')),
            format_func=lambda value: SEARCH_MODE_LABELS.get(value, value),
        )
        model_device = st.selectbox('Эмбеддинги и индексация', DEVICE_OPTIONS, index=DEVICE_OPTIONS.index(runtime_config.get('model_device', 'auto')))
        reranker_device = st.selectbox('Реранкер', DEVICE_OPTIONS, index=DEVICE_OPTIONS.index(runtime_config.get('reranker_device', 'auto')))
        llm_device = st.selectbox('Локальная LLM', DEVICE_OPTIONS, index=DEVICE_OPTIONS.index(runtime_config.get('llm_device', 'auto')))
        llm_model_id = st.text_input('LLM model id или локальный путь', value=runtime_config.get('llm_model_id', 'Qwen/Qwen2.5-0.5B-Instruct'))
        llm_max_new_tokens = st.number_input('LLM max_new_tokens', min_value=32, max_value=1024, value=int(runtime_config.get('llm_max_new_tokens', 220)))
        llm_temperature = st.slider('LLM temperature', min_value=0.0, max_value=1.0, value=float(runtime_config.get('llm_temperature', 0.1)), step=0.05)
        batch_size_cpu = st.number_input('Batch size для CPU', min_value=1, max_value=256, value=int(runtime_config.get('batch_size_cpu', 16)))
        batch_size_gpu = st.number_input('Batch size для GPU', min_value=1, max_value=512, value=int(runtime_config.get('batch_size_gpu', 64)))
        cloud_timeout_sec = st.number_input('Таймаут облачной загрузки (сек)', min_value=5, max_value=600, value=int(runtime_config.get('cloud_timeout_sec', 90)))
        cloud_max_file_mb = st.number_input('Макс. размер облачного файла (МБ)', min_value=1, max_value=4096, value=int(runtime_config.get('cloud_max_file_mb', 1024)))
        st.caption('Lite использует текущий агентный пайплайн. Full добавляет локальную LLM для планирования поиска и grounded-ответа.')
        if st.button('💾 Применить режим', use_container_width=True):
            _apply_runtime_config(
                model_device,
                reranker_device,
                batch_size_cpu,
                batch_size_gpu,
                cloud_timeout_sec,
                cloud_max_file_mb,
                search_mode,
                llm_device,
                llm_model_id,
                llm_max_new_tokens,
                llm_temperature,
            )
            st.success('Новый режим сохранён.')
            st.rerun()

    with st.expander('📉 Анализ вычислительных требований', expanded=False):
        active = requirements['active']
        lite_profile = requirements['profiles']['lite']
        full_profile = requirements['profiles']['full']
        st.write(f"Активный режим: {active['name']}")
        st.caption(f"Оценка памяти: {active['ram']} · {active['vram']}")
        st.caption(active['notes'])
        st.markdown('**Lite**')
        st.caption(f"{lite_profile['ram']} · {lite_profile['vram']}")
        st.caption(lite_profile['notes'])
        st.markdown('**Full**')
        st.caption(f"{full_profile['ram']} · {full_profile['vram']}")
        st.caption(full_profile['notes'])

    with st.expander('🧪 Дообучение embedding-модели', expanded=False):
        adaptation = adaptation_status()
        preview = preview_corpus_adaptation(max_pairs=24) if index else {'total_pairs': 0, 'preview': [], 'supported_strategies': list(SUPPORTED_ADAPTATION_STRATEGIES)}
        adaptation_labels = {'full': 'Полный fine-tuning', 'lora': 'LoRA'}
        selected_strategy = st.radio(
            'Стратегия адаптации',
            options=list(SUPPORTED_ADAPTATION_STRATEGIES),
            index=list(SUPPORTED_ADAPTATION_STRATEGIES).index('lora') if 'lora' in SUPPORTED_ADAPTATION_STRATEGIES else 0,
            horizontal=True,
            format_func=lambda value: adaptation_labels.get(value, value),
        )
        if adaptation['available']:
            manifest = adaptation.get('manifest') or {}
            st.success('Адаптированная embedding-модель найдена.')
            if manifest:
                st.caption(
                    f"Обучена: {manifest.get('created_at', 'неизвестно')} · "
                    f"пар: {manifest.get('sample_count', 0)} · эпох: {manifest.get('epochs', 0)} · "
                    f"стратегия: {adaptation_labels.get(manifest.get('strategy', 'full'), manifest.get('strategy', 'full'))}"
                )
                if manifest.get('lora'):
                    lora_meta = manifest['lora']
                    st.caption(
                        f"LoRA: r={lora_meta.get('r', 'n/a')} · alpha={lora_meta.get('alpha', 'n/a')} · "
                        f"merged={lora_meta.get('merged_for_inference', False)}"
                    )
        else:
            st.info('Адаптированная embedding-модель пока не создана.')
        st.caption(f"Доступно синтетических пар для адаптации: {preview.get('total_pairs', 0)}")
        for item in preview.get('preview', [])[:3]:
            st.caption(f"Запрос: {item['query']}")
        st.caption('LoRA позволяет адаптировать модель параметро-эффективно, а после обучения адаптер автоматически merge-ится для стабильного локального инференса.')
        col_ft1, col_ft2 = st.columns(2)
        with col_ft1:
            if st.button('▶️ Запустить адаптацию', key='run_adaptation', use_container_width=True):
                with st.spinner(f"Выполняется безопасная адаптация embedding-модели ({adaptation_labels.get(selected_strategy, selected_strategy)})..."):
                    result = run_corpus_adaptation(max_pairs=128, epochs=1, batch_size=8, strategy=selected_strategy)
                if result['status'] == 'trained':
                    st.success(result['message'])
                else:
                    st.warning(result['message'])
                st.session_state.orchestrator = AgentOrchestrator()
                st.rerun()
        with col_ft2:
            if st.button('🗑 Удалить адаптацию', key='remove_adaptation', use_container_width=True):
                result = remove_corpus_adaptation()
                st.info(result['message'])
                st.session_state.orchestrator = AgentOrchestrator()
                st.rerun()
        st.caption('Адаптация не запускается автоматически: базовое качество поиска сохраняется, а улучшение включается только после отдельной проверки.')

    st.header('🛠 Управление библиотекой')
    with st.expander('📤 Локальная загрузка', expanded=True):
        uploaded_files = st.file_uploader('Выберите файлы (.txt, .fb2, .pdf, .epub, .zip)', type=SUPPORTED_UPLOAD_TYPES, accept_multiple_files=True)
        st.caption('ZIP распаковывается локально; внутри поддерживаются .txt, .fb2, .pdf и .epub.')
        if uploaded_files and st.button('🚀 Начать индексацию', use_container_width=True):
            progress_bar = st.progress(0)
            status_text = st.empty()
            summary = {'indexed': 0, 'failed': 0, 'skipped': 0}
            for idx, uploaded_file in enumerate(uploaded_files):
                result = _process_source_payload(uploaded_file.name, uploaded_file.getvalue(), status_text)
                for key in summary:
                    summary[key] += result[key]
                progress_bar.progress((idx + 1) / len(uploaded_files))
            if summary['indexed']:
                st.success(f"Готово: добавлено {summary['indexed']}, пропущено {summary['skipped']}, ошибок {summary['failed']}.")
            elif summary['skipped'] and not summary['failed']:
                st.info(f"Новых книг не добавлено: пропущено {summary['skipped']}.")
            else:
                st.error('Ни одна книга не была проиндексирована.')
            time.sleep(0.4)
            st.rerun()

    with st.expander('☁️ Загрузка из облака', expanded=False):
        cloud_references = st.text_area(
            'Ссылки или cloud-path (по одной на строку)',
            placeholder='https://drive.google.com/file/d/FILE_ID/view\nhttps://storage.googleapis.com/my-bucket/books.zip\ngs://my-bucket/library/book.fb2',
            height=100,
        )
        st.caption('Поддерживаются прямые URL, публичные ссылки Google Drive и Google Cloud Storage.')
        if st.button('☁️ Импортировать из облака', use_container_width=True):
            sources = normalize_source_lines(cloud_references)
            if not sources:
                st.warning('Добавьте хотя бы одну ссылку или gs:// путь.')
            else:
                progress_bar = st.progress(0)
                status_text = st.empty()
                summary = {'indexed': 0, 'failed': 0, 'skipped': 0}
                for idx, source_reference in enumerate(sources):
                    try:
                        result = _process_cloud_reference(source_reference, status_text)
                        for key in summary:
                            summary[key] += result[key]
                    except CloudSourceError as exc:
                        summary['failed'] += 1
                        st.error(f'Облачная загрузка не удалась для {source_reference}: {exc}')
                    except Exception as exc:
                        summary['failed'] += 1
                        st.error(f'Неожиданная ошибка при обработке {source_reference}: {exc}')
                    progress_bar.progress((idx + 1) / len(sources))
                if summary['indexed']:
                    st.success(f"Готово: добавлено {summary['indexed']}, пропущено {summary['skipped']}, ошибок {summary['failed']}.")
                elif summary['skipped'] and not summary['failed']:
                    st.info(f"Новых книг не добавлено: пропущено {summary['skipped']}.")
                else:
                    st.error('Ни один облачный источник не был проиндексирован.')
                time.sleep(0.4)
                st.rerun()

    st.markdown('---')
    col_c1, col_c2 = st.columns(2)
    with col_c1:
        if st.button('🗑 Кэш поиска', use_container_width=True):
            clear_cache()
            st.success('Кэш очищен')
    with col_c2:
        if st.button('🗑 Очистить всё', use_container_width=True):
            for path in SYSTEM_PATHS:
                _remove_path(path)
            for folder in [UPLOAD_DIR, TXT_CACHE_DIR]:
                if folder.exists():
                    for file_path in folder.iterdir():
                        _remove_path(file_path)
            reset_runtime_state()
            reset_llm_state()
            st.session_state.clear()
            st.success('Система полностью очищена')
            st.rerun()

    with st.expander('📚 Библиотека'):
        if index:
            sorted_books = sorted(index, key=lambda item: item.get('added_date') or 0, reverse=True)
            for book in sorted_books:
                title = book.get('title', 'Без названия')
                added = book.get('added_date')
                added_str = datetime.fromtimestamp(added).strftime('%d.%m %H:%M') if added else 'неизвестно'
                source = '☁️ Облако' if (book.get('metadata') or {}).get('ingest_origin') == 'cloud' else '💾 Локально'
                st.write(f'**{title}**')
                st.caption(f'{source} · {book.get("format", "unknown")} · добавлено {added_str}')
                if st.button('🗑 Удалить', key=f"del_{book['id']}", use_container_width=True):
                    deleted = storage.delete_book_physically(book['id'])
                    if deleted.get('deleted'):
                        st.success(f"Удалено: {deleted.get('title', title)}")
                    else:
                        st.warning(deleted.get('message', 'Книга не найдена в индексе.'))
                    time.sleep(0.3)
                    st.rerun()
                st.markdown('---')
        else:
            st.write('Библиотека пуста')

st.subheader('🔎 Поиск')
with st.form('search_form'):
    query = st.text_input('Введите запрос:', placeholder='Например: Раскольников / Доктор Живаго / хочу книгу про конфликт поколений')
    submitted = st.form_submit_button('🔍 Найти', use_container_width=True)

if submitted and query:
    started = time.time()
    with st.status('🤖 Система анализирует запрос...', expanded=True) as status:
        try:
            active_profile = get_compute_profile()
            if active_profile['search_mode'] == 'full':
                st.write('🧭 Эвристический анализ + планирование через локальную LLM...')
                st.write('🛠 Агент выполняет несколько поисковых проходов по индексу...')
                st.write('🧾 Формируется grounded-ответ с опорой на найденные фрагменты...')
            else:
                st.write('🧭 Анализ типа запроса и очистка формулировки...')
                st.write('📚 Поиск по title / entity / keyword / recommendation маршрутам...')

            response = st.session_state.orchestrator.process_query(query)
            elapsed = time.time() - started
            status.update(label=f'✅ Обработка завершена за {elapsed:.2f} сек', state='complete', expanded=False)
            st.markdown('### Результаты')

            if response.get('mode') == 'full_llm_agent':
                _render_full_response(response)
            else:
                _render_lite_response(response)

            st.session_state.history.append(
                {
                    'Запрос': query,
                    'Тип': response.get('query_type', response.get('type', 'unknown')),
                    'Режим': 'Full' if response.get('mode') == 'full_llm_agent' else 'Lite',
                    'LLM': 'да' if response.get('llm_used') else 'нет',
                    'Время (сек)': round(elapsed, 2),
                    'Устройство': active_profile['model_device'],
                }
            )
        except Exception as exc:
            st.error(f'❌ Критическая ошибка поиска: {exc}')

if st.session_state.history:
    with st.expander('📋 История последних запросов', expanded=False):
        df = pd.DataFrame(st.session_state.history).iloc[::-1]
        st.table(df.head(10))
        if st.button('🗑 Очистить журнал', use_container_width=True):
            st.session_state.history = []
            st.rerun()
