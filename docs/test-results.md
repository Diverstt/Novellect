# Результаты тестирования

## Что было проверено

### Go backend
1. `GOMAXPROCS=1 go test ./...`
2. `GOMAXPROCS=1 go test ./internal/app -run TestEndToEnd_PostgresRedisBackedFlow -v`

### Python recommendation service
1. `python tests/smoke_test.py`
2. `pytest -q tests/test_recommendation_api.py`

## Что покрывает end-to-end тест

`TestEndToEnd_PostgresRedisBackedFlow` поднимает:
- fake PostgreSQL server по wire protocol;
- fake Redis server по RESP;
- реальный Python recommendation service через `uvicorn main:app`;
- Go backend поверх настоящих `pgclient` и `redisclient`.

Дальше тест прогоняет сценарий:
- register;
- login;
- me;
- onboarding;
- get recommendations;
- like/save/skip/dislike;
- get updated recommendations;
- explain recommendation;
- reading lists;
- refresh token rotation.

Дополнительно проверяется, что:
- пользователи реально пишутся в PostgreSQL repository layer;
- refresh tokens реально хранятся и ротируются в PostgreSQL;
- recommendation events пишутся в PostgreSQL;
- interactions пишутся в PostgreSQL;
- Redis реально используется для cache invalidation по префиксам и хранения feed/profile/session state.

## Фактический статус

- Go tests: passed
- Python smoke test: passed
- Python API tests: passed
- Cross-service Go -> Python flow: passed

## Ограничение среды

В этой песочнице нет Docker daemon, PostgreSQL server и Redis server, поэтому `docker compose up --build` нельзя было реально исполнить здесь. По этой причине compose-файл подготовлен для локального запуска у пользователя, а проверка data-backed flow выполнена через protocol-level integration tests с real network clients.
