import asyncio
import importlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx


def configure_temp_state(project_root: Path, tmp_dir: Path):
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    os.chdir(project_root)
    os.environ['NOVELLECT_DATA_DIR'] = str(tmp_dir / 'data')
    os.environ['NOVELLECT_STORAGE_FILE'] = str(tmp_dir / 'storage.json')
    os.environ['NOVELLECT_VECTOR_DB_FILE'] = str(tmp_dir / 'vector_db.npz')
    os.environ['NOVELLECT_VECTOR_MANIFEST_FILE'] = str(tmp_dir / 'data' / 'vector_manifest.json')
    os.environ['NOVELLECT_VECTOR_SEGMENTS_DIR'] = str(tmp_dir / 'data' / 'vector_segments')
    os.environ['NOVELLECT_CHUNK_DB_FILE'] = str(tmp_dir / 'data' / 'chunk_store.sqlite')
    os.environ['NOVELLECT_SEARCH_CACHE_FILE'] = str(tmp_dir / 'search_cache.pkl')
    os.environ['NOVELLECT_TXT_CACHE_DIR'] = str(tmp_dir / 'txt_cache')
    os.environ['NOVELLECT_UPLOADS_DIR'] = str(tmp_dir / 'uploads')
    os.environ['NOVELLECT_MIGRATE_LEGACY_RUNTIME'] = '0'
    os.environ['INGESTION_ENABLED'] = '0'
    import converter
    import dataset_ingestion
    import fine_tuning
    import recommendation_service
    import service_paths
    import service_settings
    import search_engine
    import storage
    importlib.reload(service_paths)
    importlib.reload(service_settings)
    importlib.reload(converter)
    importlib.reload(dataset_ingestion)
    importlib.reload(recommendation_service)

    storage_file = tmp_dir / 'storage.json'
    vector_db_file = tmp_dir / 'vector_db.npz'
    cache_file = tmp_dir / 'search_cache.pkl'
    uploads_dir = tmp_dir / 'uploads'
    adaptation_dir = tmp_dir / 'fine_tuned_literary_model'
    uploads_dir.mkdir(exist_ok=True)
    search_engine.STORAGE_FILE = str(storage_file)
    search_engine.VECTOR_DB_FILE = str(vector_db_file)
    search_engine.CACHE_FILE = str(cache_file)
    search_engine.FINE_TUNED_MODEL_PATH = str(adaptation_dir)
    search_engine._INDEX_CACHE = {'mtime': None, 'data': []}
    search_engine._VECTOR_CACHE = {'mtime': None, 'embeddings': None, 'metadata': None}
    search_engine._RUNTIME_CACHE = {'signature': None, 'runtime': None, 'source_mtimes': None}
    search_engine._LOOKUP_CACHE = {'by_id': {}, 'title_candidates': [], 'title_keys_by_id': {}}
    search_engine.reset_runtime_state()
    search_engine.clear_cache()
    storage.STORAGE_FILE = storage_file
    storage.UPLOADS_DIR = uploads_dir
    fine_tuning.FINE_TUNED_MODEL_PATH = str(adaptation_dir)
    os.environ['NOVELLECT_PROFILES_FILE'] = str(tmp_dir / 'profiles.json')
    os.environ['NOVELLECT_INTERACTIONS_FILE'] = str(tmp_dir / 'events.json')
    os.environ['NOVELLECT_SESSIONS_FILE'] = str(tmp_dir / 'sessions.json')
    os.environ['NOVELLECT_SEARCH_MODE'] = 'lite'
    os.environ['NOVELLECT_USE_STUB_LLM'] = '1'
    os.environ['NOVELLECT_LLM_MODEL_ID'] = 'stub'


def seed_library(tmp_dir: Path):
    from search_engine import add_to_index

    books = [
        ('book_1', 'Тайна старого дома', 'Мрачная усадьба, тайна исчезновения семьи, тревожная атмосфера, расследование и секреты.', {'author': 'Анна Сумрак', 'genres': ['детектив', 'готика'], 'cover_url': 'https://example.test/book1.jpg'}),
        ('book_2', 'Светлая дорога', 'Путешествие, дружба, надежда, теплая атмосфера и преодоление трудностей.', {'author': 'Илья Рассвет', 'genres': ['приключение'], 'cover_url': 'https://example.test/book2.jpg'}),
        ('book_3', 'Доктор Живаго', 'История любви, тяжелого выбора, философских размышлений и исторических потрясений.', {'author': 'Борис Пастернак', 'genres': ['исторический роман'], 'cover_url': 'https://example.test/book3.jpg'}),
        ('book_4', 'Замок тумана', 'Таинственный замок, мрачный тон, тревога, скрытый конфликт и расследование древнего секрета.', {'author': 'Марта Нокс', 'genres': ['детектив', 'готика'], 'cover_url': 'https://example.test/book4.jpg'}),
    ]
    for book_id, title, content, metadata in books:
        file_stub = tmp_dir / f'{book_id}.txt'
        file_stub.write_text(content, encoding='utf-8')
        payload = {
            'title': title,
            'content': content,
            'format': 'txt',
            'file_path': str(file_stub),
            'source_filename': file_stub.name,
            'cache_path': str(tmp_dir / f'{book_id}.cache.txt'),
            'metadata': {'ingest_origin': 'test', **metadata},
            'file_hash': book_id,
        }
        assert add_to_index(payload, file_id=book_id) is not None


def seed_classics_library(tmp_dir: Path):
    from search_engine import add_to_index

    books = [
        (
            'crime_punishment',
            'Преступление и наказание',
            (
                'Раскольников, бедный студент, убил старуху-процентщицу и ее сестру. '
                'После преступления его мучают вина, совесть, страх и раскаяние. '
                'Роман исследует пороки человечества, нравственное падение, бедность и искупление.'
            ),
            {'author': 'Федор Достоевский', 'genres': ['роман', 'психологический роман']},
        ),
        (
            'dead_souls',
            'Мертвые души',
            (
                'Чичиков ездит по помещикам и скупает мертвые души по ревизским сказкам. '
                'Это афера о чиновниках, помещиках, бюрократии и социальной сатире.'
            ),
            {'author': 'Николай Гоголь', 'genres': ['поэма', 'сатира']},
        ),
        (
            'mumu',
            'Муму',
            (
                'Герасим любит собаку Муму, но по приказу барыни вынужден утопить собаку в реке. '
                'Повесть показывает жестокость, сострадание, бесправие и унижение.'
            ),
            {'author': 'Иван Тургенев', 'genres': ['повесть', 'классика']},
        ),
        (
            'decoy_book',
            'Зимний берег',
            (
                'Мрачная семейная история о тяжелом выборе, ссорах и одиночестве. '
                'В книге нет Раскольникова, ревизских сказок и истории с собакой.'
            ),
            {'author': 'Елена Север', 'genres': ['драма']},
        ),
    ]

    for book_id, title, content, metadata in books:
        file_stub = tmp_dir / f'{book_id}.txt'
        file_stub.write_text(content, encoding='utf-8')
        payload = {
            'title': title,
            'content': content,
            'format': 'txt',
            'file_path': str(file_stub),
            'source_filename': file_stub.name,
            'cache_path': str(tmp_dir / f'{book_id}.cache.txt'),
            'metadata': {'ingest_origin': 'test', **metadata},
            'file_hash': book_id,
        }
        assert add_to_index(payload, file_id=book_id) is not None


async def request_json(app, method: str, path: str, json_payload=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://testserver') as client:
        response = await client.request(method, path, json=json_payload)
    return response


def test_profile_and_feed_end_to_end():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_library(tmp_dir)
        import main
        importlib.reload(main)

        async def scenario():
            onboarding = await request_json(main.app, 'POST', '/api/v1/profile/user-1/onboarding', {
                'favorite_moods': ['тревожное'],
                'favorite_atmosphere': ['таинственная', 'мрачная'],
                'favorite_plot': ['тайна'],
                'favorite_books': ['Тайна старого дома'],
                'favorite_genres': ['детектив'],
                'favorite_authors': ['Анна Сумрак'],
            })
            assert onboarding.status_code == 200
            assert onboarding.json()['dominant_taste']

            interaction = await request_json(main.app, 'POST', '/api/v1/interactions/ingest', {
                'user_id': 'user-1', 'book_id': 'book_1', 'action': 'like', 'session_id': 's-1'
            })
            assert interaction.status_code == 200

            feed = await request_json(main.app, 'POST', '/api/v1/recommendations/feed', {
                'user_id': 'user-1', 'mode': 'similar', 'recommendation_context': 'profile', 'limit': 3, 'session_id': 's-1'
            })
            assert feed.status_code == 200
            payload = feed.json()
            items = payload['items']
            assert items
            assert payload['recommendation_context'] == 'profile'
            assert 'book_4' in [item['book_id'] for item in items[:2]]
            first = items[0]
            for key in ('author', 'cover_url', 'genres', 'moods', 'short_summary', 'preview_excerpt', 'why_for_you', 'read_url', 'actions'):
                assert key in first
            assert first['recommendation_context'] == 'profile'
            assert first['explanation_mode'] == 'profile_explanation'

            preview = await request_json(main.app, 'GET', f"/api/v1/books/{first['book_id']}/preview")
            assert preview.status_code == 200
            assert preview.json()['preview_excerpt']

            content = await request_json(main.app, 'GET', f"/api/v1/books/{first['book_id']}/content")
            assert content.status_code == 200
            assert content.json()['content_length'] >= 0

        asyncio.run(scenario())


def test_search_endpoint_reuses_agents():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_library(tmp_dir)
        import main
        importlib.reload(main)

        async def scenario():
            response = await request_json(main.app, 'POST', '/api/v1/search/query', {'query': 'хочу книгу с тайной и мрачной атмосферой'})
            assert response.status_code == 200
            payload = response.json()
            assert payload['mode'] in {'lite_pipeline', 'full_llm_agent'}

        asyncio.run(scenario())


def test_hybrid_search_understands_entities_scenes_and_themes():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_classics_library(tmp_dir)
        import agents
        import search_engine
        importlib.reload(search_engine)
        importlib.reload(agents)

        analyzer = agents.QueryAnalyzerAgent()

        def top_ids(query: str, limit: int = 3):
            analysis = analyzer.analyze(query)
            results = search_engine.search_hybrid(
                analysis['search_text'],
                top_k=limit,
                query_profile=analysis,
            )
            return analysis, [item['book_id'] for item in results]

        analysis, ids = top_ids('Раскольников', limit=1)
        assert analysis['type'] == 'entity'
        assert ids[0] == 'crime_punishment'

        analysis, ids = top_ids('человек убил процентщицу', limit=1)
        assert analysis['type'] in {'scene', 'specific', 'mixed'}
        assert ids[0] == 'crime_punishment'

        analysis, ids = top_ids('ревизские сказки', limit=1)
        assert ids[0] == 'dead_souls'

        analysis, ids = top_ids('утопил собаку', limit=1)
        assert ids[0] == 'mumu'

        analysis, ids = top_ids('мужчина утопил собаку', limit=1)
        assert ids[0] == 'mumu'

        analysis, ids = top_ids('хочу книгу про пороки человечества', limit=3)
        assert analysis['type'] in {'theme', 'recommendation', 'mixed'}
        assert 'crime_punishment' in ids[:3]


def test_semantic_passport_surfaces_in_card_tags():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_classics_library(tmp_dir)
        import book_metadata
        import search_engine
        importlib.reload(search_engine)
        importlib.reload(book_metadata)

        book = next(item for item in search_engine.load_index(force=True) if item['id'] == 'crime_punishment')
        tags = book_metadata.get_hybrid_tags(book)

        assert tags
        assert any(tag in tags for tag in ('вина и раскаяние', 'пороки человечества', 'бедность и унижение'))


def test_mumu_passport_does_not_pick_revision_souls_artifacts():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_classics_library(tmp_dir)
        import search_engine
        importlib.reload(search_engine)

        book = next(item for item in search_engine.load_index(force=True) if item['id'] == 'mumu')
        passport = dict(book.get('semantic_passport') or {})

        assert 'утопление' in (passport.get('scene_hints') or [])
        assert 'жестокость и сострадание' in (passport.get('themes') or [])
        assert 'убийство' not in (passport.get('scene_hints') or [])
        assert 'афера с ревизскими душами' not in (passport.get('scene_hints') or [])
        assert 'бюрократия и афера' not in (passport.get('themes') or [])
        assert 'социальная сатира' not in (passport.get('themes') or [])


def test_runtime_refreshes_after_new_book_without_manual_reset():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        os.environ['NOVELLECT_FREEZE_RUNTIME_SNAPSHOT'] = '1'
        import search_engine
        importlib.reload(search_engine)

        first_path = tmp_dir / 'first.txt'
        first_path.write_text('мрачный дом тайна секрет дождь ночь', encoding='utf-8')
        assert search_engine.add_to_index(
            {
                'title': 'Первый роман',
                'content': first_path.read_text(encoding='utf-8'),
                'format': 'txt',
                'file_path': str(first_path),
                'source_filename': first_path.name,
                'metadata': {'author': 'Автор Первый'},
                'file_hash': 'first',
            },
            file_id='first',
        ) is not None

        assert search_engine.search_hybrid('мрачный дом', top_k=1)[0]['book_id'] == 'first'
        search_engine.warm_runtime()

        second_path = tmp_dir / 'second.txt'
        second_path.write_text('солнечная дорога дружба надежда путешествие тепло', encoding='utf-8')
        assert search_engine.add_to_index(
            {
                'title': 'Второй роман',
                'content': second_path.read_text(encoding='utf-8'),
                'format': 'txt',
                'file_path': str(second_path),
                'source_filename': second_path.name,
                'metadata': {'author': 'Автор Второй'},
                'file_hash': 'second',
            },
            file_id='second',
        ) is not None

        refreshed = search_engine.search_hybrid('солнечная дорога', top_k=1)
        assert refreshed
        assert refreshed[0]['book_id'] == 'second'


def test_feed_pagination_and_excludes_are_stable():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_library(tmp_dir)
        import main
        importlib.reload(main)

        async def scenario():
            first_page = await request_json(main.app, 'POST', '/api/v1/recommendations/feed', {
                'user_id': 'user-2',
                'mode': 'similar',
                'recommendation_context': 'query',
                'limit': 2,
                'session_id': 's-2',
                'query': 'Хочу мрачную книгу с тайной',
            })
            assert first_page.status_code == 200
            first_payload = first_page.json()
            assert first_payload['count'] == 2
            first_ids = [item['book_id'] for item in first_payload['items']]
            assert len(set(first_ids)) == 2

            second_page = await request_json(main.app, 'POST', '/api/v1/recommendations/feed', {
                'user_id': 'user-2',
                'mode': 'similar',
                'recommendation_context': 'query',
                'limit': 2,
                'session_id': 's-2',
                'query': 'Хочу мрачную книгу с тайной',
                'cursor': first_payload['cursor'],
            })
            assert second_page.status_code == 200
            second_payload = second_page.json()
            second_ids = [item['book_id'] for item in second_payload['items']]
            assert not (set(first_ids) & set(second_ids))

            excluded = await request_json(main.app, 'POST', '/api/v1/recommendations/feed', {
                'user_id': 'user-2',
                'mode': 'similar',
                'recommendation_context': 'query',
                'limit': 3,
                'session_id': 's-2',
                'query': 'Хочу мрачную книгу с тайной',
                'exclude_book_ids': [first_ids[0]],
            })
            assert excluded.status_code == 200
            excluded_ids = [item['book_id'] for item in excluded.json()['items']]
            assert first_ids[0] not in excluded_ids

        asyncio.run(scenario())


def test_understand_query_extracts_avoid_signal():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        import ai_layer
        importlib.reload(ai_layer)

        payload = ai_layer.understand_query('Хочу мрачную книгу с тайной, но без романтики')

        assert payload['intent']
        assert 'романтика' in [item.lower() for item in payload['avoid']]


def test_query_mode_without_onboarding_returns_results():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_library(tmp_dir)
        import main
        importlib.reload(main)

        async def scenario():
            feed = await request_json(main.app, 'POST', '/api/v1/recommendations/feed', {
                'user_id': 'user-4',
                'mode': 'similar',
                'recommendation_context': 'query',
                'limit': 3,
                'session_id': 's-4',
                'query': 'Хочу мрачную книгу с тайной и без романтики',
            })
            assert feed.status_code == 200
            payload = feed.json()
            items = payload['items']

            assert items
            assert items[0]['book_id'] in {'book_1', 'book_4'}
            assert payload['recommendation_context'] == 'query'
            assert items[0]['recommendation_context'] == 'query'
            assert items[0]['explanation_mode'] == 'query_explanation'
            assert 'романтика' in [item.lower() for item in payload['query_understanding']['avoid']]

        asyncio.run(scenario())


def test_query_mode_prioritizes_query_over_profile_signals():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_library(tmp_dir)
        import main
        importlib.reload(main)

        async def scenario():
            onboarding = await request_json(main.app, 'POST', '/api/v1/profile/user-5/onboarding', {
                'favorite_moods': ['светлое'],
                'favorite_atmosphere': ['теплая'],
                'favorite_plot': ['путешествие'],
                'favorite_books': ['Светлая дорога'],
                'favorite_genres': ['приключение'],
                'favorite_authors': ['Илья Рассвет'],
            })
            assert onboarding.status_code == 200

            feed = await request_json(main.app, 'POST', '/api/v1/recommendations/feed', {
                'user_id': 'user-5',
                'mode': 'similar',
                'recommendation_context': 'query',
                'limit': 3,
                'session_id': 's-5',
                'query': 'Хочу мрачную книгу с тайной и без романтики',
            })
            assert feed.status_code == 200
            payload = feed.json()
            items = payload['items']

            assert items
            assert items[0]['book_id'] in {'book_1', 'book_4'}
            assert 'Илья Рассвет' not in payload['resolved_query']
            assert 'приключение' not in payload['resolved_query'].lower()
            assert payload['recommendation_context'] == 'query'

        asyncio.run(scenario())


def test_query_mode_scene_query_keeps_dead_souls_first_for_revision_tales():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_classics_library(tmp_dir)
        import main
        importlib.reload(main)

        async def scenario():
            feed = await request_json(main.app, 'POST', '/api/v1/recommendations/feed', {
                'user_id': 'user-7',
                'mode': 'similar',
                'recommendation_context': 'query',
                'limit': 3,
                'session_id': 's-7',
                'query': 'фантастический триллер о ревизских сказках',
            })
            assert feed.status_code == 200
            payload = feed.json()
            items = payload['items']

            assert items
            assert items[0]['book_id'] == 'dead_souls'
            assert payload['resolved_query'] == 'фантастический триллер о ревизских сказках'
            assert 'бюрократия и афера' in [item.lower() for item in payload['query_understanding']['themes']]

        asyncio.run(scenario())


def test_explanations_differ_by_recommendation_context():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        seed_library(tmp_dir)
        import main
        importlib.reload(main)

        async def scenario():
            onboarding = await request_json(main.app, 'POST', '/api/v1/profile/user-6/onboarding', {
                'favorite_moods': ['тревожное'],
                'favorite_atmosphere': ['таинственная', 'мрачная'],
                'favorite_plot': ['тайна'],
                'favorite_books': ['Тайна старого дома'],
                'favorite_genres': ['детектив'],
                'favorite_authors': ['Анна Сумрак'],
            })
            assert onboarding.status_code == 200

            profile_feed = await request_json(main.app, 'POST', '/api/v1/recommendations/feed', {
                'user_id': 'user-6',
                'mode': 'similar',
                'recommendation_context': 'profile',
                'limit': 3,
                'session_id': 's-6',
            })
            query_feed = await request_json(main.app, 'POST', '/api/v1/recommendations/feed', {
                'user_id': 'user-6',
                'mode': 'similar',
                'recommendation_context': 'query',
                'limit': 3,
                'session_id': 's-6',
                'query': 'мрачная книга с тайной',
            })

            assert profile_feed.status_code == 200
            assert query_feed.status_code == 200

            profile_item = profile_feed.json()['items'][0]
            query_item = query_feed.json()['items'][0]

            assert profile_item['explanation_mode'] == 'profile_explanation'
            assert query_item['explanation_mode'] == 'query_explanation'
            assert profile_item['match_reason_context'] == 'profile'
            assert query_item['match_reason_context'] == 'query'

        asyncio.run(scenario())


def test_profile_store_falls_back_to_json_when_postgres_unavailable():
    project_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp_dir = Path(tmp_name)
        configure_temp_state(project_root, tmp_dir)
        import profile_store
        from service_settings import ServiceSettings

        settings = ServiceSettings(
            profiles_file=tmp_dir / 'profiles.json',
            events_file=tmp_dir / 'events.json',
            sessions_file=tmp_dir / 'sessions.json',
            qdrant_url='',
            qdrant_timeout_sec=5,
            qdrant_book_collection='books',
            qdrant_user_collection='users',
            cache_ttl_sec=300,
            profile_store_backend='postgres',
            postgres_host='127.0.0.1',
            postgres_port=5432,
            postgres_user='novellect',
            postgres_password='novellect',
            postgres_database='novellect',
            postgres_timeout_sec=1,
        )
        store = profile_store.build_profile_store(settings)
        payload = {'user_id': 'user-3', 'positive_books': ['book_1']}

        with patch('profile_store._import_psycopg', side_effect=RuntimeError('psycopg unavailable')):
            store.upsert('user-3', payload)
            loaded = store.get('user-3')

        assert loaded == payload
