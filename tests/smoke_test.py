import json
import os
import sys
import tempfile
from unittest.mock import patch
from pathlib import Path


def configure_temp_state(tmp_dir: Path):
    import search_engine
    import storage
    import fine_tuning

    storage_file = tmp_dir / 'storage.json'
    vector_db_file = tmp_dir / 'vector_db.npz'
    cache_file = tmp_dir / 'search_cache.pkl'
    adaptation_dir = tmp_dir / 'fine_tuned_literary_model'
    adaptation_manifest = tmp_dir / 'fine_tuned_literary_model_manifest.json'
    uploads_dir = tmp_dir / 'uploads'
    uploads_dir.mkdir(exist_ok=True)

    search_engine.STORAGE_FILE = str(storage_file)
    search_engine.VECTOR_DB_FILE = str(vector_db_file)
    search_engine.CACHE_FILE = str(cache_file)
    search_engine.FINE_TUNED_MODEL_PATH = str(adaptation_dir)
    search_engine._INDEX_CACHE = {'mtime': None, 'data': []}
    search_engine._VECTOR_CACHE = {'mtime': None, 'embeddings': None, 'metadata': None}
    search_engine._RUNTIME_CACHE = {'signature': None, 'runtime': None}
    search_engine._LOOKUP_CACHE = {'by_id': {}, 'title_candidates': [], 'title_keys_by_id': {}}
    search_engine.reset_runtime_state()
    search_engine.clear_cache()

    storage.STORAGE_FILE = storage_file
    storage.UPLOADS_DIR = uploads_dir

    fine_tuning.ADAPTATION_MANIFEST_PATH = adaptation_manifest
    fine_tuning.FINE_TUNED_MODEL_PATH = str(adaptation_dir)

    return {
        'storage_file': storage_file,
        'vector_db_file': vector_db_file,
        'cache_file': cache_file,
    }



def seed_library(tmp_dir: Path):
    from search_engine import add_to_index

    books = [
        {
            'id': 'book_1',
            'title': 'Тайна старого дома',
            'content': (
                'Старый дом стоял на окраине города. Внутри царила мрачная атмосфера, '
                'а герой шаг за шагом раскрывал тайну исчезновения семьи. '
                'Расследование, тревога и загадка сопровождали каждую главу.'
            ),
        },
        {
            'id': 'book_2',
            'title': 'Светлая дорога',
            'content': (
                'Это история про путешествие, дружбу и преодоление. '
                'Герои идут по дороге, поддерживают друг друга и находят надежду.'
            ),
        },
        {
            'id': 'book_3',
            'title': 'Доктор Живаго',
            'content': (
                'Роман о судьбе человека, любви, исторических потрясениях и внутреннем выборе. '
                'Главный герой переживает тяжелые события и сохраняет человечность.'
            ),
        },
    ]

    for book in books:
        file_stub = tmp_dir / f"{book['id']}.txt"
        file_stub.write_text(book['content'], encoding='utf-8')
        payload = {
            'title': book['title'],
            'content': book['content'],
            'format': 'txt',
            'file_path': str(file_stub),
            'source_filename': file_stub.name,
            'cache_path': str(tmp_dir / f"{book['id']}.cache.txt"),
            'metadata': {'ingest_origin': 'test'},
            'file_hash': book['id'],
        }
        record = add_to_index(payload, file_id=book['id'])
        assert record is not None, f"Failed to index {book['title']}"



def test_lite_mode():
    os.environ['NOVELLECT_SEARCH_MODE'] = 'lite'
    os.environ['NOVELLECT_USE_STUB_LLM'] = '1'
    os.environ['NOVELLECT_LLM_MODEL_ID'] = 'stub'

    from llm_runtime import reset_llm_state
    from agents import AgentOrchestrator

    reset_llm_state()
    response = AgentOrchestrator().process_query('хочу книгу с тайной и мрачной атмосферой')
    assert response.get('mode') == 'lite_pipeline', response
    assert response.get('type') == 'recommendation', response
    titles = [item['title'] for item in response.get('recommendations', [])]
    assert any('Тайна старого дома' == title for title in titles), titles
    print('[OK] lite mode search')



def test_full_mode():
    os.environ['NOVELLECT_SEARCH_MODE'] = 'full'
    os.environ['NOVELLECT_USE_STUB_LLM'] = '1'
    os.environ['NOVELLECT_LLM_MODEL_ID'] = 'stub'

    from llm_runtime import reset_llm_state
    from agents import AgentOrchestrator

    reset_llm_state()
    response = AgentOrchestrator().process_query('хочу книгу с тайной и мрачной атмосферой')
    assert response.get('mode') == 'full_llm_agent', response
    assert response.get('sources'), response
    assert '[1]' in response.get('answer', ''), response.get('answer', '')
    top_titles = [item['title'] for item in response.get('sources', [])]
    assert any('Тайна старого дома' == title for title in top_titles), top_titles
    print('[OK] full mode search')


def test_full_mode_filters_prompt_echo():
    os.environ['NOVELLECT_SEARCH_MODE'] = 'full'
    os.environ['NOVELLECT_USE_STUB_LLM'] = '0'
    os.environ['NOVELLECT_LLM_MODEL_ID'] = 'stub'

    from llm_runtime import reset_llm_state
    from agents import AgentOrchestrator

    reset_llm_state()
    planner_payload = json.dumps(
        {
            'strategy': 'stub-planner',
            'query_type': 'recommendation',
            'search_queries': ['тайна мрачная атмосфера'],
            'needs_clarification': False,
            'clarification_question': '',
            'answer_style': 'grounded_summary',
        },
        ensure_ascii=False,
    )
    bad_answer = (
        'SOURCE [1] TITLE::Тайна старого дома FORMAT::txt '
        'SNIPPET::Старый дом стоял на окраине города. Старый дом стоял на окраине города. '
        'Старый дом стоял на окраине города.'
    )

    with patch('agents.generate_text', side_effect=[planner_payload, bad_answer]):
        response = AgentOrchestrator().process_query('хочу книгу с тайной и мрачной атмосферой')

    answer = response.get('answer', '')
    assert response.get('mode') == 'full_llm_agent', response
    assert response.get('sources'), response
    assert 'SOURCE [' not in answer, answer
    assert 'TITLE::' not in answer, answer
    assert 'SNIPPET::' not in answer, answer
    assert '[1]' in answer, answer
    print('[OK] full mode answer sanitization')



def test_adaptation_preview():
    from fine_tuning import preview_corpus_adaptation

    preview = preview_corpus_adaptation(max_pairs=16)
    assert preview['total_pairs'] > 0, preview
    print('[OK] adaptation preview')



def main():
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.chdir(project_root)

    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(tmp_dir)
        seed_library(tmp_dir)
        test_lite_mode()
        test_full_mode()
        test_full_mode_filters_prompt_echo()
        test_adaptation_preview()
    print('[OK] smoke test finished')


if __name__ == '__main__':
    main()
