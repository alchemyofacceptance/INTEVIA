from django import forms
from django.contrib.auth.forms import AuthenticationForm


class ProductAuthenticationForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "Unable to sign in with those credentials.",
        "inactive": "Unable to sign in with those credentials.",
    }

    username = forms.CharField(
        label="Username",
        max_length=150,
        widget=forms.TextInput(
            attrs={"autocomplete": "username", "autofocus": True}
        ),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "current-password"}
        ),
    )