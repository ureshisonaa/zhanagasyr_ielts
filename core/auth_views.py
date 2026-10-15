import hashlib
from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.forms import SetPasswordForm, PasswordChangeForm
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.views.decorators.http import require_POST
from .forms import LoginForm
from .models import User, LoginThrottle
from .services import audit


def activation_link(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return f'{settings.SITE_URL}/activate-account/{uid}/{default_token_generator.make_token(user)}/'


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = LoginForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        identifier = form.cleaned_data['identifier'].strip().lower()
        keys = [hashlib.sha256(x.encode()).hexdigest() for x in ['user:' + identifier, 'ip:' + request.META.get('REMOTE_ADDR', '')]]
        with transaction.atomic():
            throttles = [LoginThrottle.objects.select_for_update().get_or_create(key=k)[0] for k in keys]
            now = timezone.now()
            if any(t.failures >= 8 and t.until > now for t in throttles):
                form.add_error(None, 'Слишком много попыток. Повторите вход через 15 минут.')
            else:
                account = User.objects.filter(email__iexact=identifier).first() if '@' in identifier else User.objects.filter(username__iexact=identifier).first()
                user = authenticate(request, username=account.username if account else identifier, password=form.cleaned_data['password'])
                if user:
                    LoginThrottle.objects.filter(key__in=keys).delete()
                    login(request, user)
                    request.session['session_version'] = user.session_version
                    audit(user, 'account.login', user.pk)
                    return redirect('dashboard')
                for throttle in throttles:
                    if throttle.until <= now:
                        throttle.failures = 0
                    throttle.failures += 1
                    throttle.until = now + timedelta(minutes=15)
                    throttle.save()
                form.add_error(None, 'Не удалось войти. Проверьте данные или обратитесь к администратору.')
    return render(request, 'core/login.html', {'form': form})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')


def activate(request, uid, token):
    try:
        user = User.objects.get(pk=urlsafe_base64_decode(uid).decode(), is_active=True)
    except (User.DoesNotExist, ValueError, UnicodeDecodeError, OverflowError):
        user = None
    valid = user is not None and default_token_generator.check_token(user, token)
    form = SetPasswordForm(user, request.POST or None) if valid else None
    if request.method == 'POST' and valid and form.is_valid():
        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=user.pk)
            if not locked.is_active or not default_token_generator.check_token(locked, token):
                return render(request, 'core/activate.html', {'valid': False})
            locked.set_password(form.cleaned_data['new_password1'])
            locked.session_version += 1
            locked.save()
            audit(locked, 'account.activated_or_reset', locked.pk)
        messages.success(request, 'Пароль установлен. Теперь можно войти.')
        return redirect('login')
    return render(request, 'core/activate.html', {'form': form, 'valid': valid})


def forgot(request):
    if request.method == 'POST':
        email = request.POST.get('email', '')[:254].strip()
        # Same response for all addresses; throttle both existence and sending.
        key = hashlib.sha256(('reset:' + request.META.get('REMOTE_ADDR', '')).encode()).hexdigest()
        with transaction.atomic():
            limiter, _ = LoginThrottle.objects.select_for_update().get_or_create(key=key)
            now = timezone.now()
            if limiter.until <= now:
                limiter.failures = 0
            allowed = limiter.failures < 5
            limiter.failures += 1
            limiter.until = now + timedelta(minutes=15)
            limiter.save()
        user = User.objects.filter(email__iexact=email, is_active=True).first() if allowed else None
        if user and settings.EMAIL_HOST:
            try:
                send_mail('Восстановление доступа · ZhanaGasyr', 'Установите новый пароль по ссылке (действует 24 часа):\n' + activation_link(user), settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
            except Exception:
                audit(None, 'email.delivery_failed', 'password-reset')
        return render(request, 'core/forgot.html', {'sent': True})
    return render(request, 'core/forgot.html')


@login_required
def profile(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        user.session_version += 1
        user.save(update_fields=['session_version'])
        update_session_auth_hash(request, user)
        request.session['session_version'] = user.session_version
        audit(user, 'account.password_changed', user.pk)
        messages.success(request, 'Пароль изменён. Остальные сеансы завершены.')
        return redirect('profile')
    return render(request, 'core/form.html', {'form': form, 'title': 'Мой профиль', 'eyebrow': request.user.display_name, 'description': 'Смена пароля завершит другие активные сеансы.', 'submit': 'Изменить пароль'})
