"""Versioned exam contract. Only this module interprets answer keys."""
import math
import re
from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError

KINDS = ['listening', 'reading', 'writing', 'speaking']
LABELS = dict(zip(KINDS, ['Listening', 'Reading', 'Writing', 'Speaking']))
QUESTION_TYPES = [('short', 'Короткий ответ / заполнение'), ('choice', 'Один вариант'), ('multi', 'Несколько вариантов'), ('tfng', 'True / False / Not Given'), ('ynng', 'Yes / No / Not Given'), ('matching', 'Сопоставление'), ('essay', 'Письменная работа'), ('audio', 'Устный ответ')]
WRITING_CRITERIA = ['Task Achievement / Response', 'Coherence and Cohesion', 'Lexical Resource', 'Grammatical Range and Accuracy']
SPEAKING_CRITERIA = ['Fluency and Coherence', 'Lexical Resource', 'Grammatical Range and Accuracy', 'Pronunciation']
DEFAULT_BANDS = [[0, 0], [1, 1], [4, 2], [6, 3], [10, 4], [13, 4.5], [16, 5], [20, 5.5], [23, 6], [27, 6.5], [30, 7], [33, 7.5], [35, 8], [37, 8.5], [39, 9]]


def empty_content():
    return {'sections': [{'kind': kind, 'duration': duration, 'instructions': '', 'passage': '', 'audio_id': '', 'replay': False, 'questions': [], 'bands': DEFAULT_BANDS if kind in KINDS[:2] else []} for kind, duration in zip(KINDS, [1800, 3600, 3600, 840])]}


def validate_content(content):
    try:
        sections = content['sections']
        if len(sections) != 4 or [s['kind'] for s in sections] != KINDS:
            raise ValueError('Нужны четыре секции в порядке Listening, Reading, Writing, Speaking.')
        ids = set()
        for section in sections:
            if type(section['duration']) is not int or not 60 <= section['duration'] <= 10800:
                raise ValueError('Продолжительность секции: от 60 до 10 800 секунд.')
            questions = section['questions']
            if not 1 <= len(questions) <= 100:
                raise ValueError('В каждой секции должно быть от 1 до 100 вопросов.')
            if section['kind'] == 'writing' and len(questions) != 2:
                raise ValueError('Writing должен содержать ровно два задания: Task 1 и Task 2.')
            if section['kind'] == 'speaking' and len(questions) != 3:
                raise ValueError('Speaking должен содержать ровно три части.')
            for q in questions:
                if not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}', q['id']) or q['id'] in ids:
                    raise ValueError('Идентификаторы вопросов должны быть уникальными.')
                ids.add(q['id'])
                if not q['prompt'].strip() or q['type'] not in dict(QUESTION_TYPES):
                    raise ValueError('Проверьте текст и тип вопроса.')
                allowed = ['essay'] if section['kind'] == 'writing' else ['audio'] if section['kind'] == 'speaking' else ['short', 'choice', 'multi', 'tfng', 'ynng', 'matching']
                if q['type'] not in allowed:
                    raise ValueError('Тип вопроса не соответствует секции.')
                if section['kind'] in KINDS[:2]:
                    if not isinstance(q.get('answers'), list) or not q['answers'] or any(not isinstance(a, str) or not a.strip() for a in q['answers']):
                        raise ValueError('Для каждого автоматического вопроса нужен ключ ответа.')
                    if q['type'] in ('choice', 'multi', 'matching', 'tfng', 'ynng'):
                        options = q.get('options', [])
                        if not 2 <= len(options) <= 30 or len(set(options)) != len(options) or any(a not in options for a in q['answers']):
                            raise ValueError('Ключи должны совпадать с вариантами ответа, варианты — быть уникальными.')
                    if type(q.get('max_words', 0)) is not int or not 0 <= q.get('max_words', 0) <= 100:
                        raise ValueError('Некорректное ограничение слов.')
            if section['kind'] in KINDS[:2]:
                bands = section['bands']
                if not bands or bands[0] != [0, 0]:
                    raise ValueError('Таблица перевода баллов должна начинаться с [0, 0].')
                previous_raw, previous_band = -1, -1
                for raw, band in bands:
                    if type(raw) is not int or raw <= previous_raw or not 0 <= raw <= len(questions) or not isinstance(band, (int, float)) or not math.isfinite(band) or not 0 <= band <= 9 or band * 2 != int(band * 2) or band < previous_band:
                        raise ValueError('Таблица баллов: возрастающие пороги в пределах числа вопросов, band от 0 до 9 с шагом 0.5.')
                    previous_raw, previous_band = raw, band
        return content
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc


def normalized(value, case_sensitive=False):
    value = ' '.join(str(value).strip().split())
    return value if case_sensitive else value.casefold()


def auto_score(section, responses):
    raw = 0
    consumed = {}
    for q in section['questions']:
        answer = responses.get(q['id'], '')
        norm = lambda x: normalized(x, q.get('case_sensitive', False))
        if q['type'] == 'multi':
            correct = isinstance(answer, list) and bool(answer) and {norm(x) for x in answer} == {norm(x) for x in q['answers']}
        else:
            correct = isinstance(answer, str) and bool(norm(answer)) and norm(answer) in {norm(x) for x in q['answers']}
            if q.get('max_words') and len(answer.split()) > q['max_words']:
                correct = False
        if correct and q.get("answer_group"):
            used = consumed.setdefault(q["answer_group"], set())
            value = norm(answer)
            correct = value not in used
            used.add(value)
        raw += int(correct)
    band = max((Decimal(str(b)) for minimum, b in section['bands'] if raw >= minimum), default=Decimal('0'))
    return raw, band


def half_band(value):
    return (Decimal(str(value)) * 2).quantize(Decimal('1'), rounding=ROUND_HALF_UP) / 2


def manual_band(kind, criteria):
    expected = ['task1', 'task2'] if kind == 'writing' else ['speaking']
    if set(criteria) != set(expected):
        raise ValidationError('Заполните все группы критериев.')
    averages = []
    for key in expected:
        values = criteria[key]
        if not isinstance(values, list) or len(values) != 4:
            raise ValidationError('Нужны четыре оценки по критериям.')
        vals = []
        for v in values:
            try:
                d = Decimal(str(v))
                if not d.is_finite() or not 0 <= d <= 9 or d * 2 != int(d * 2):
                    raise ValueError()
                vals.append(d)
            except Exception as exc:
                raise ValidationError('Оценки должны быть от 0 до 9 с шагом 0.5.') from exc
        averages.append(sum(vals) / 4)
    return half_band((averages[0] + 2 * averages[1]) / 3 if kind == 'writing' else averages[0])
