from django.contrib.auth.tokens import PasswordResetTokenGenerator


class AccountTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        return super()._make_hash_value(user, timestamp) + str(user.session_version)


account_tokens = AccountTokenGenerator()
