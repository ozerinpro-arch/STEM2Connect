# API v1 — общий контракт

Контракт проектный. Реализация ещё не готова. Разрешены обратимые уточнения при согласовании обеих сторон.
Префикс /api/v1. JSON UTF-8. Сохранение сессионных данных изолируется сервером через HttpOnly cookie. Клиент fetch с credentials: include. CORS только для согласованного frontend origin.

| Метод и путь | Вход | Выход |
|---|---|---|
| GET /health | — | {"status":"ok","api_version":"1"} |
| GET /examples | — | [{"id":"simpson-search","label":"Search benchmark","synthetic":true},{"id":"stable-search","label":"Stable comparison","synthetic":true}] |
| POST /datasets | multipart file CSV либо JSON {"example_id":"simpson-search"} | Dataset |
| GET /datasets/{id} | — | Dataset с preview максимум 20 строк |
| POST /plans | dataset_id, claim, outcome_column, group_column, stratum_column, positive_value, groups, target_weights | Plan с подтверждаемой постановкой |
| GET /plans/{id} | — | Plan |
| POST /runs | {"plan_id":"plan_demo"} | Run; завершённый расчёт либо status running |
| GET /runs/{id} | — | Run |
| POST /runs/{id}/explanation | — | Explanation либо error MODEL_UNAVAILABLE |
| GET /runs/{id}/export?include_data=true | — | application/zip |

Если run выполняется асинхронно, клиент опрашивает GET раз в 2 секунды не дольше 60 секунд, затем показывает продолжающийся расчёт и возможность вернуться. Изменение плана создаёт новый plan_id. POST runs с Idempotency-Key исключает дубли при повторе. Форматы объектов и примеры находятся в contracts/fixtures.

## Типы

Dataset {id,synthetic,row_count,columns:[{name,type,missing_count}],preview:[record]}.
Plan {id,dataset_id,claim,outcome_column,group_column,stratum_column,positive_value,groups:[string,string],target_weights:record,confirmed:true}.
Run {id,plan_id,status:running|completed|failed,dataset_hash,estimates:Estimate[],warnings:string[],explanation_status:not_requested|ready|failed,error:object|null}.
Estimate {id,estimand:observed|stratum|standardized,group,stratum:string|null,numerator:number|null,denominator:number|null,value:number,unit:proportion,formula:string}.
Explanation {run_id,summary,statements:[{text,estimate_ids:string[]}],limitations:string[]}.
Error {error:{code,message,details:object},request_id}. HTTP 400 для формата, 413 для лимита, 422 для неподдерживаемой схемы, 404 для отсутствующего или чужого ресурса, 429 для лимитов, 503 для модели.

CSV первой версии содержит system (A/B), difficulty (easy/hard), success (0/1). Свои колонки назначает пользователь.
Числа value передаются как доли 0..1. Frontend форматирует проценты. numerator/denominator у standardized равны null, размер смешанной популяции не выдумывать.
Графики разных estimand раздельны. Доверительные интервалы не показывать до появления согласованного метода и контрактного поля.
Экспорт содержит manifest.json, plan.json, results.json, requirements.txt, reproduce.py, report.html и при include_data=true data.csv.

При status running estimates=[] и error=null. При failed estimates=[] и error={code,message,details}. При completed estimates заполнен и error=null.

Примеры simpson-search используют dataset.json, plan.json, run.json. stable-search — stable-dataset.json, stable-plan.json, stable-run.json. Общее соответствие — examples.json. Для ожидания, ошибки и ИИ — run-running.json, run-failed.json, explanation.json, model-error.json.
