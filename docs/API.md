# Внутренний API

HTML-страницы и JSON API работают на одном origin через Django session cookie. Публичной регистрации нет. Все изменяющие запросы — POST с `X-CSRFToken` или полем `csrfmiddlewaretoken`. Сессионные cookie `HttpOnly`, `SameSite=Lax`, в production `Secure`. Неаутентифицированные запросы перенаправляются на вход; браузерный клиент обнаруживает это как завершение сессии.

| Метод / маршрут | Доступ | Результат |
|---|---|---|
| GET `/health/` | Без входа | Проверка соединения с БД, `{"status":"ok"}` |
| POST `/login/` | Без входа | `identifier`, `password`; сессия или ошибки формы |
| POST `/logout/` | По сессии | Завершение сессии |
| POST `/exams/{assignment}/instructions/` | Назначенный Student | `ready=yes`; возвращает попытку, не дублируя активную |
| GET `/api/attempts/{uuid}/state/` | Владелец | `status`, `index`, `server_now`, активные `deadline`, `revision` |
| POST `/api/attempts/{uuid}/answers/` | Владелец | JSON сохранения/отправки секции |
| POST `/api/attempts/{uuid}/listening/` | Владелец активной секции | Однократное разрешение воспроизведения, `url` |
| POST `/api/attempts/{uuid}/recordings/{question}/` | Владелец активного Speaking | multipart `file`, ответ `saved`, `url` |
| GET `/media/{uuid}/` | По принадлежности и состоянию | Приватный поток, поддержка Range и 206 |
| POST `/reviews/{run}/` | Назначенный Teacher / Admin | Критерии формы, `feedback`, `action=draft/final` |
| POST `/results/{uuid}/publish/` | Admin | Публикация полностью проверенного результата |
| POST `/results/{uuid}/void/` | Admin | `reason`, аннулирование и дополнительная попытка |
| GET `/manage/export/` | Super / Admin с разрешением | CSV опубликованных результатов; событие аудита |

Пример сохранения:

```json
{"index":0,"revision":3,"responses":{"l1":"Riverside","l4":["Headphones","Laptops"]},"submit":false}
```

Успех: `{"saved":true,"revision":4,"closed":false,"index":0,"server_now":"…"}`. Устаревшая версия — HTTP 409 и `conflict:true`. Чужая попытка — 404, запрещённое действие — 403, неверные данные — 400. Поздний ответ — `closed:true` без `saved`; он **не** принят. Это позволяет зафиксировать истечение срока в той же транзакции, не откатывая переход секции.

Полный набор полей экзамена хранится только на сервере. Ученическая страница получает явно разрешённые поля вопросов; ключи, шкалы и непросмотренные секции не сериализуются. Максимум файла — 25 МБ; короткого ответа — 2000 символов; эссе — 30000. Авторизационные токены нельзя включать в аудит, отчёты или access logs reverse proxy.
