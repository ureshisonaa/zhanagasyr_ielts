"""Build escaped presentation fragments from trusted question references, never answer keys."""
import re

TOKEN = re.compile(r'\[\[([a-zA-Z0-9_-]{1,60})\]\]')


def question_groups(section, questions):
    lookup = {q['id']: q for q in questions}
    groups = []
    specs = section.get('groups')
    if not specs:
        specs = [{'id': f'part-{part}', 'part': part, 'question_ids': [q['id'] for q in questions if str(q.get('part') or '1') == part]}
                 for part in dict.fromkeys(str(q.get('part') or '1') for q in questions)]
    for spec in specs:
        def fragments(text):
            result, pos = [], 0
            for match in TOKEN.finditer(text):
                result.append({'text': text[pos:match.start()]})
                result.append({'question': lookup[match[1]]})
                pos = match.end()
            result.append({'text': text[pos:]})
            return result
        group = {k: spec.get(k, '') for k in ('id', 'part', 'title', 'instructions', 'layout', 'choose_count')}
        group['questions'] = [lookup[i] for i in spec.get('question_ids', [])]
        group['paragraphs'] = [fragments(p) for p in spec.get('paragraphs', [])]
        group['headers'] = spec.get('headers', [])
        group['rows'] = [[fragments(cell) for cell in row] for row in spec.get('rows', [])]
        refs = spec.get('question_ids', []) + [m[1] for text in spec.get('paragraphs', []) + [c for row in spec.get('rows', []) for c in row] for m in TOKEN.finditer(text)]
        bank_question = next((lookup[i] for i in refs if lookup[i]['type'] == 'matching'), None)
        group['bank'] = bank_question['choices'] if bank_question else []
        group['pair_choices'] = group['questions'][0]['choices'] if group.get('choose_count') else []
        for i in refs:
            lookup[i]['group_id'] = spec['id']
        groups.append(group)
    return groups
