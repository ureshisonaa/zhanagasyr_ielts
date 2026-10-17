import copy
import io
import json
import tempfile
import wave
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import User, Exam, TestVersion, Assignment, Attempt, SectionRun, MediaAsset, Review, AuditLog, StudyGroup
from .exams import auto_score, half_band, manual_band, validate_content
from .services import start_attempt, save_answers, SaveConflict, refresh_attempt, grade_run, publish_result, publish_version
from .media import inspect_upload
from .auth_views import activation_link
from .management.commands.seed_demo import sample_content


class PlatformTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user('admin', 'admin@example.com', 'strong-test-password-881', role='super', first_name='Администратор')
        cls.manager = User.objects.create_user('manager', 'manager@example.com', 'strong-test-password-881', role='admin')
        cls.teacher = User.objects.create_user('teacher', 'teacher@example.com', 'strong-test-password-881', role='teacher')
        cls.other_teacher = User.objects.create_user('otherteacher', 'otherteacher@example.com', 'strong-test-password-881', role='teacher')
        cls.student = User.objects.create_user('student', 'student@example.com', 'strong-test-password-881', role='student')
        cls.other = User.objects.create_user('other', 'other@example.com', 'strong-test-password-881', role='student')
        cls.exam = Exam.objects.create(title='Test Academic', author=cls.admin)
        cls.version = TestVersion.objects.create(exam=cls.exam, content=sample_content(), published_at=timezone.now())
        cls.assignment = Assignment.objects.create(version=cls.version, student=cls.student, teacher=cls.teacher, opens_at=timezone.now() - timedelta(days=1), due_at=timezone.now() + timedelta(days=1), attempts_allowed=2, assigned_by=cls.admin)

    def sign_in(self, user, client=None):
        client = client or self.client
        client.force_login(user)
        session = client.session
        session['session_version'] = user.session_version
        session.save()
        return client

    def completed_attempt(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        for i in range(4):
            responses = {'w1': 'First response', 'w2': 'Second response'} if i == 2 else {}
            save_answers(self.student, attempt.pk, i, 0, responses, True)
        attempt.refresh_from_db()
        return attempt

    def graded_attempt(self):
        attempt = self.completed_attempt()
        grade_run(self.teacher, attempt.runs.get(kind='writing').pk, {'task1': [6] * 4, 'task2': [7] * 4}, 'Writing feedback', True)
        grade_run(self.teacher, attempt.runs.get(kind='speaking').pk, {'speaking': [7] * 4}, 'Speaking feedback', True)
        attempt.refresh_from_db()
        return attempt

    def test_closed_auth_has_no_registration(self):
        self.assertEqual(self.client.get('/register/').status_code, 404)
        self.assertRedirects(self.client.get('/'), '/login/?next=/', fetch_redirect_response=False)

    def test_login_email_and_username_work(self):
        response = self.client.post('/login/', {'identifier': 'STUDENT@EXAMPLE.COM', 'password': 'strong-test-password-881'})
        self.assertRedirects(response, '/', fetch_redirect_response=False)
        self.assertEqual(self.client.get('/').status_code, 200)
        self.client.post('/logout/')
        response = self.client.post('/login/', {'identifier': 'student', 'password': 'strong-test-password-881'})
        self.assertEqual(response.status_code, 302)

    def test_login_rate_limit_blocks_even_correct_password(self):
        for _ in range(8):
            self.client.post('/login/', {'identifier': 'student', 'password': 'bad'})
        response = self.client.post('/login/', {'identifier': 'student', 'password': 'strong-test-password-881'})
        self.assertContains(response, 'Слишком много попыток')

    def test_logout_requires_post(self):
        self.sign_in(self.student)
        self.assertEqual(self.client.get('/logout/').status_code, 405)

    def test_block_and_unblock_revokes_old_session(self):
        student_client = self.sign_in(self.student, Client())
        self.sign_in(self.admin)
        self.client.post(reverse('user_action', args=[self.student.pk]), {'action': 'toggle'})
        self.assertEqual(student_client.get('/').status_code, 302)
        self.client.post(reverse('user_action', args=[self.student.pk]), {'action': 'toggle'})
        self.assertEqual(student_client.get('/').status_code, 302)

    def test_student_cannot_access_admin_routes(self):
        self.sign_in(self.student)
        for path in ['/manage/users/', '/manage/exams/', '/manage/assignments/', '/manage/audit/', '/manage/export/', '/groups/', '/reviews/']:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_admin_cannot_promote_self_or_create_super(self):
        self.sign_in(self.manager)
        self.assertEqual(self.client.get(reverse('user_edit', args=[self.manager.pk])).status_code, 403)
        response = self.client.post('/manage/users/new/', {'username': 'evil', 'email': 'evil@example.com', 'role': 'super'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='evil').exists())

    def test_account_creation_and_one_time_activation(self):
        self.sign_in(self.admin)
        response = self.client.post('/manage/users/new/', {'username': 'newstudent', 'email': 'new@example.com', 'first_name': 'New', 'last_name': 'Student', 'role': 'student'})
        self.assertContains(response, 'Персональная ссылка')
        user = User.objects.get(username='newstudent')
        self.assertFalse(user.has_usable_password())
        url = activation_link(user).replace('http://127.0.0.1:8000', '')
        client = Client()
        response = client.post(url, {'new_password1': 'A-long-new-secret-987', 'new_password2': 'A-long-new-secret-987'})
        self.assertEqual(response.status_code, 302)
        self.assertContains(client.get(url), 'Ссылка недействительна')

    def test_expired_activation(self):
        with override_settings(PASSWORD_RESET_TIMEOUT=-1):
            url = activation_link(self.student).replace('http://127.0.0.1:8000', '')
            self.assertContains(self.client.get(url), 'Ссылка недействительна')

    def test_reissued_invitation_immediately_invalidates_previous_link(self):
        old_url = activation_link(self.student).replace('http://127.0.0.1:8000', '')
        self.sign_in(self.admin)
        self.client.post(reverse('user_action', args=[self.student.pk]), {'action': 'invite'})
        self.assertContains(Client().get(old_url), 'Ссылка недействительна')

    def test_password_reset_does_not_enumerate_accounts(self):
        one = self.client.post('/forgot-password/', {'email': 'student@example.com'})
        two = self.client.post('/forgot-password/', {'email': 'missing@example.com'})
        self.assertEqual(one.status_code, two.status_code)
        self.assertEqual(one.context['sent'], two.context['sent'])

    def test_csrf_protects_admin_mutations(self):
        client = self.sign_in(self.admin, Client(enforce_csrf_checks=True))
        self.assertEqual(client.post(reverse('user_action', args=[self.student.pk]), {'action': 'toggle'}).status_code, 403)

    def test_attempt_start_idempotent_and_limit(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        self.assertEqual(start_attempt(self.student, self.assignment.pk).pk, attempt.pk)
        self.assertEqual(Attempt.objects.count(), 1)
        self.completed_attempt()
        self.completed_attempt()
        with self.assertRaises(ValidationError):
            start_attempt(self.student, self.assignment.pk)

    def test_assignment_availability_enforced(self):
        self.assignment.opens_at = timezone.now() + timedelta(hours=1)
        self.assignment.save()
        with self.assertRaises(ValidationError):
            start_attempt(self.student, self.assignment.pk)

    def test_other_student_cannot_access_attempt(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        self.sign_in(self.other)
        for name in ['attempt', 'attempt_state', 'result_detail']:
            self.assertEqual(self.client.get(reverse(name, args=[attempt.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('save_answers', args=[attempt.pk]), json.dumps({'index': 0, 'revision': 0, 'responses': {}}), content_type='application/json').status_code, 404)

    def test_student_payload_never_exposes_keys_or_band_tables(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        self.sign_in(self.student)
        response = self.client.get(reverse('attempt', args=[attempt.pk]))
        self.assertEqual(response.status_code, 200)
        for hidden in ['Riverside Learning Centre', '"answers"', '"bands"', 'case_sensitive']:
            self.assertNotContains(response, hidden)

    def test_answers_survive_refresh_and_timer_does_not_reset(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        deadline = attempt.runs.get(index=0).deadline
        save_answers(self.student, attempt.pk, 0, 0, {'l1': 'Riverside'})
        self.sign_in(self.student)
        response = self.client.get(reverse('attempt', args=[attempt.pk]))
        self.assertContains(response, 'Riverside')
        self.assertEqual(attempt.runs.get(index=0).deadline, deadline)

    def test_conflicting_revision_cannot_overwrite(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        save_answers(self.student, attempt.pk, 0, 0, {'l1': 'first'})
        with self.assertRaises(SaveConflict):
            save_answers(self.student, attempt.pk, 0, 0, {'l1': 'second'})
        self.assertEqual(attempt.runs.get(index=0).responses['l1'], 'first')

    def test_expiry_discards_late_answers_and_starts_next_at_original_deadline(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        run = attempt.runs.get(index=0)
        run.deadline = timezone.now() - timedelta(seconds=10)
        run.save()
        data = save_answers(self.student, attempt.pk, 0, 0, {'l1': 'late'})
        self.assertTrue(data['closed'])
        run.refresh_from_db()
        self.assertEqual(run.responses, {})
        self.assertEqual(attempt.runs.get(index=1).started_at, run.deadline)

    def test_background_expiry_finishes_all_sections_after_due_time(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        self.assignment.due_at = timezone.now() - timedelta(seconds=1)
        self.assignment.save()
        run = attempt.runs.first()
        run.deadline = self.assignment.due_at
        run.save()
        call_command('expire_attempts', stdout=io.StringIO())
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'review')
        self.assertEqual(attempt.runs.count(), 4)
        self.assertFalse(attempt.runs.filter(submitted_at__isnull=True).exists())

    def test_cannot_skip_sections_or_edit_submitted_answers(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        self.assertTrue(save_answers(self.student, attempt.pk, 2, 0, {'w1': 'skip'})['closed'])
        save_answers(self.student, attempt.pk, 0, 0, {'l1': 'original'}, True)
        self.assertTrue(save_answers(self.student, attempt.pk, 0, 1, {'l1': 'changed'})['closed'])
        self.assertEqual(attempt.runs.get(index=0).responses['l1'], 'original')

    def test_rejects_foreign_question_and_wrong_answer_type(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        for responses in [{'r1': 'wrong section'}, {'l1': ['list']}, {'l4': 'string'}, {'l2': 'not an option'}]:
            with self.subTest(responses=responses), self.assertRaises(ValidationError):
                save_answers(self.student, attempt.pk, 0, 0, responses)

    def test_scoring_normalization_multiselect_and_word_limit(self):
        section = sample_content()['sections'][0]
        raw, band = auto_score(section, {'l1': '  RIVERSIDE  ', 'l2': 'Thursday', 'l3': '6', 'l4': ['Laptops', 'Headphones']})
        self.assertEqual((raw, band), (4, Decimal('9')))
        self.assertEqual(auto_score(section, {'l4': ['Laptops']})[0], 0)
        section['questions'][0]['max_words'] = 1
        self.assertEqual(auto_score(section, {'l1': 'Riverside Learning Centre'})[0], 0)
        self.assertEqual(auto_score(section, {})[0], 0)

    def test_ielts_rounding_and_task2_weight(self):
        self.assertEqual(half_band(6.25), Decimal('6.5'))
        self.assertEqual(half_band(6.75), Decimal('7'))
        self.assertEqual(half_band(6.125), Decimal('6'))
        self.assertEqual(manual_band('writing', {'task1': [3] * 4, 'task2': [9] * 4}), Decimal('7'))
        self.assertEqual(manual_band('speaking', {'speaking': [6, 6.5, 7, 7.5]}), Decimal('7'))
        for bad in ['NaN', 9.5, -1, 6.1]:
            with self.assertRaises(ValidationError):
                manual_band('speaking', {'speaking': [bad] * 4})

    def test_immutable_published_version_and_clone(self):
        version = TestVersion.objects.get(pk=self.version.pk)
        version.content['sections'][0]['duration'] = 60
        with self.assertRaises(ValidationError):
            version.save()
        self.sign_in(self.admin)
        self.client.post(reverse('version_edit', args=[version.pk]), {'action': 'clone'})
        cloned = self.exam.versions.get(number=2)
        self.assertIsNone(cloned.published_at)
        self.assertEqual(cloned.content['sections'][0]['duration'], 1800)
        self.assertEqual(self.assignment.version_id, self.version.pk)

    def test_publication_validates_content(self):
        content = sample_content()
        content['sections'][2]['questions'].pop()
        with self.assertRaises(ValidationError):
            validate_content(content)
        content = sample_content()
        content['sections'][0]['bands'] = [[0, 0], [5, 9]]
        with self.assertRaises(ValidationError):
            validate_content(content)
        draft = TestVersion.objects.create(exam=self.exam, number=2, content=sample_content())
        with self.assertRaises(ValidationError):
            publish_version(self.admin, draft.pk)

    def test_teacher_scoping_and_student_hidden_results(self):
        attempt = self.graded_attempt()
        run = attempt.runs.get(kind='writing')
        self.sign_in(self.other_teacher)
        self.assertEqual(self.client.get(reverse('review_detail', args=[run.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('result_detail', args=[attempt.pk])).status_code, 404)
        self.sign_in(self.student)
        self.assertNotContains(self.client.get(reverse('result_detail', args=[attempt.pk])), 'Writing feedback')
        publish_result(self.admin, attempt.pk)
        self.assertContains(self.client.get(reverse('result_detail', args=[attempt.pk])), 'Writing feedback')

    def test_teacher_cannot_publish_or_modify_published_grade(self):
        attempt = self.graded_attempt()
        with self.assertRaises(PermissionDenied):
            publish_result(self.teacher, attempt.pk)
        publish_result(self.admin, attempt.pk)
        with self.assertRaises(PermissionDenied):
            grade_run(self.teacher, attempt.runs.get(kind='writing').pk, {'task1': [8] * 4, 'task2': [8] * 4}, 'unauthorized', True)

    def test_draft_grade_and_authorized_revision_require_republication(self):
        attempt = self.graded_attempt()
        publish_result(self.admin, attempt.pk)
        run = attempt.runs.get(kind='speaking')
        grade_run(self.admin, run.pk, {'speaking': [8] * 4}, 'Edited', False)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, 'review')
        self.assertIsNone(attempt.overall)
        self.assertIsNone(attempt.published_at)
        self.assertTrue(AuditLog.objects.filter(action='grade.draft', target=str(run.pk)).exists())

    def test_cannot_publish_incomplete_result(self):
        attempt = self.completed_attempt()
        with self.assertRaises(ValidationError):
            publish_result(self.admin, attempt.pk)

    def test_end_to_end_student_teacher_admin_cycle(self):
        self.sign_in(self.student)
        response = self.client.post(reverse('instructions', args=[self.assignment.pk]), {'ready': 'yes'})
        self.assertEqual(response.status_code, 302)
        attempt = Attempt.objects.get()
        for index, answers in enumerate([{'l1': 'Riverside', 'l2': 'Thursday', 'l3': 'six', 'l4': ['Headphones', 'Laptops']}, {'r1': 'False', 'r2': 'Not Given', 'r3': 'social connection', 'r4': 'rainwater tanks'}, {'w1': 'An original description.', 'w2': 'An original essay.'}, {}]):
            self.assertEqual(self.client.get(reverse('attempt', args=[attempt.pk])).status_code, 200)
            response = self.client.post(reverse('save_answers', args=[attempt.pk]), json.dumps({'index': index, 'revision': 0, 'responses': answers, 'submit': True}), content_type='application/json')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['saved'])
        self.sign_in(self.teacher)
        writing = attempt.runs.get(kind='writing')
        speaking = attempt.runs.get(kind='speaking')
        data = {f'{key}_{i}': '7' for key in ['task1', 'task2'] for i in range(4)}
        self.assertEqual(self.client.post(reverse('review_detail', args=[writing.pk]), {**data, 'feedback': 'Good work', 'action': 'final'}).status_code, 302)
        self.assertEqual(self.client.post(reverse('review_detail', args=[speaking.pk]), {**{f'speaking_{i}': '7' for i in range(4)}, 'feedback': 'Keep practising', 'action': 'final'}).status_code, 302)
        self.sign_in(self.admin)
        self.assertEqual(self.client.post(reverse('publish_result', args=[attempt.pk])).status_code, 302)
        self.sign_in(self.student)
        response = self.client.get(reverse('result_detail', args=[attempt.pk]))
        self.assertContains(response, 'Good work')
        attempt.refresh_from_db()
        self.assertEqual(attempt.overall, Decimal('8'))

    def test_export_permissions_and_csv_formula_escaping(self):
        attempt = self.graded_attempt()
        publish_result(self.admin, attempt.pk)
        self.exam.title = '=HYPERLINK("unsafe")'
        self.exam.save()
        self.sign_in(self.manager)
        self.assertEqual(self.client.get('/manage/export/').status_code, 403)
        self.assertEqual(self.client.get('/manage/audit/').status_code, 403)
        self.sign_in(self.admin)
        response = self.client.get('/manage/export/')
        self.assertEqual(response.status_code, 200)
        self.assertIn("'=HYPERLINK", response.content.decode())
        self.assertTrue(AuditLog.objects.filter(action='results.exported').exists())

    def test_all_role_pages_render(self):
        attempt = self.graded_attempt()
        pages = {self.admin: ['/', '/manage/users/', '/manage/users/new/', '/groups/', '/groups/new/', '/manage/exams/', '/manage/exams/new/', f'/manage/versions/{self.version.pk}/', '/manage/assignments/', '/manage/assignments/new/', '/reviews/', '/results/', '/manage/audit/', '/profile/'], self.teacher: ['/', '/groups/', '/reviews/', '/results/', '/profile/'], self.student: ['/', '/exams/', '/results/', '/profile/']}
        for user, paths in pages.items():
            self.sign_in(user)
            for path in paths:
                with self.subTest(role=user.role, path=path):
                    self.assertEqual(self.client.get(path).status_code, 200)
        self.sign_in(self.teacher)
        for run in attempt.runs.filter(kind__in=['writing', 'speaking']):
            self.assertEqual(self.client.get(reverse('review_detail', args=[run.pk])).status_code, 200)

    def test_group_assignment_is_expanded_idempotently(self):
        group = StudyGroup.objects.create(name='Test Group', teacher=self.teacher)
        group.students.add(self.student, self.other)
        self.sign_in(self.admin)
        data = {'version': self.version.pk, 'group': group.pk, 'teacher': self.teacher.pk, 'opens_at': '2020-01-01T10:00', 'due_at': '2035-01-01T10:00', 'attempts_allowed': 2}
        for _ in range(2):
            self.assertEqual(self.client.post('/manage/assignments/new/', data).status_code, 302)
        self.assertEqual(Assignment.objects.count(), 2)
        self.assertEqual(Assignment.objects.get(student=self.other).teacher_id, self.teacher.pk)

    def test_private_audio_upload_and_range_authorization(self):
        with tempfile.TemporaryDirectory() as folder, override_settings(MEDIA_ROOT=Path(folder)):
            audio = io.BytesIO()
            with wave.open(audio, 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(b'\x00\x00' * 8000)
            data = audio.getvalue()
            file = SimpleUploadedFile('answer.wav', data, 'audio/wav')
            self.assertEqual(inspect_upload(file, audio_only=True), 'audio/wav')
            attempt = start_attempt(self.student, self.assignment.pk)
            for index in range(3):
                save_answers(self.student, attempt.pk, index, 0, {}, True)
            self.sign_in(self.student)
            response = self.client.post(reverse('recording_upload', args=[attempt.pk, 's1']), {'file': file})
            self.assertEqual(response.status_code, 200)
            url = response.json()['url']
            response = self.client.get(url, HTTP_RANGE='bytes=0-19')
            self.assertEqual(response.status_code, 206)
            self.assertEqual(b''.join(response.streaming_content), data[:20])
            self.sign_in(self.other)
            self.assertEqual(self.client.get(url).status_code, 403)
            self.sign_in(self.teacher)
            self.assertEqual(self.client.get(url).status_code, 403)
            save_answers(self.student, attempt.pk, 3, 0, {}, True)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()
            self.sign_in(self.other_teacher)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_rejects_disguised_and_non_audio_files(self):
        for name, data in [('attack.wav', b'<script>bad</script>'), ('image.svg', b'<svg></svg>'), ('answer.exe', b'MZ')]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                inspect_upload(SimpleUploadedFile(name, data))

    def test_single_listening_playback_claim(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        self.sign_in(self.student)
        self.assertEqual(self.client.post(reverse('listening_start', args=[attempt.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('listening_start', args=[attempt.pk])).status_code, 409)

    def test_void_is_audited_and_repeat_does_not_add_extra_attempts(self):
        attempt = start_attempt(self.student, self.assignment.pk)
        self.sign_in(self.admin)
        for _ in range(2):
            self.client.post(reverse('void_attempt', args=[attempt.pk]), {'reason': 'Audio device failure'})
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.attempts_allowed, 3)
        self.assertEqual(AuditLog.objects.filter(action='attempt.voided').count(), 1)

    def test_xss_in_question_is_escaped(self):
        draft = TestVersion.objects.create(exam=self.exam, number=2, content=sample_content())
        draft.content['sections'][0]['questions'][0]['prompt'] = '<script>alert(1)</script>'
        draft.published_at = timezone.now()
        draft.save()
        assignment = Assignment.objects.create(version=draft, student=self.student, teacher=self.teacher, due_at=timezone.now() + timedelta(days=1), assigned_by=self.admin)
        attempt = start_attempt(self.student, assignment.pk)
        self.sign_in(self.student)
        response = self.client.get(reverse('attempt', args=[attempt.pk]))
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')

    def test_unordered_pair_scores_distinct_answers_only(self):
        section = {'bands': [[0, 0], [1, 5], [2, 9]], 'questions': [
            {'id': 'a', 'type': 'choice', 'answers': ['B', 'D'], 'answer_group': 'pair'},
            {'id': 'b', 'type': 'choice', 'answers': ['B', 'D'], 'answer_group': 'pair'},
        ]}
        self.assertEqual(auto_score(section, {'a': 'D', 'b': 'B'})[0], 2)
        self.assertEqual(auto_score(section, {'a': 'B', 'b': 'B'})[0], 1)
        self.assertEqual(auto_score(section, {'a': 'A', 'b': 'D'})[0], 1)

    def test_source_pages_require_current_section_and_owner(self):
        with tempfile.TemporaryDirectory() as directory, self.settings(MEDIA_ROOT=directory):
            asset = MediaAsset.objects.create(file=SimpleUploadedFile('page.jpg', b'page'), name='Page', content_type='image/jpeg', owner=self.admin, version=self.version)
            content = copy.deepcopy(self.version.content)
            content['sections'][0]['materials'] = [{'id': str(asset.pk), 'title': 'Source page'}]
            TestVersion.objects.filter(pk=self.version.pk).update(content=content)
            attempt = start_attempt(self.student, self.assignment.pk)
            self.sign_in(self.student)
            self.assertEqual(self.client.get(reverse('private_media', args=[asset.pk])).status_code, 200)
            page = self.client.get(reverse('attempt', args=[attempt.pk]))
            self.assertContains(page, 'Source page')
            self.assertNotContains(page, '"answers"')
            self.sign_in(self.other)
            self.assertEqual(self.client.get(reverse('private_media', args=[asset.pk])).status_code, 403)
            save_answers(self.student, attempt.pk, 0, 0, {}, True)
            self.sign_in(self.student)
            self.assertEqual(self.client.get(reverse('private_media', args=[asset.pk])).status_code, 403)
