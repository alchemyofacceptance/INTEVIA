from django.contrib import admin
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from core.care_identity import CARE_ID, RESERVED_NAME, reserved_name
from core.models import Identity


class StaffLoginForm(AdminAuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if Identity.objects.filter(identity_id=CARE_ID, credential=user).exists():
            raise self.get_invalid_login_error()


class AddUserForm(AdminUserCreationForm):
    def clean_username(self):
        if reserved_name(self.cleaned_data["username"]):
            raise ValidationError(RESERVED_NAME, code="reserved")
        return super().clean_username()


class EditUserForm(UserChangeForm):
    def clean_username(self):
        name = self.cleaned_data["username"]
        if name != self.instance.username and reserved_name(name):
            raise ValidationError(RESERVED_NAME, code="reserved")
        return name


admin.site.login_form = StaffLoginForm
admin.site.unregister(User)


@admin.register(User)
class GovernedUserAdmin(UserAdmin):
    add_form = AddUserForm
    form = EditUserForm

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if obj is not None and Identity.objects.filter(identity_id=CARE_ID, credential=obj).exists():
            return (*fields, "username")
        return fields
