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


class LOApplicationSettingsDraftForm(forms.Form):
    applications_open = forms.TypedChoiceField(
        choices=(("", "Select"), ("yes", "Yes"), ("no", "No")),
        coerce=lambda value: {"yes": True, "no": False}[value],
        empty_value=None, required=True,
    )
    automatic_acceptance = forms.TypedChoiceField(
        choices=(("", "Select"), ("yes", "Yes"), ("no", "No")),
        coerce=lambda value: {"yes": True, "no": False}[value],
        empty_value=None, required=True,
    )
    processing_responsibility = forms.ChoiceField(
        choices=(("", "Select"), ("LO", "Living organism"), ("APPLICATION_CIRCLE", "Application circle")),
        required=True,
    )


class LOApplicationQuestionDraftForm(forms.Form):
    question_text = forms.CharField(max_length=255)
    answer_format = forms.ChoiceField(
        choices=(("", "Select"), ("SHORT_ANSWER", "Short answer"), ("LONG_ANSWER", "Long answer"),
                 ("YES_NO", "Yes / No"), ("MULTIPLE_CHOICE", "Multiple choice"),
                 ("SCALE_COMPLEX", "Scale")),
        required=True,
    )

    def __init__(self, *args, living_organism=None, name_source=None, **kwargs):
        if living_organism is not None and not args and "data" not in kwargs and "initial" not in kwargs:
            from src.intevia.services.organism_application_service import default_application_question

            kwargs["initial"] = default_application_question(living_organism, name_source)
        super().__init__(*args, **kwargs)