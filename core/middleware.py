from django.contrib.auth import logout


class SessionVersionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.session.get('session_version') != request.user.session_version:
            logout(request)
        response = self.get_response(request)
        if request.path != '/static/' and not request.path.startswith('/static/'):
            response['Cache-Control'] = 'no-store, private'
            response['Referrer-Policy'] = 'same-origin'
        return response
