import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from llm_runtime import generate_text, llm_status, safe_json_loads
from runtime_config import get_compute_profile
from search_engine import (
    QueryProfile,
    _sanitize_search_text,
    find_title_matches,
    get_query_analyzer,
    load_index,
    search_hybrid,
    tokenize_smart,
)

_query_analyzer = get_query_analyzer()



def _empty_feature_flags():
    return {'mood': False, 'style': False, 'plot': False, 'atmosphere': False, 'tone': False}



def _dedupe_texts(values: List[str], limit: int = 3) -> List[str]:
    result = []
    seen = set()
    for value in values or []:
        normalized = (value or '').strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result



class QueryAnalyzerAgent:
    """Определяет тип запроса и готовит безопасный search text без шумного expansion."""

    RECOMMENDATION_MARKERS = (
        'хочу', 'посоветуй', 'подбери', 'что почитать', 'интересуют', 'что-то', 'что то',
        'мрач', 'тревож', 'атмосфер', 'настроен', 'сюжет', 'похож', 'напомина',
    )
    SPECIFIC_PATTERNS = [
        r'\bв какой книге\b',
        r'\bв каком произведении\b',
        r'\bгде есть\b',
        r'\bкто такой\b',
        r'\bкто такая\b',
        r'\bкак зовут\b',
        r'\bнайди книгу где\b',
    ]

    def __init__(self):
        self.stop_words = {
            'про', 'книгу', 'книге', 'найди', 'хочу', 'посоветуй', 'что', 'где', 'как', 'кто', 'в', 'на', 'с', 'о',
            'или', 'и', 'мне', 'что-то', 'что', 'то', 'книга', 'роман', 'повесть', 'рассказ',
        }

    def _extract_keywords(self, query: str) -> List[str]:
        words = re.findall(r'\w+', (query or '').lower())
        return [word for word in words if word not in self.stop_words and len(word) > 2]

    def _extract_entities(self, query: str) -> Dict[str, List[str]]:
        entities = {'characters': [], 'places': [], 'concepts': []}
        matches = re.findall(r'\b[А-ЯA-Z][а-яa-zёЁ-]+(?:\s+[А-ЯA-Z][а-яa-zёЁ-]+)*\b', query)
        seen = set()
        for match in matches:
            value = match.strip()
            if value and value not in seen:
                seen.add(value)
                entities['characters'].append(value)
        return entities

    def _determine_type(self, original_query: str, cleaned_query: str, keywords: List[str], title_matches: List[dict], query_analysis: dict):
        lowered = original_query.lower()
        strong_title = title_matches and title_matches[0]['score'] >= 0.9
        soft_title = title_matches and title_matches[0]['score'] >= 0.8

        if strong_title and len(keywords) <= 5 and not any(marker in lowered for marker in self.RECOMMENDATION_MARKERS):
            return 'title'

        if any(re.search(pattern, lowered) for pattern in self.SPECIFIC_PATTERNS):
            return 'specific'

        if soft_title and len(keywords) <= 4 and not any(marker in lowered for marker in self.RECOMMENDATION_MARKERS):
            return 'title'

        if any(marker in lowered for marker in self.RECOMMENDATION_MARKERS):
            return 'recommendation'

        if any(query_analysis.get(flag) for flag in ('has_mood', 'has_style', 'has_plot', 'has_atmosphere', 'has_tone')) and len(keywords) >= 3:
            return 'recommendation'

        if len(keywords) <= 2:
            return 'entity'

        if len(cleaned_query.split()) >= 5:
            return 'recommendation'

        return 'keyword'

    def analyze(self, query: str):
        original_query = (query or '').strip()
        cleaned_query = _sanitize_search_text(original_query)
        keywords = self._extract_keywords(cleaned_query or original_query)
        title_matches = find_title_matches(original_query, limit=5)
        entities = self._extract_entities(original_query)
        query_analysis = _query_analyzer.analyze_query(cleaned_query or original_query)
        query_type = self._determine_type(original_query, cleaned_query, keywords, title_matches, query_analysis)

        if query_type in {'title', 'entity', 'specific'}:
            priorities = {}
            has_features = _empty_feature_flags()
        else:
            priorities = query_analysis['priorities']
            has_features = {
                'mood': query_analysis['has_mood'],
                'style': query_analysis['has_style'],
                'plot': query_analysis['has_plot'],
                'atmosphere': query_analysis['has_atmosphere'],
                'tone': query_analysis['has_tone'],
            }

        return {
            'original_query': original_query,
            'search_text': cleaned_query or original_query,
            'type': query_type,
            'keywords': keywords,
            'entities': entities,
            'query_analysis': query_analysis,
            'priorities': priorities,
            'title_matches': title_matches,
            'title_like': query_type == 'title',
            'require_exact': query_type in {'title', 'entity', 'specific'},
            'has_features': has_features,
        }



class RetrievalAgent:
    def search(self, analysis: dict, top_k: int = 10):
        profile = QueryProfile(
            original_query=analysis['original_query'],
            search_text=analysis['search_text'],
            query_type=analysis['type'],
            keywords=analysis.get('keywords', []),
            priorities=analysis.get('priorities', {}),
            has_features=analysis.get('has_features', {}),
            title_matches=analysis.get('title_matches', []),
            title_like=analysis.get('title_like', False),
            require_exact=analysis.get('require_exact', False),
        )
        title_boosts = {item['book_id']: float(item['score']) for item in analysis.get('title_matches', [])}
        return search_hybrid(
            analysis['search_text'],
            top_k=top_k,
            title_boosts=title_boosts or None,
            query_profile={
                'type': profile.query_type,
                'search_text': profile.search_text,
                'original_query': profile.original_query,
                'keywords': profile.keywords,
                'priorities': profile.priorities,
                'has_features': profile.has_features,
                'title_like': profile.title_like,
                'require_exact': profile.require_exact,
            },
        )



class RankingAgent:
    def rank(self, results: List[dict], analysis: dict):
        return results



class ResponseAgent:
    def __init__(self):
        self.index = load_index()

    def _get_book_info(self, book_id: str):
        if not self.index:
            self.index = load_index()
        book = next((book for book in self.index if book.get('id') == book_id), None)
        if book is None:
            self.index = load_index(force=True)
            book = next((book for book in self.index if book.get('id') == book_id), None)
        return book

    def serialize_results(self, results: List[dict], limit: int = 5):
        payload = []
        for result in results[:limit]:
            payload.append(
                {
                    'title': result['title'],
                    'format': result.get('format', 'unknown'),
                    'snippet': result.get('snippet', ''),
                    'relevance': float(result.get('similarity', 0.0)),
                    'matched_queries': list(result.get('seen_in_queries', [])),
                    'exact_match': bool(result.get('title_match_score', 0) >= 0.95 or result.get('coverage_score', 0) >= 0.95),
                }
            )
        return payload

    def _recommendation_response(self, results: List[dict], analysis: dict):
        payload = {
            'type': 'recommendation',
            'query': analysis['original_query'],
            'query_type': analysis['type'],
            'recommendations': [],
        }
        for result in results[:5]:
            book_info = self._get_book_info(result['book_id']) or {}
            feature_matches = []
            for _category, priorities in (analysis.get('priorities') or {}).items():
                category_scores = (book_info.get('features') or {}).get(_category, {})
                for feature_name, _weight in priorities[:2]:
                    score = float(category_scores.get(feature_name, 0.0))
                    if score >= 0.12:
                        feature_matches.append(f'{feature_name}: {score:.0%}')
            payload['recommendations'].append(
                {
                    'title': result['title'],
                    'format': result.get('format', 'unknown'),
                    'relevance_score': float(result['similarity']),
                    'snippets': result.get('snippets', [])[:2],
                    'feature_matches': feature_matches[:3],
                }
            )
        return payload

    def _result_response(self, results: List[dict], analysis: dict):
        response_type = analysis['type'] if analysis['type'] in {'title', 'entity', 'specific', 'keyword'} else 'general'
        payload = {
            'type': response_type,
            'query': analysis['original_query'],
            'query_type': analysis['type'],
            'results': [],
        }
        for result in results[:5]:
            payload['results'].append(
                {
                    'title': result['title'],
                    'format': result.get('format', 'unknown'),
                    'snippet': result.get('snippet', ''),
                    'relevance': float(result['similarity']),
                    'exact_match': bool(result.get('title_match_score', 0) >= 0.95 or result.get('coverage_score', 0) >= 0.95),
                }
            )
        return payload

    def _empty_message(self, analysis: dict):
        qtype = analysis.get('type', 'keyword')
        if qtype == 'title':
            return 'Точное название не найдено. Попробуйте другое написание или более короткую форму названия.'
        if qtype in {'entity', 'specific'}:
            return 'Точных совпадений по персонажу или фразе не найдено. Попробуйте указать больше контекста.'
        if qtype == 'recommendation':
            return 'Не удалось подобрать релевантные книги. Попробуйте уточнить тему, настроение или сюжет.'
        return 'Ничего не найдено. Попробуйте изменить формулировку запроса.'

    def format(self, results: List[dict], analysis: dict):
        if not results:
            return {
                'type': 'empty',
                'query': analysis['original_query'],
                'query_type': analysis['type'],
                'message': self._empty_message(analysis),
            }
        if analysis['type'] == 'recommendation':
            return self._recommendation_response(results, analysis)
        return self._result_response(results, analysis)



class PipelineAgentOrchestrator:
    def __init__(self):
        self.analyzer = QueryAnalyzerAgent()
        self.retriever = RetrievalAgent()
        self.ranker = RankingAgent()
        self.formatter = ResponseAgent()
        self.history = []

    def process_query(self, query: str, update_opened: bool = False):
        started = datetime.now()
        analysis = self.analyzer.analyze(query)
        results = self.retriever.search(analysis)
        ranked = self.ranker.rank(results, analysis)
        response = self.formatter.format(ranked, analysis)
        response['mode'] = 'lite_pipeline'
        response['trace'] = [
            'Анализ запроса эвристическим агентом',
            'Поиск по гибридному индексу (BM25 + эмбеддинги + rerank)',
            'Форматирование детерминированного ответа',
        ]
        self.history.append(
            {
                'query': query,
                'type': analysis['type'],
                'mode': 'lite',
                'timestamp': datetime.now().isoformat(timespec='seconds'),
                'latency_sec': round((datetime.now() - started).total_seconds(), 3),
            }
        )
        return response



class LLMPlannerAgent:
    ALLOWED_TYPES = {'title', 'entity', 'specific', 'keyword', 'recommendation'}

    def _heuristic_plan(self, analysis: dict) -> dict:
        search_queries = []
        base_query = analysis.get('search_text') or analysis.get('original_query')
        for candidate in [base_query]:
            candidate = _sanitize_search_text(candidate)
            if candidate and candidate not in search_queries:
                search_queries.append(candidate)

        title_matches = analysis.get('title_matches') or []
        if title_matches:
            candidate = title_matches[0].get('title', '').strip()
            if candidate and candidate not in search_queries:
                search_queries.append(candidate)

        keywords = analysis.get('keywords', [])
        keyword_query = ' '.join(keywords[:4]).strip()
        if keyword_query and keyword_query not in search_queries:
            search_queries.append(keyword_query)

        feature_terms = []
        for _category, values in (analysis.get('priorities') or {}).items():
            feature_terms.extend(name for name, _weight in values[:1])
        feature_query = ' '.join((feature_terms + keywords[:2])[:4]).strip()
        if feature_query and feature_query not in search_queries:
            search_queries.append(feature_query)

        ambiguous = len(keywords) <= 1 and not title_matches
        strategy = 'direct_lookup' if analysis.get('type') in {'title', 'entity', 'specific'} else 'iterative_retrieval'
        return {
            'strategy': strategy,
            'query_type': analysis.get('type', 'keyword'),
            'search_queries': search_queries[:3] or [analysis.get('original_query', '')],
            'needs_clarification': ambiguous,
            'clarification_question': 'Запрос слишком общий. Уточните героя, жанр или атмосферу.' if ambiguous else '',
            'answer_style': 'grounded_summary',
            'llm_used': False,
        }

    def _build_prompt(self, query: str, analysis: dict) -> str:
        title_hint = ', '.join(item.get('title', '') for item in analysis.get('title_matches', [])[:3])
        priorities = {
            category: [name for name, _weight in values[:2]] for category, values in (analysis.get('priorities') or {}).items()
        }
        prompt = {
            'task': 'PLAN_SEARCH',
            'instruction': (
                'Верни только JSON без пояснений. '
                'Ключи: strategy, query_type, search_queries, needs_clarification, clarification_question, answer_style. '
                'Допустимые query_type: title, entity, specific, keyword, recommendation. '
                'search_queries: до 3 коротких запросов на русском языке.'
            ),
            'user_query': query,
            'base_type': analysis.get('type', 'keyword'),
            'keywords': analysis.get('keywords', []),
            'title_hints': title_hint,
            'feature_priorities': priorities,
        }
        return (
            '### TASK: PLAN_SEARCH ###\n'
            f"<user_query>{prompt['user_query']}</user_query>\n"
            f"<base_type>{prompt['base_type']}</base_type>\n"
            f"<keywords>{json.dumps(prompt['keywords'], ensure_ascii=False)}</keywords>\n"
            f"<title_hints>{prompt['title_hints']}</title_hints>\n"
            f"<feature_priorities>{json.dumps(prompt['feature_priorities'], ensure_ascii=False)}</feature_priorities>\n"
            f"{prompt['instruction']}"
        )

    def plan(self, query: str, analysis: dict) -> dict:
        fallback = self._heuristic_plan(analysis)
        raw = generate_text(self._build_prompt(query, analysis), max_new_tokens=180, temperature=0.05)
        loaded = safe_json_loads(raw or '')
        if not loaded:
            return fallback

        query_type = str(loaded.get('query_type') or fallback['query_type']).strip().lower()
        if query_type not in self.ALLOWED_TYPES:
            query_type = fallback['query_type']

        search_queries = []
        for candidate in loaded.get('search_queries') or []:
            sanitized = _sanitize_search_text(str(candidate))
            if sanitized and sanitized not in search_queries:
                search_queries.append(sanitized)
        if not search_queries:
            search_queries = fallback['search_queries']

        return {
            'strategy': str(loaded.get('strategy') or fallback['strategy']).strip() or fallback['strategy'],
            'query_type': query_type,
            'search_queries': search_queries[:3],
            'needs_clarification': bool(loaded.get('needs_clarification', fallback['needs_clarification'])),
            'clarification_question': str(loaded.get('clarification_question') or fallback['clarification_question']).strip(),
            'answer_style': str(loaded.get('answer_style') or 'grounded_summary').strip(),
            'llm_used': True,
        }



class SearchToolAgent:
    def _profile_for_query(self, tool_query: str, base_analysis: dict, planned_type: str) -> dict:
        query_type = planned_type or base_analysis.get('type', 'keyword')
        safe_query = _sanitize_search_text(tool_query)
        is_recommendation = query_type == 'recommendation'
        return {
            'type': query_type,
            'search_text': safe_query,
            'original_query': base_analysis.get('original_query', tool_query),
            'keywords': tokenize_smart(safe_query)[:8],
            'priorities': base_analysis.get('priorities', {}) if is_recommendation else {},
            'has_features': base_analysis.get('has_features', {}) if is_recommendation else _empty_feature_flags(),
            'title_like': query_type == 'title',
            'require_exact': query_type in {'title', 'entity', 'specific'},
        }

    def _merge(self, batches: List[dict]) -> List[dict]:
        merged = {}
        for item in batches:
            book_id = item.get('book_id')
            if not book_id:
                continue
            if book_id not in merged:
                chosen = dict(item)
                chosen['seen_in_queries'] = _dedupe_texts([item.get('tool_query')], limit=3)
                chosen['snippets'] = _dedupe_texts(item.get('snippets', []) or [item.get('snippet', '')], limit=3)
                merged[book_id] = chosen
                continue

            current = merged[book_id]
            current['seen_in_queries'] = _dedupe_texts(current.get('seen_in_queries', []) + [item.get('tool_query')], limit=3)
            current['snippets'] = _dedupe_texts(current.get('snippets', []) + (item.get('snippets', []) or [item.get('snippet', '')]), limit=3)
            if float(item.get('similarity', 0.0)) > float(current.get('similarity', 0.0)):
                promoted = dict(item)
                promoted['seen_in_queries'] = current['seen_in_queries']
                promoted['snippets'] = current['snippets']
                merged[book_id] = promoted
        ranked = sorted(merged.values(), key=lambda item: float(item.get('similarity', 0.0)), reverse=True)
        return ranked

    def search(self, plan: dict, analysis: dict, top_k: int = 8):
        all_results = []
        traces = []
        for tool_query in plan.get('search_queries', [])[:3]:
            title_boosts = {item['book_id']: float(item['score']) for item in find_title_matches(tool_query, limit=5)}
            profile = self._profile_for_query(tool_query, analysis, plan.get('query_type', analysis.get('type', 'keyword')))
            batch = search_hybrid(
                tool_query,
                top_k=top_k,
                title_boosts=title_boosts or None,
                query_profile=profile,
            )
            traces.append({'query': tool_query, 'hits': len(batch)})
            for item in batch:
                enriched = dict(item)
                enriched['tool_query'] = tool_query
                all_results.append(enriched)
        return self._merge(all_results)[:top_k], traces



class GroundedAnswerAgent:
    _META_LINE_PREFIXES = ('### TASK:', 'SOURCE [', 'QUERY::', 'QUERY_TYPE::', 'PLAN::', 'TITLE::', 'FORMAT::', 'SNIPPET::')

    def __init__(self):
        self.formatter = ResponseAgent()

    @staticmethod
    def _sources_from_results(results: List[dict], limit: int = 4) -> List[dict]:
        sources = []
        for idx, result in enumerate(results[:limit], start=1):
            snippet = result.get('snippet') or (result.get('snippets') or [''])[0]
            sources.append(
                {
                    'source_id': idx,
                    'book_id': result.get('book_id'),
                    'title': result.get('title', 'Без названия'),
                    'format': result.get('format', 'unknown'),
                    'snippet': snippet,
                    'relevance': float(result.get('similarity', 0.0)),
                    'matched_queries': list(result.get('seen_in_queries', [])),
                }
            )
        return sources

    def _fallback_answer(self, query: str, results: List[dict], analysis: dict, plan: dict, sources: List[dict]) -> str:
        if not sources:
            return self.formatter._empty_message(analysis)

        top = sources[0]
        snippet = (top.get('snippet') or '').strip()
        if len(snippet) > 220:
            snippet = snippet[:217].rstrip() + '...'

        qtype = plan.get('query_type') or analysis.get('type', 'keyword')
        if qtype == 'recommendation':
            titles = ', '.join(f"**{item['title']}** [{item['source_id']}]" for item in sources[:3])
            return (
                f"По запросу лучше всего подходят {titles}. "
                f"Самое сильное совпадение — **{top['title']}** [{top['source_id']}], потому что в найденном фрагменте есть нужные мотивы: {snippet}"
            )

        tail = ''
        if len(sources) > 1:
            tail = ' Дополнительно можно проверить ' + ', '.join(
                f"**{item['title']}** [{item['source_id']}]" for item in sources[1:3]
            ) + '.'
        return (
            f"Наиболее релевантная книга — **{top['title']}** [{top['source_id']}]. "
            f"Основание: {snippet}.{tail}"
        )

    @staticmethod
    def _has_excessive_repetition(text: str) -> bool:
        sentences = [part.strip() for part in re.split(r'(?<=[.!?…])\s+', text) if len(part.strip()) >= 24]
        counts = {}
        for sentence in sentences:
            counts[sentence] = counts.get(sentence, 0) + 1
            if counts[sentence] >= 3:
                return True
        return False

    def _sanitize_generated_answer(self, raw: Optional[str], fallback: str, sources: List[dict]) -> str:
        text = (raw or '').strip()
        if not text:
            return fallback

        tagged = re.search(r'<answer>(.*?)</answer>', text, flags=re.IGNORECASE | re.DOTALL)
        if tagged:
            text = tagged.group(1).strip()

        cleaned_lines = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(self._META_LINE_PREFIXES):
                continue
            cleaned_lines.append(stripped)

        cleaned = ' '.join(cleaned_lines).strip()
        if not cleaned:
            return fallback

        if any(prefix in cleaned for prefix in self._META_LINE_PREFIXES):
            cleaned = re.sub(r'SOURCE \[\d+\]\s*TITLE::.*?(?=(?:SOURCE \[\d+\]|$))', ' ', cleaned, flags=re.DOTALL)
            cleaned = cleaned.replace('TITLE::', ' ')
            cleaned = cleaned.replace('FORMAT::', ' ')
            cleaned = cleaned.replace('SNIPPET::', ' ')
            cleaned = re.sub(r'(?:QUERY|QUERY_TYPE|PLAN)::.*', ' ', cleaned)
            cleaned = re.sub(r'###\s*TASK:.*', ' ', cleaned)
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()

        if not cleaned or any(prefix in cleaned for prefix in self._META_LINE_PREFIXES):
            return fallback

        if self._has_excessive_repetition(cleaned):
            return fallback

        for source in sources:
            snippet = (source.get('snippet') or '').strip()
            if snippet and cleaned == snippet:
                return fallback
        return cleaned

    def _build_prompt(self, query: str, analysis: dict, plan: dict, sources: List[dict]) -> str:
        source_lines = []
        for source in sources:
            snippet = (source.get('snippet') or '').replace('\n', ' ').strip()
            source_lines.append(
                f"SOURCE [{source['source_id']}] TITLE::{source['title']} FORMAT::{source['format']} SNIPPET::{snippet}"
            )
        return (
            '### TASK: GROUNDED_ANSWER ###\n'
            'Ты локальный ассистент по библиотеке. Отвечай только по источникам ниже, не выдумывай факты. '\
            'Верни только финальный ответ без служебных строк SOURCE, QUERY, PLAN. '\
            'Пиши по-русски, максимум 6 предложений, используй ссылки вида [1], [2].\n'
            f"QUERY::{query}\n"
            f"QUERY_TYPE::{plan.get('query_type') or analysis.get('type', 'keyword')}\n"
            f"PLAN::{json.dumps(plan, ensure_ascii=False)}\n"
            + '\n'.join(source_lines)
        )

    def answer(self, query: str, results: List[dict], analysis: dict, plan: dict) -> dict:
        sources = self._sources_from_results(results)
        fallback = self._fallback_answer(query, results, analysis, plan, sources)
        if not sources:
            return {'answer': fallback, 'sources': [], 'llm_used': False}

        raw = generate_text(self._build_prompt(query, analysis, plan, sources), max_new_tokens=220, temperature=0.1)
        answer = self._sanitize_generated_answer(raw, fallback, sources)
        if not any(f"[{source['source_id']}]" in answer for source in sources):
            answer = answer.rstrip() + f" [{sources[0]['source_id']}]"
        return {'answer': answer, 'sources': sources, 'llm_used': bool(raw)}



class LLMAgentOrchestrator:
    def __init__(self):
        self.analyzer = QueryAnalyzerAgent()
        self.planner = LLMPlannerAgent()
        self.search_tool = SearchToolAgent()
        self.answer_agent = GroundedAnswerAgent()
        self.formatter = ResponseAgent()
        self.history = []

    def process_query(self, query: str, update_opened: bool = False):
        started = datetime.now()
        analysis = self.analyzer.analyze(query)
        plan = self.planner.plan(query, analysis)
        results, search_trace = self.search_tool.search(plan, analysis, top_k=8)
        answer_payload = self.answer_agent.answer(query, results, analysis, plan)
        deterministic = self.formatter.format(results, analysis)

        response = {
            'type': 'llm_agent' if results else 'empty',
            'mode': 'full_llm_agent',
            'query': query,
            'query_type': plan.get('query_type', analysis.get('type', 'keyword')),
            'answer': answer_payload.get('answer', ''),
            'results': self.formatter.serialize_results(results, limit=5),
            'sources': answer_payload.get('sources', []),
            'plan': plan,
            'llm_used': bool(plan.get('llm_used')) or bool(answer_payload.get('llm_used')),
            'llm_status': llm_status(load=False),
            'fallback_payload': deterministic,
            'message': plan.get('clarification_question') if plan.get('needs_clarification') else '',
            'trace': [
                'Эвристический анализ запроса',
                'LLM-планирование маршрута поиска' if plan.get('llm_used') else 'Fallback-планирование маршрута поиска',
                'Инструментальный поиск по нескольким поисковым подзапросам',
                'Синтез ответа на основе найденных источников',
            ],
            'search_trace': search_trace,
        }

        if not results and deterministic.get('type') == 'empty':
            response['message'] = deterministic.get('message')

        self.history.append(
            {
                'query': query,
                'type': response['query_type'],
                'mode': 'full',
                'llm_used': response['llm_used'],
                'timestamp': datetime.now().isoformat(timespec='seconds'),
                'latency_sec': round((datetime.now() - started).total_seconds(), 3),
            }
        )
        return response



class AgentOrchestrator:
    def __init__(self):
        self.pipeline = PipelineAgentOrchestrator()
        self.llm_agent = LLMAgentOrchestrator()

    def process_query(self, query: str, update_opened: bool = False):
        mode = get_compute_profile().get('search_mode', 'lite')
        if mode == 'full':
            return self.llm_agent.process_query(query, update_opened=update_opened)
        return self.pipeline.process_query(query, update_opened=update_opened)



_orchestrator = AgentOrchestrator()



def process_with_agents(query: str):
    return _orchestrator.process_query(query)
